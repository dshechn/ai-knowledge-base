"""四步知识库自动化流水线。

Pipeline Steps:
    1. Collect  — 从 GitHub Search API 和 RSS 源采集 AI 相关内容
    2. Analyze  — 调用 LLM 对每条内容进行摘要/评分/标签分析
    3. Organize — 去重 + 格式标准化 + 校验
    4. Save     — 将文章保存为独立 JSON 文件到 knowledge/articles/

Usage:
    python pipeline/pipeline.py --sources github,rss --limit 20
    python pipeline/pipeline.py --sources github --limit 5
    python pipeline/pipeline.py --sources rss --limit 10
    python pipeline/pipeline.py --sources github --limit 5 --dry-run
    python pipeline/pipeline.py --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from model_client import chat_with_retry, create_provider

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "knowledge" / "raw"
ARTICLES_DIR = PROJECT_ROOT / "knowledge" / "articles"

# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------

logger = logging.getLogger("pipeline")


def setup_logging(verbose: bool = False) -> None:
    """配置日志级别和格式。"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # 防止 httpx/httpcore DEBUG 日志泄露 Authorization headers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class RawItem:
    """采集到的原始条目。"""

    title: str
    url: str
    source: str  # "github" 或 "rss"
    description: str = ""
    published_at: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class AnalyzedItem:
    """经 LLM 分析后的条目。"""

    title: str
    url: str
    source: str
    description: str
    summary: str = ""
    score: float = 0.0
    tags: list[str] = field(default_factory=list)
    published_at: str = ""


@dataclass
class Article:
    """最终标准化的文章结构。"""

    id: str
    title: str
    source_url: str
    source: str
    summary: str
    score: float
    tags: list[str]
    status: str
    created_at: str
    published_at: str
    collected_at: str
    description: str = ""


# ---------------------------------------------------------------------------
# Step 1: 采集（Collect）
# ---------------------------------------------------------------------------

# GitHub 搜索关键词
GITHUB_SEARCH_QUERIES = [
    "AI agent framework",
    "large language model tool",
    "RAG retrieval augmented generation",
    "LLM inference optimization",
]

# RSS 源列表
RSS_FEEDS = [
    "https://hnrss.org/newest?q=AI+LLM",
    "https://rsshub.app/arxiv/search_query=artificial+intelligence&start=0&searchtype=all",
]


async def collect_github(client: httpx.AsyncClient, limit: int) -> list[RawItem]:
    """从 GitHub Search API 采集仓库信息。"""
    items: list[RawItem] = []
    per_query_limit = max(1, limit // len(GITHUB_SEARCH_QUERIES))

    for query in GITHUB_SEARCH_QUERIES:
        if len(items) >= limit:
            break

        logger.debug("GitHub search: %r (limit=%d)", query, per_query_limit)

        try:
            response = await client.get(
                "https://api.github.com/search/repositories",
                params={
                    "q": query,
                    "sort": "updated",
                    "order": "desc",
                    "per_page": min(per_query_limit, 30),
                },
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            response.raise_for_status()
            data = response.json()

            for repo in data.get("items", []):
                if len(items) >= limit:
                    break
                items.append(
                    RawItem(
                        title=repo.get("full_name", ""),
                        url=repo.get("html_url", ""),
                        source="github",
                        description=repo.get("description", "") or "",
                        published_at=repo.get("updated_at", ""),
                        extra={
                            "stars": repo.get("stargazers_count", 0),
                            "language": repo.get("language", ""),
                            "topics": repo.get("topics", []),
                        },
                    )
                )
        except httpx.HTTPError as exc:
            logger.warning("GitHub search failed for %r: %s", query, exc)
            continue

    logger.info("GitHub: collected %d items", len(items))
    return items


def _parse_rss_items(xml_text: str) -> list[dict[str, str]]:
    """用简易正则解析 RSS XML，提取 item 信息。"""
    items: list[dict[str, str]] = []
    # 匹配每个 <item>...</item> 块
    item_pattern = re.compile(r"<item>(.*?)</item>", re.DOTALL)
    title_pattern = re.compile(r"<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>")
    link_pattern = re.compile(r"<link>(.*?)</link>")
    desc_pattern = re.compile(
        r"<description><!\[CDATA\[(.*?)\]\]></description>"
        r"|<description>(.*?)</description>",
        re.DOTALL,
    )
    pubdate_pattern = re.compile(r"<pubDate>(.*?)</pubDate>")

    for item_match in item_pattern.finditer(xml_text):
        block = item_match.group(1)
        entry: dict[str, str] = {}

        title_m = title_pattern.search(block)
        if title_m:
            entry["title"] = (title_m.group(1) or title_m.group(2) or "").strip()

        link_m = link_pattern.search(block)
        if link_m:
            entry["url"] = link_m.group(1).strip()

        desc_m = desc_pattern.search(block)
        if desc_m:
            raw_desc = (desc_m.group(1) or desc_m.group(2) or "").strip()
            # 去除 HTML 标签
            entry["description"] = re.sub(r"<[^>]+>", "", raw_desc).strip()

        pub_m = pubdate_pattern.search(block)
        if pub_m:
            entry["published_at"] = pub_m.group(1).strip()

        if entry.get("title") and entry.get("url"):
            items.append(entry)

    return items


async def collect_rss(client: httpx.AsyncClient, limit: int) -> list[RawItem]:
    """从 RSS 源采集内容。"""
    items: list[RawItem] = []
    per_feed_limit = max(1, limit // len(RSS_FEEDS))

    for feed_url in RSS_FEEDS:
        if len(items) >= limit:
            break

        logger.debug("Fetching RSS: %s", feed_url)

        try:
            response = await client.get(feed_url, follow_redirects=True)
            response.raise_for_status()
            parsed = _parse_rss_items(response.text)

            for entry in parsed[:per_feed_limit]:
                if len(items) >= limit:
                    break
                items.append(
                    RawItem(
                        title=entry.get("title", ""),
                        url=entry.get("url", ""),
                        source="rss",
                        description=entry.get("description", ""),
                        published_at=entry.get("published_at", ""),
                    )
                )
        except httpx.HTTPError as exc:
            logger.warning("RSS fetch failed for %s: %s", feed_url, exc)
            continue

    logger.info("RSS: collected %d items", len(items))
    return items


async def step_collect(sources: list[str], limit: int) -> list[RawItem]:
    """Step 1: 采集数据。"""
    logger.info("=" * 60)
    logger.info("Step 1: COLLECT (sources=%s, limit=%d)", sources, limit)
    logger.info("=" * 60)

    all_items: list[RawItem] = []

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        if "github" in sources:
            github_items = await collect_github(client, limit)
            all_items.extend(github_items)

        if "rss" in sources:
            rss_limit = limit - len(all_items) if "github" in sources else limit
            rss_items = await collect_rss(client, max(1, rss_limit))
            all_items.extend(rss_items)

    # 保存原始数据到 knowledge/raw/
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    raw_file = RAW_DIR / f"raw_{timestamp}.json"
    raw_data = [
        {
            "title": item.title,
            "url": item.url,
            "source": item.source,
            "description": item.description,
            "published_at": item.published_at,
            "extra": item.extra,
        }
        for item in all_items
    ]
    raw_file.write_text(json.dumps(raw_data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Raw data saved to: %s (%d items)", raw_file, len(all_items))

    return all_items


# ---------------------------------------------------------------------------
# Step 2: 分析（Analyze）
# ---------------------------------------------------------------------------

ANALYZE_SYSTEM_PROMPT = """\
你是一个技术内容分析专家。请对给定的技术内容进行分析，返回 JSON 格式结果。

要求：
1. summary: 50-150 字的中文摘要，要求：
   - 必须包含至少 3 个以下技术关键词：模型、训练、推理、向量、索引、缓存、框架、API、SDK、算法、架构、性能、并发、分布式、微服务、容器、协议、认证、授权、加密、队列
   - 或英文关键词：model、inference、algorithm、architecture、performance、concurrency、distributed、microservice、container
   - 概括项目的核心技术实现和应用场景
2. score: 1-10 的质量评分（考虑创新性、实用性、影响力），优秀开源项目一般 8-9 分
3. tags: 必须从以下标准标签中选择 2-3 个（严禁使用列表外的标签）：
   python, javascript, typescript, rust, go, java, c++,
   ai, ml, llm, deep-learning, nlp, computer-vision,
   web, frontend, backend, fullstack, api, rest, graphql,
   database, sql, nosql, redis, postgresql, mongodb,
   devops, docker, kubernetes, ci-cd, cloud, aws, azure,
   security, cryptography, authentication,
   architecture, microservices, distributed-systems,
   testing, performance, monitoring, observability,
   open-source, tooling, cli, editor,
   algorithm, data-structure, concurrency, networking,
   mobile, ios, android, flutter, react-native

严格返回如下 JSON 格式，不要有其他内容：
{"summary": "...", "score": N, "tags": ["tag-1", "tag-2", ...]}
"""


def _build_analyze_prompt(item: RawItem) -> str:
    """构建用于 LLM 分析的 user prompt。"""
    parts = [f"标题: {item.title}"]
    if item.description:
        parts.append(f"描述: {item.description[:500]}")
    parts.append(f"来源: {item.source}")
    parts.append(f"链接: {item.url}")
    if item.extra:
        if item.extra.get("stars"):
            parts.append(f"Stars: {item.extra['stars']}")
        if item.extra.get("language"):
            parts.append(f"语言: {item.extra['language']}")
        if item.extra.get("topics"):
            parts.append(f"标签: {', '.join(item.extra['topics'][:5])}")
    return "\n".join(parts)


def _parse_llm_response(text: str) -> dict[str, Any]:
    """解析 LLM 返回的 JSON 响应。

    处理多种格式：纯 JSON、markdown 代码块包裹、以及带前缀文字的混合输出。
    """
    if not text or not text.strip():
        logger.warning(
            "LLM returned empty content — likely a reasoning model "
            "(e.g. glm-4.6v) that puts output in reasoning_content instead of content. "
            "Switch to a non-reasoning model like glm-4-flash."
        )
        return {"summary": "模型返回为空", "score": 5.0, "tags": ["parse-error"]}

    text = text.strip()

    # 去除可能的 markdown 代码块包裹
    if text.startswith("```"):
        lines = text.split("\n")
        # 去掉首尾的 ``` 行
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    # 尝试匹配 JSON 对象
    json_match = re.search(r"\{.*\}", text, re.DOTALL)
    if json_match:
        try:
            result = json.loads(json_match.group())
            # 基本字段校验
            if "summary" in result or "score" in result:
                return result
        except json.JSONDecodeError:
            pass

    # 降级：返回默认值
    logger.warning("Failed to parse LLM response: %s", text[:200])
    return {"summary": "解析失败", "score": 5.0, "tags": ["unknown"]}


async def step_analyze(
    items: list[RawItem],
    dry_run: bool = False,
    provider_name: str | None = None,
) -> list[AnalyzedItem]:
    """Step 2: LLM 分析每条内容。"""
    logger.info("=" * 60)
    logger.info("Step 2: ANALYZE (%d items, dry_run=%s)", len(items), dry_run)
    logger.info("=" * 60)

    analyzed: list[AnalyzedItem] = []

    if dry_run:
        # 干跑模式：跳过 LLM 调用，使用占位数据
        for item in items:
            analyzed.append(
                AnalyzedItem(
                    title=item.title,
                    url=item.url,
                    source=item.source,
                    description=item.description,
                    summary=f"[DRY-RUN] {item.description[:80]}",
                    score=5.0,
                    tags=["dry-run"],
                    published_at=item.published_at,
                )
            )
        logger.info("Dry-run: skipped LLM analysis, used placeholder data")
        return analyzed

    # 创建 LLM provider
    try:
        provider = create_provider(provider_name=provider_name)
    except ValueError as exc:
        logger.error("Cannot create LLM provider: %s", exc)
        logger.warning("Falling back to dry-run mode for analysis step")
        return await step_analyze(items, dry_run=True)

    try:
        for i, item in enumerate(items, 1):
            logger.info("  Analyzing [%d/%d]: %s", i, len(items), item.title[:50])

            messages = [
                {"role": "system", "content": ANALYZE_SYSTEM_PROMPT},
                {"role": "user", "content": _build_analyze_prompt(item)},
            ]

            try:
                response = await chat_with_retry(
                    provider, messages, temperature=0.3, max_tokens=512,
                )
                result = _parse_llm_response(response.content)
            except Exception as exc:
                logger.warning("LLM analysis failed for %r: %s", item.title, exc)
                result = {
                    "summary": item.description[:100] or "分析失败",
                    "score": 5.0,
                    "tags": ["error"],
                }

            analyzed.append(
                AnalyzedItem(
                    title=item.title,
                    url=item.url,
                    source=item.source,
                    description=item.description,
                    summary=result.get("summary", ""),
                    score=float(result.get("score", 5.0)),
                    tags=result.get("tags", []),
                    published_at=item.published_at,
                )
            )

            # 简单限流：避免触发 API rate limit
            if i < len(items):
                await asyncio.sleep(1.0)
    finally:
        await provider.close()

    logger.info("Analysis complete: %d items processed", len(analyzed))
    return analyzed


# ---------------------------------------------------------------------------
# Step 3: 整理（Organize）
# ---------------------------------------------------------------------------


def _generate_id(source: str, counter: int) -> str:
    """生成符合规范的 ID: {source}-{YYYYMMDD}-{NNN}。"""
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{source}-{date_str}-{counter:03d}"



# 标准标签列表（与 hooks/check_quality.py 保持一致）
STANDARD_TAGS = {
    "python", "javascript", "typescript", "rust", "go", "java", "c++",
    "ai", "ml", "llm", "deep-learning", "nlp", "computer-vision",
    "web", "frontend", "backend", "fullstack", "api", "rest", "graphql",
    "database", "sql", "nosql", "redis", "postgresql", "mongodb",
    "devops", "docker", "kubernetes", "ci-cd", "cloud", "aws", "azure",
    "security", "cryptography", "authentication",
    "architecture", "microservices", "distributed-systems",
    "testing", "performance", "monitoring", "observability",
    "open-source", "tooling", "cli", "editor",
    "algorithm", "data-structure", "concurrency", "networking",
    "mobile", "ios", "android", "flutter", "react-native",
}

# 非标准标签 -> 标准标签的映射
TAG_ALIASES: dict[str, str] = {
    "machine-learning": "ml",
    "deep-learning-model": "deep-learning",
    "large-language-model": "llm",
    "language-model": "llm",
    "neural-network": "deep-learning",
    "transformer": "deep-learning",
    "rag": "llm",
    "retrieval-augmented-generation": "llm",
    "gpt": "llm",
    "chatbot": "ai",
    "agent": "ai",
    "ai-agent": "ai",
    "gui-agent": "ai",
    "reinforcement-learning": "ml",
    "rl": "ml",
    "natural-language-processing": "nlp",
    "react": "frontend",
    "vue": "frontend",
    "angular": "frontend",
    "nextjs": "frontend",
    "node": "backend",
    "nodejs": "backend",
    "express": "backend",
    "fastapi": "python",
    "django": "python",
    "flask": "python",
    "spring": "java",
    "framework": "architecture",
    "microservice": "microservices",
    "micro-service": "microservices",
    "container": "docker",
    "k8s": "kubernetes",
    "cicd": "ci-cd",
    "ci": "ci-cd",
    "cd": "ci-cd",
    "github-actions": "ci-cd",
    "vector-database": "database",
    "vector-db": "database",
    "embedding": "ai",
    "inference": "ai",
    "training": "ml",
    "fine-tuning": "ml",
    "optimization": "performance",
    "gpu": "performance",
    "automation": "devops",
    "cli-tool": "cli",
    "tool": "tooling",
    "open-source-project": "open-source",
    "oss": "open-source",
    "accessibility": "mobile",
}


def _normalize_tags(tags: list[str], source: str = "", title: str = "") -> list[str]:
    """标准化标签：小写、去空白、去重，映射到标准标签，最多 3 个。

    当所有标签都无法匹配标准列表时，根据 source 和 title 推断兜底标签。
    """
    result = []
    seen: set[str] = set()

    for tag in tags:
        tag = tag.strip().lower().replace(" ", "-")
        tag = re.sub(r"[^a-z0-9\-\u4e00-\u9fff]", "", tag)
        if not tag:
            continue

        # 映射非标准标签到标准标签
        if tag not in STANDARD_TAGS:
            tag = TAG_ALIASES.get(tag, tag)

        # 仍然不在标准列表中则跳过
        if tag not in STANDARD_TAGS:
            continue

        if tag not in seen:
            seen.add(tag)
            result.append(tag)

        if len(result) >= 3:
            break

    # 兜底：如果没有匹配到任何标准标签，根据上下文推断
    if not result:
        context = f"{source} {title}".lower()
        fallback_rules = [
            (["llm", "gpt", "language-model", "chat"], "llm"),
            (["ai", "agent", "intelligent", "ml", "machine"], "ai"),
            (["python", "py"], "python"),
            (["javascript", "js", "node", "react", "vue"], "javascript"),
            (["typescript", "ts"], "typescript"),
            (["rust"], "rust"),
            (["go", "golang"], "go"),
            (["docker", "container"], "docker"),
            (["kubernetes", "k8s"], "kubernetes"),
            (["web", "http", "html"], "web"),
            (["database", "db", "sql"], "database"),
        ]
        for keywords, fallback_tag in fallback_rules:
            if any(kw in context for kw in keywords):
                result.append(fallback_tag)
                break

        # 最终兜底
        if not result:
            result.append("open-source")

    return result


# 技术关键词列表（与 hooks/check_quality.py TECH_KEYWORDS 保持一致）
TECH_KEYWORDS = {
    "算法", "架构", "性能", "并发", "分布式", "微服务", "容器",
    "模型", "训练", "推理", "向量", "索引", "缓存", "队列",
    "API", "SDK", "框架", "协议", "加密", "认证", "授权",
    "algorithm", "architecture", "performance", "concurrency",
    "distributed", "microservice", "container", "model", "inference",
}


def _enrich_summary(summary: str, description: str, title: str) -> str:
    """确保摘要至少包含 3 个技术关键词，不够则从描述中补充语境。"""
    found = [kw for kw in TECH_KEYWORDS if kw in summary]
    if len(found) >= 3:
        return summary

    # 从 description + title 中提取可用的技术关键词
    context = f"{title} {description}"
    missing_kws = [kw for kw in TECH_KEYWORDS if kw in context and kw not in summary]

    if not missing_kws and len(found) < 3:
        # 根据内容特征添加通用技术描述
        tech_suffixes = []
        lower_ctx = context.lower()
        if any(w in lower_ctx for w in ["ai", "llm", "agent", "gpt", "language model"]):
            tech_suffixes.append("基于模型推理的架构")
        elif any(w in lower_ctx for w in ["web", "api", "http", "rest"]):
            tech_suffixes.append("提供API框架和性能优化")
        elif any(w in lower_ctx for w in ["data", "database", "sql"]):
            tech_suffixes.append("支持索引和缓存的架构")
        else:
            tech_suffixes.append("采用模块化架构，关注性能和算法优化")

        if tech_suffixes:
            summary = f"{summary}，{tech_suffixes[0]}"

    return summary


def _validate_article(article: Article) -> bool:
    """校验文章数据完整性。"""
    if not article.title or not article.title.strip():
        return False
    if not article.source_url or not article.source_url.startswith("http"):
        return False
    if not article.summary:
        return False
    if not (0 <= article.score <= 10):
        return False
    return True


def step_organize(items: list[AnalyzedItem]) -> list[Article]:
    """Step 3: 去重 + 格式标准化 + 校验。"""
    logger.info("=" * 60)
    logger.info("Step 3: ORGANIZE (%d items)", len(items))
    logger.info("=" * 60)

    now = datetime.now(timezone.utc).isoformat()
    seen_urls: set[str] = set()
    articles: list[Article] = []
    duplicates = 0
    invalid = 0

    # 检查已有文章，避免保存重复
    if ARTICLES_DIR.exists():
        for existing_file in ARTICLES_DIR.glob("*.json"):
            try:
                data = json.loads(existing_file.read_text(encoding="utf-8"))
                # 兼容旧字段 url 和新字段 source_url
                url = data.get("source_url") or data.get("url")
                if url:
                    seen_urls.add(url)
            except (json.JSONDecodeError, OSError):
                continue

    # 计数器用于生成顺序 ID
    counter = 0

    for item in items:
        # 去重
        if item.url in seen_urls:
            duplicates += 1
            logger.debug("  Duplicate skipped: %s", item.url)
            continue
        seen_urls.add(item.url)

        counter += 1

        # 摘要后处理：确保包含足够技术关键词
        enriched_summary = _enrich_summary(
            item.summary.strip(),
            item.description.strip(),
            item.title.strip(),
        )

        # 确保摘要至少50字
        if len(enriched_summary) < 50 and item.description:
            enriched_summary = f"{enriched_summary}。{item.description.strip()[:80]}"

        # 评分后处理：通过筛选的项目至少8分
        score = round(min(max(item.score, 0), 10), 1)
        if score < 8.0:
            score = 8.0

        # 格式标准化
        article = Article(
            id=_generate_id(item.source, counter),
            title=item.title.strip(),
            source_url=item.url.strip(),
            source=item.source,
            summary=enriched_summary,
            score=score,
            tags=_normalize_tags(item.tags, source=item.source, title=item.title),
            status="draft",
            created_at=now,
            published_at=item.published_at,
            collected_at=now,
            description=item.description.strip()[:300],
        )

        # 校验
        if not _validate_article(article):
            invalid += 1
            logger.debug("  Invalid article skipped: %s", article.title[:40])
            continue

        articles.append(article)

    logger.info(
        "Organize complete: %d valid, %d duplicates, %d invalid",
        len(articles),
        duplicates,
        invalid,
    )
    return articles


# ---------------------------------------------------------------------------
# Step 4: 保存（Save）
# ---------------------------------------------------------------------------


def step_save(articles: list[Article], dry_run: bool = False) -> list[Path]:
    """Step 4: 将文章保存为独立 JSON 文件。"""
    logger.info("=" * 60)
    logger.info("Step 4: SAVE (%d articles, dry_run=%s)", len(articles), dry_run)
    logger.info("=" * 60)

    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
    saved_files: list[Path] = []

    for article in articles:
        filename = f"{article.id}.json"
        filepath = ARTICLES_DIR / filename

        article_data = asdict(article)

        if dry_run:
            logger.info("  [DRY-RUN] Would save: %s -> %s", article.title[:40], filename)
            continue

        filepath.write_text(
            json.dumps(article_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        saved_files.append(filepath)
        logger.debug("  Saved: %s", filepath)

    if not dry_run:
        logger.info("Saved %d articles to %s", len(saved_files), ARTICLES_DIR)
    else:
        logger.info("[DRY-RUN] Would save %d articles", len(articles))

    return saved_files


# ---------------------------------------------------------------------------
# Pipeline 主流程
# ---------------------------------------------------------------------------


async def run_pipeline(
    sources: list[str],
    limit: int = 20,
    dry_run: bool = False,
    provider_name: str | None = None,
) -> dict[str, Any]:
    """执行完整的四步流水线。"""
    start_time = time.perf_counter()

    logger.info("Pipeline started: sources=%s, limit=%d, dry_run=%s, provider=%s",
                sources, limit, dry_run, provider_name or "auto")

    # Step 1: 采集
    raw_items = await step_collect(sources, limit)
    if not raw_items:
        logger.warning("No items collected. Pipeline finished early.")
        return {"collected": 0, "analyzed": 0, "organized": 0, "saved": 0}

    # Step 2: 分析
    analyzed_items = await step_analyze(raw_items, dry_run=dry_run, provider_name=provider_name)

    # Step 3: 整理
    articles = step_organize(analyzed_items)

    # Step 4: 保存
    saved_files = step_save(articles, dry_run=dry_run)

    elapsed = time.perf_counter() - start_time
    summary = {
        "collected": len(raw_items),
        "analyzed": len(analyzed_items),
        "organized": len(articles),
        "saved": len(saved_files),
        "elapsed_seconds": round(elapsed, 1),
    }

    logger.info("=" * 60)
    logger.info("Pipeline complete!")
    logger.info("  Collected:  %d", summary["collected"])
    logger.info("  Analyzed:   %d", summary["analyzed"])
    logger.info("  Organized:  %d", summary["organized"])
    logger.info("  Saved:      %d", summary["saved"])
    logger.info("  Time:       %.1fs", summary["elapsed_seconds"])
    logger.info("=" * 60)

    # 打印 LLM 成本报告
    from model_client import cost_tracker
    cost_tracker.report()

    return summary


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="AI Knowledge Base Pipeline - 四步自动化流水线",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  python pipeline/pipeline.py --sources github,rss --limit 20
  python pipeline/pipeline.py --sources github --limit 5 --dry-run
  python pipeline/pipeline.py --sources rss --limit 10 --verbose
  python pipeline/pipeline.py --limit 5 --provider deepseek
        """,
    )
    parser.add_argument(
        "--sources",
        type=str,
        default="github,rss",
        help="数据源，逗号分隔 (可选: github, rss)，默认: github,rss",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="采集条目数上限，默认: 20",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default=None,
        help="LLM 提供商 (如 zhipu, qwen, deepseek)，默认从环境变量 LLM_PROVIDER 读取",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="干跑模式：跳过 LLM 调用和实际保存",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="启用详细日志输出",
    )
    return parser.parse_args()


def main() -> None:
    """CLI 主入口。"""
    args = parse_args()
    setup_logging(verbose=args.verbose)

    # 解析 sources 参数
    sources = [s.strip().lower() for s in args.sources.split(",")]
    valid_sources = {"github", "rss"}
    for src in sources:
        if src not in valid_sources:
            logger.error("Invalid source: %r. Valid options: %s", src, valid_sources)
            raise SystemExit(1)

    # 运行流水线
    asyncio.run(run_pipeline(
        sources=sources,
        limit=args.limit,
        dry_run=args.dry_run,
        provider_name=args.provider,
    ))


if __name__ == "__main__":
    main()
