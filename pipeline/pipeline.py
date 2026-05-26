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
import hashlib
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
    url: str
    source: str
    summary: str
    score: float
    tags: list[str]
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
1. summary: 50-100 字的中文摘要，概括核心内容
2. score: 1-10 的质量评分（考虑创新性、实用性、影响力）
3. tags: 3-5 个标签（英文小写，用连字符分隔）

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


async def step_analyze(items: list[RawItem], dry_run: bool = False) -> list[AnalyzedItem]:
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
        provider = create_provider()
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


def _generate_id(url: str) -> str:
    """根据 URL 生成唯一 ID。"""
    return hashlib.sha256(url.encode()).hexdigest()[:12]


def _normalize_tags(tags: list[str]) -> list[str]:
    """标准化标签：小写、去空白、去重。"""
    normalized = []
    seen: set[str] = set()
    for tag in tags:
        tag = tag.strip().lower().replace(" ", "-")
        # 只保留合法字符
        tag = re.sub(r"[^a-z0-9\-\u4e00-\u9fff]", "", tag)
        if tag and tag not in seen:
            seen.add(tag)
            normalized.append(tag)
    return normalized[:5]  # 最多 5 个标签


def _validate_article(article: Article) -> bool:
    """校验文章数据完整性。"""
    if not article.title or not article.title.strip():
        return False
    if not article.url or not article.url.startswith("http"):
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
                if url := data.get("url"):
                    seen_urls.add(url)
            except (json.JSONDecodeError, OSError):
                continue

    for item in items:
        # 去重
        if item.url in seen_urls:
            duplicates += 1
            logger.debug("  Duplicate skipped: %s", item.url)
            continue
        seen_urls.add(item.url)

        # 格式标准化
        article = Article(
            id=_generate_id(item.url),
            title=item.title.strip(),
            url=item.url.strip(),
            source=item.source,
            summary=item.summary.strip(),
            score=round(min(max(item.score, 0), 10), 1),
            tags=_normalize_tags(item.tags),
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
) -> dict[str, Any]:
    """执行完整的四步流水线。"""
    start_time = time.perf_counter()

    logger.info("Pipeline started: sources=%s, limit=%d, dry_run=%s", sources, limit, dry_run)

    # Step 1: 采集
    raw_items = await step_collect(sources, limit)
    if not raw_items:
        logger.warning("No items collected. Pipeline finished early.")
        return {"collected": 0, "analyzed": 0, "organized": 0, "saved": 0}

    # Step 2: 分析
    analyzed_items = await step_analyze(raw_items, dry_run=dry_run)

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
    asyncio.run(run_pipeline(sources=sources, limit=args.limit, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
