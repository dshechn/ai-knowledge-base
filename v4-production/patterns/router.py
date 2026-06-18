"""Router 路由模式：两层意图分类 + 三意图处理。

两层分类策略:
    1. 关键词快速匹配（零成本，不调 LLM）
    2. LLM 分类兜底（处理模糊意图）

三种意图:
    - github_search: 调用 GitHub Search API 搜索仓库
    - knowledge_query: 从本地知识库检索文章
    - general_chat: 调用 LLM 直接回答

依赖:
    - pipeline.model_client.quick_chat (LLM 调用)

Example:
    >>> from patterns.router import route
    >>> result = route("搜索 LangChain 相关的 GitHub 项目")
    >>> print(result)
"""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from pipeline.model_client import LLMResponse, quick_chat

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM 调用适配层（适配 chat / chat_json 接口）
# ---------------------------------------------------------------------------

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 知识库文章目录
ARTICLES_DIR = PROJECT_ROOT / "knowledge" / "articles"

# 意图枚举
INTENT_GITHUB_SEARCH = "github_search"
INTENT_KNOWLEDGE_QUERY = "knowledge_query"
INTENT_GENERAL_CHAT = "general_chat"

VALID_INTENTS = {INTENT_GITHUB_SEARCH, INTENT_KNOWLEDGE_QUERY, INTENT_GENERAL_CHAT}


def chat(prompt: str, system: str = "你是一个有帮助的AI助手。") -> tuple[str, Any]:
    """调用 LLM 并返回 (text, usage) 元组。

    Args:
        prompt: 用户提示词。
        system: 系统提示词。

    Returns:
        (回复文本, Usage 对象) 元组。
    """
    response: LLMResponse = quick_chat(prompt, system=system)
    return response.content, response.usage


def chat_json(
    prompt: str,
    system: str = "你是一个有帮助的AI助手。",
) -> tuple[dict[str, Any], Any]:
    """调用 LLM 并将返回内容解析为 JSON。

    Args:
        prompt: 用户提示词（应要求模型返回 JSON）。
        system: 系统提示词。

    Returns:
        (解析后的 dict, Usage 对象) 元组。

    Raises:
        json.JSONDecodeError: 模型返回内容无法解析为 JSON。
    """
    text, usage = chat(prompt, system=system)
    # 尝试提取 JSON 块（兼容 ```json ... ``` 格式）
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # 去掉首行 ```json 和末尾 ```
        lines = cleaned.split("\n")
        lines = lines[1:]  # 去掉 ```json
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines)

    parsed: dict[str, Any] = json.loads(cleaned)
    return parsed, usage


# ---------------------------------------------------------------------------
# 第一层：关键词快速匹配
# ---------------------------------------------------------------------------

# 关键词 -> 意图映射表
KEYWORD_RULES: list[tuple[list[str], str]] = [
    # GitHub 搜索意图关键词
    (
        [
            "github", "仓库", "repo", "repository", "开源项目",
            "star", "stars", "fork", "搜索项目", "搜索仓库",
            "github搜索", "github search",
        ],
        INTENT_GITHUB_SEARCH,
    ),
    # 知识库查询意图关键词
    (
        [
            "知识库", "文章", "收藏", "已采集", "本地知识",
            "knowledge", "article", "已有文章", "之前采集",
            "知识条目", "检索",
        ],
        INTENT_KNOWLEDGE_QUERY,
    ),
]


def _classify_by_keyword(query: str) -> str | None:
    """通过关键词快速匹配意图（零 LLM 成本）。

    Args:
        query: 用户输入的查询文本。

    Returns:
        匹配到的意图字符串，未匹配返回 None。
    """
    query_lower = query.lower()
    for keywords, intent in KEYWORD_RULES:
        for kw in keywords:
            if kw in query_lower:
                logger.debug(
                    "Keyword match: %r -> intent=%s", kw, intent
                )
                return intent
    return None


# ---------------------------------------------------------------------------
# 第二层：LLM 分类兜底
# ---------------------------------------------------------------------------

_CLASSIFY_SYSTEM_PROMPT = """你是一个意图分类器。根据用户输入，判断属于以下哪种意图：

1. github_search - 用户想搜索 GitHub 上的开源项目、仓库、代码
2. knowledge_query - 用户想查询本地知识库中已有的文章或技术内容
3. general_chat - 通用对话，不属于以上两种

请只返回一个 JSON 对象，格式：{"intent": "意图名称"}
不要包含任何其他文字。"""


def _classify_by_llm(query: str) -> str:
    """通过 LLM 进行意图分类（兜底策略）。

    Args:
        query: 用户输入的查询文本。

    Returns:
        分类后的意图字符串。无法识别时默认返回 general_chat。
    """
    try:
        result, _usage = chat_json(
            prompt=f"用户输入：{query}",
            system=_CLASSIFY_SYSTEM_PROMPT,
        )
        intent = result.get("intent", INTENT_GENERAL_CHAT)
        if intent not in VALID_INTENTS:
            logger.warning(
                "LLM returned invalid intent %r, falling back to general_chat",
                intent,
            )
            return INTENT_GENERAL_CHAT
        logger.info("LLM classification: query=%r -> intent=%s", query, intent)
        return intent
    except (json.JSONDecodeError, Exception) as exc:
        logger.error("LLM classification failed: %s, fallback to general_chat", exc)
        return INTENT_GENERAL_CHAT


def classify(query: str) -> str:
    """两层意图分类：关键词优先，LLM 兜底。

    Args:
        query: 用户输入。

    Returns:
        意图字符串：github_search / knowledge_query / general_chat。
    """
    # 第一层：关键词快速匹配
    intent = _classify_by_keyword(query)
    if intent is not None:
        logger.info("Intent resolved by keyword: %s", intent)
        return intent

    # 第二层：LLM 分类兜底
    logger.info("No keyword match, falling back to LLM classification")
    return _classify_by_llm(query)


# ---------------------------------------------------------------------------
# 处理器：github_search
# ---------------------------------------------------------------------------

_GITHUB_SEARCH_API = "https://api.github.com/search/repositories"


def handle_github_search(query: str) -> str:
    """调用 GitHub Search API 搜索仓库。

    使用 urllib.request 发起请求，query 参数经 urllib.parse.quote 编码
    以正确处理中文与空格。

    Args:
        query: 搜索关键词。

    Returns:
        格式化的搜索结果字符串。
    """
    # 从 query 中提取搜索关键词（去掉意图触发词）
    search_term = _extract_search_term(query)
    encoded_query = urllib.parse.quote(search_term)
    url = f"{_GITHUB_SEARCH_API}?q={encoded_query}&sort=stars&per_page=5"

    logger.info("GitHub search: query=%r, url=%s", search_term, url)

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "AI-Knowledge-Base-Router/1.0",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.error("GitHub API request failed: %s", exc)
        return f"GitHub 搜索失败：{exc}"

    items = data.get("items", [])
    if not items:
        return f"未找到与「{search_term}」相关的 GitHub 仓库。"

    lines = [f"🔍 GitHub 搜索结果（关键词：{search_term}）：\n"]
    for i, item in enumerate(items, 1):
        name = item.get("full_name", "unknown")
        desc = item.get("description", "无描述") or "无描述"
        stars = item.get("stargazers_count", 0)
        html_url = item.get("html_url", "")
        lines.append(
            f"{i}. **{name}** ⭐ {stars:,}\n"
            f"   {desc}\n"
            f"   {html_url}\n"
        )

    total_count = data.get("total_count", 0)
    lines.append(f"\n共找到 {total_count:,} 个相关仓库（显示前 5 个）。")
    return "\n".join(lines)


def _extract_search_term(query: str) -> str:
    """从用户输入中提取实际搜索关键词。

    去掉常见的意图触发词，保留核心搜索词。

    Args:
        query: 原始用户输入。

    Returns:
        清洗后的搜索关键词。
    """
    noise_words = [
        "搜索", "查找", "找", "搜", "帮我",
        "github", "GitHub", "上的", "相关的",
        "项目", "仓库", "repo", "请",
    ]
    result = query
    for word in noise_words:
        result = result.replace(word, "")
    result = result.strip()
    # 如果清洗后为空，使用原始 query
    return result if result else query


# ---------------------------------------------------------------------------
# 处理器：knowledge_query
# ---------------------------------------------------------------------------


def _load_knowledge_index() -> list[dict[str, Any]]:
    """加载本地知识库索引。

    优先从 knowledge/articles/index.json 读取；
    若 index.json 不存在，则扫描目录下所有 .json 文件构建索引。

    Returns:
        文章元数据列表。
    """
    index_path = ARTICLES_DIR / "index.json"

    if index_path.exists():
        with open(index_path, "r", encoding="utf-8") as f:
            return json.load(f)

    # 回退：扫描目录构建索引
    articles: list[dict[str, Any]] = []
    if not ARTICLES_DIR.exists():
        logger.warning("Articles directory not found: %s", ARTICLES_DIR)
        return articles

    for file_path in sorted(ARTICLES_DIR.glob("*.json")):
        if file_path.name == "index.json":
            continue
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                article = json.load(f)
                articles.append(article)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load %s: %s", file_path, exc)

    logger.info("Built knowledge index from %d article files", len(articles))
    return articles


def handle_knowledge_query(query: str) -> str:
    """从本地知识库检索相关文章。

    基于关键词在标题、摘要和标签中进行模糊匹配。

    Args:
        query: 用户查询文本。

    Returns:
        格式化的检索结果字符串。
    """
    articles = _load_knowledge_index()
    if not articles:
        return "本地知识库暂无文章，请先运行采集流程。"

    query_lower = query.lower()
    # 提取查询中的关键词
    search_keywords = [
        w for w in query_lower.split()
        if len(w) > 1 and w not in ("知识库", "文章", "检索", "查询", "的", "有")
    ]

    # 如果关键词为空，展示最新文章
    if not search_keywords:
        return _format_articles(articles[:5], title="最新知识库文章")

    # 评分匹配
    scored: list[tuple[float, dict[str, Any]]] = []
    for article in articles:
        score = _calc_relevance(article, search_keywords)
        if score > 0:
            scored.append((score, article))

    scored.sort(key=lambda x: x[0], reverse=True)
    matched = [article for _, article in scored[:5]]

    if not matched:
        return f"知识库中未找到与「{query}」相关的文章。"

    return _format_articles(matched, title=f"知识库检索结果（关键词：{query}）")


def _calc_relevance(
    article: dict[str, Any], keywords: list[str]
) -> float:
    """计算文章与搜索关键词的相关性得分。

    Args:
        article: 文章字典。
        keywords: 搜索关键词列表。

    Returns:
        相关性得分（0.0 ~ 无上限）。
    """
    score = 0.0
    title = article.get("title", "").lower()
    summary = article.get("summary", "").lower()
    description = article.get("description", "").lower()
    tags = [t.lower() for t in article.get("tags", [])]

    for kw in keywords:
        if kw in title:
            score += 3.0
        if kw in summary:
            score += 2.0
        if kw in description:
            score += 1.5
        if kw in tags:
            score += 2.5

    return score


def _format_articles(
    articles: list[dict[str, Any]], title: str = "检索结果"
) -> str:
    """格式化文章列表为可读字符串。

    Args:
        articles: 文章列表。
        title: 结果标题。

    Returns:
        格式化的结果文本。
    """
    lines = [f"📚 {title}：\n"]
    for i, article in enumerate(articles, 1):
        name = article.get("title", "未知标题")
        summary = article.get("summary", "暂无摘要")
        tags = ", ".join(article.get("tags", []))
        url = article.get("source_url", "")
        status = article.get("status", "unknown")
        lines.append(
            f"{i}. **{name}** [{status}]\n"
            f"   {summary[:100]}{'...' if len(summary) > 100 else ''}\n"
            f"   标签：{tags}\n"
            f"   链接：{url}\n"
        )

    lines.append(f"\n共匹配 {len(articles)} 篇文章。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 处理器：general_chat
# ---------------------------------------------------------------------------


def handle_general_chat(query: str) -> str:
    """调用 LLM 直接回答通用问题。

    Args:
        query: 用户输入。

    Returns:
        LLM 生成的回复文本。
    """
    system_prompt = (
        "你是 AI Knowledge Base 的智能助手。"
        "请用简洁、专业的中文回答用户的问题。"
        "如果涉及 AI/LLM/Agent 技术领域，请提供尽可能准确的信息。"
    )
    text, _usage = chat(prompt=query, system=system_prompt)
    return text


# ---------------------------------------------------------------------------
# 路由分发表
# ---------------------------------------------------------------------------

_HANDLER_MAP: dict[str, Any] = {
    INTENT_GITHUB_SEARCH: handle_github_search,
    INTENT_KNOWLEDGE_QUERY: handle_knowledge_query,
    INTENT_GENERAL_CHAT: handle_general_chat,
}


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------


def route(query: str) -> str:
    """Router 统一入口：分类意图并路由到对应处理器。

    工作流程:
        1. 两层意图分类（关键词 → LLM 兜底）
        2. 根据意图调度对应处理器函数
        3. 返回处理结果字符串

    Args:
        query: 用户输入的查询文本。

    Returns:
        处理器返回的结果字符串。
    """
    if not query or not query.strip():
        return "请输入您的问题或查询。"

    query = query.strip()
    logger.info("Route received query: %r", query)

    # 意图分类
    intent = classify(query)
    logger.info("Classified intent: %s", intent)

    # 路由到处理器
    handler = _HANDLER_MAP.get(intent, handle_general_chat)
    try:
        result = handler(query)
    except Exception as exc:
        logger.error(
            "Handler %s failed for query %r: %s", intent, query, exc
        )
        result = f"处理请求时发生错误：{exc}"

    return result


# ---------------------------------------------------------------------------
# 测试入口
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    test_queries = [
        # 第一层关键词匹配测试
        ("搜索 LangChain 相关的 GitHub 仓库", INTENT_GITHUB_SEARCH),
        ("知识库里有哪些关于 Agent 的文章", INTENT_KNOWLEDGE_QUERY),
        # 需要 LLM 兜底分类的模糊意图
        ("最近有什么好用的 AI 框架推荐", None),  # 可能是 github_search 或 general_chat
        ("什么是 RAG？", None),  # general_chat
    ]

    logger.info("=" * 60)
    logger.info("  Router 路由模式测试")
    logger.info("=" * 60)

    for query, expected_intent in test_queries:
        logger.info("\n--- Query: %r ---", query)

        # 测试分类
        intent = classify(query)
        match_status = ""
        if expected_intent:
            match_status = (
                " ✓" if intent == expected_intent
                else f" ✗ (expected {expected_intent})"
            )
        logger.info("Intent: %s%s", intent, match_status)

        # 测试完整路由（仅对关键词匹配的意图执行，避免大量 API 调用）
        if expected_intent == INTENT_KNOWLEDGE_QUERY:
            result = route(query)
            logger.info("Result:\n%s", result)
        elif expected_intent == INTENT_GITHUB_SEARCH:
            result = route(query)
            logger.info("Result:\n%s", result)

    logger.info("\n" + "=" * 60)
    logger.info("  测试完成")
    logger.info("=" * 60)
