"""知识条目格式化模块。

提供将知识条目 JSON 转换为 Markdown、Telegram MarkdownV2、
飞书 interactive 卡片格式的纯函数，以及生成当日简报的工具函数。

本模块为纯函数模块，不发送任何网络请求——
实际推送由 publisher.py 负责。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Telegram MarkdownV2 需要转义的特殊字符
# 参考: https://core.telegram.org/bots/api#markdownv2-style
_TG_ESCAPE_CHARS = "_*[]()~`>#+-=|{}.!"

# 默认知识条目存储目录
_DEFAULT_KNOWLEDGE_DIR = "knowledge/articles"


def _escape_telegram(text: str) -> str:
    """转义 Telegram MarkdownV2 特殊字符。

    Args:
        text: 原始文本。

    Returns:
        转义后的文本，可直接用于 MarkdownV2 消息。
    """
    escaped = []
    for char in str(text):
        if char in _TG_ESCAPE_CHARS:
            escaped.append(f"\\{char}")
        else:
            escaped.append(char)
    return "".join(escaped)


def _score_emoji(score: float) -> str:
    """根据相关性评分返回 emoji 指示灯。

    Args:
        score: 相关性评分 (0.0-1.0)。

    Returns:
        >= 0.8 返回 🟢，>= 0.6 返回 🟡，否则返回 🔴。
    """
    if score >= 0.8:
        return "🟢"
    if score >= 0.6:
        return "🟡"
    return "🔴"


def _score_feishu_color(score: float) -> str:
    """根据相关性评分返回飞书卡片 header 模板颜色。

    Args:
        score: 相关性评分 (0.0-1.0)。

    Returns:
        green / yellow / red 之一。
    """
    if score >= 0.8:
        return "green"
    if score >= 0.6:
        return "yellow"
    return "red"


def _get_url(article: dict[str, Any]) -> str:
    """从知识条目中获取原文链接，兼容 url / source_url 字段。

    Args:
        article: 知识条目 dict。

    Returns:
        原文链接字符串，若无则返回空字符串。
    """
    return article.get("url") or article.get("source_url") or ""


def _get_score(article: dict[str, Any]) -> float:
    """从知识条目中获取相关性评分。

    兼容 ``relevance_score`` 和 ``score`` 两种字段名。

    Args:
        article: 知识条目 dict。

    Returns:
        评分数值 (float)，缺省为 0.0。
    """
    value = article.get("relevance_score")
    if value is None:
        value = article.get("score", 0.0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _get_date(article: dict[str, Any]) -> str:
    """从知识条目中提取日期 (collected_at 前 10 位)。

    Args:
        article: 知识条目 dict。

    Returns:
        YYYY-MM-DD 格式日期字符串，缺省为空字符串。
    """
    collected_at = article.get("collected_at") or ""
    return str(collected_at)[:10]


def json_to_markdown(article: dict[str, Any]) -> str:
    """将单篇知识条目格式化为 Markdown。

    Args:
        article: 知识条目 dict，遵循 v3 Organizer 产出 schema。

    Returns:
        Markdown 格式字符串，包含标题、来源、日期、相关性评分
        (带 emoji 指示灯)、标签、摘要、原文链接。
    """
    title = article.get("title", "")
    source = article.get("source", "")
    date = _get_date(article)
    score = _get_score(article)
    emoji = _score_emoji(score)
    tags = article.get("tags", [])
    summary = article.get("summary", "")
    url = _get_url(article)

    tags_str = ", ".join(f"`{tag}`" for tag in tags) if tags else "无标签"

    lines = [
        f"# {title}",
        "",
        f"- **来源**: {source}",
        f"- **日期**: {date}",
        f"- **相关性**: {emoji} {score:.2f}",
        f"- **标签**: {tags_str}",
        "",
        "## 摘要",
        "",
        summary,
        "",
        f"**原文链接**: {url}" if url else "",
    ]

    # 过滤掉空行末尾的多余空行
    return "\n".join(line for line in lines if line is not None).rstrip() + "\n"


def json_to_telegram(article: dict[str, Any]) -> str:
    """将单篇知识条目格式化为 Telegram MarkdownV2 消息。

    转义 _*[]()~`>#+-=|{}.! 这些特殊字符。
    tag 中的空格替换为下划线。

    Args:
        article: 知识条目 dict，遵循 v3 Organizer 产出 schema。

    Returns:
        Telegram MarkdownV2 格式字符串。
    """
    title = _escape_telegram(article.get("title", ""))
    source = _escape_telegram(article.get("source", ""))
    score = _get_score(article)
    emoji = _score_emoji(score)
    summary = _escape_telegram(article.get("summary", ""))
    url = _escape_telegram(_get_url(article))

    tags = article.get("tags", [])
    tags_str = " ".join(
        f"#{_escape_telegram(str(tag).replace(' ', '_'))}" for tag in tags
    ) if tags else ""

    score_str = _escape_telegram(f"{score:.2f}")

    # 标题作为超链接
    if url:
        title_line = f"[{title}]({url})"
    else:
        title_line = title

    lines = [
        f"*{title_line}*",
        "",
        summary,
        "",
        f"相关性: {emoji} {score_str}",
        f"来源: {source}",
    ]

    if tags_str:
        lines.append(tags_str)

    return "\n".join(lines)


def json_to_feishu(article: dict[str, Any]) -> dict[str, Any]:
    """将单篇知识条目格式化为飞书 interactive 卡片 dict。

    msg_type 为 interactive，header.template 按 score 染色
    (green / yellow / red)。

    Args:
        article: 知识条目 dict，遵循 v3 Organizer 产出 schema。

    Returns:
        飞书消息卡片的 dict 结构，含 msg_type 和 card 两部分。
    """
    title = article.get("title", "")
    source = article.get("source", "")
    date = _get_date(article)
    score = _get_score(article)
    emoji = _score_emoji(score)
    color = _score_feishu_color(score)
    tags = article.get("tags", [])
    summary = article.get("summary", "")
    url = _get_url(article)
    key_insight = article.get("key_insight", "")

    # 构造卡片内容元素
    elements: list[dict[str, Any]] = []

    # 摘要 (markdown 类型)
    if summary:
        elements.append({
            "tag": "markdown",
            "content": summary,
        })

    # 关键洞察 (引用块样式)
    if key_insight:
        elements.append({
            "tag": "markdown",
            "content": f"> 💡 **关键洞察**: {key_insight}",
        })

    # 分隔线
    elements.append({"tag": "hr"})

    # 元信息 (字段列)
    fields: list[dict[str, Any]] = [
        {
            "is_short": True,
            "text": {
                "tag": "lark_md",
                "content": f"**来源**\n{source}",
            },
        },
        {
            "is_short": True,
            "text": {
                "tag": "lark_md",
                "content": f"**日期**\n{date}",
            },
        },
        {
            "is_short": True,
            "text": {
                "tag": "lark_md",
                "content": f"**相关性**\n{emoji} {score:.2f}",
            },
        },
    ]

    if tags:
        tags_text = " ".join(f"`{tag}`" for tag in tags)
        fields.append({
            "is_short": False,
            "text": {
                "tag": "lark_md",
                "content": f"**标签**\n{tags_text}",
            },
        })

    elements.append({"tag": "div", "fields": fields})

    # 原文链接 (行动按钮)
    if url:
        elements.append({
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {
                        "tag": "lark_md",
                        "content": "🔗 查看原文",
                    },
                    "url": url,
                    "type": "primary",
                }
            ],
        })

    card: dict[str, Any] = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": title,
            },
            "template": color,
        },
        "elements": elements,
    }

    return {"msg_type": "interactive", "card": card}


def generate_daily_digest(
    knowledge_dir: str | Path = _DEFAULT_KNOWLEDGE_DIR,
    date: str | None = None,
    top_n: int = 5,
) -> dict[str, Any]:
    """生成当日知识简报。

    扫描 knowledge_dir 下以 ``{date}-`` 为前缀的 JSON 文件，
    按 relevance_score 降序取 Top N，生成 markdown / telegram /
    feishu 三种格式的简报。

    Args:
        knowledge_dir: 知识条目存储目录，默认 ``knowledge/articles``。
        date: 日期字符串 (YYYY-MM-DD)，None 表示当天 (UTC)。
        top_n: 取前 N 条，默认 5。

    Returns:
        dict，包含 ``markdown`` / ``telegram`` / ``feishu`` 三个键。
        当日无文章时，三个键的值均为
        ``"📭 {date} 暂无新增知识条目"``。
    """
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    dir_path = Path(knowledge_dir)
    date_compact = date.replace("-", "")
    pattern = f"github-{date_compact}-*.json"
    files = sorted(dir_path.glob(pattern))
    logger.info(
        "[Digest] 扫描 %s/%s，找到 %d 个文件",
        dir_path,
        pattern,
        len(files),
    )

    if not files:
        empty_msg = f"📭 {date} 暂无新增知识条目"
        return {
            "markdown": empty_msg,
            "telegram": empty_msg,
            "feishu": empty_msg,
        }

    # 读取并解析所有条目
    articles: list[dict[str, Any]] = []
    for file_path in files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                article = json.load(f)
            if isinstance(article, dict):
                articles.append(article)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("[Digest] 跳过解析失败的文件 %s: %s", file_path, exc)

    # 按 relevance_score 降序排序，取 Top N
    articles.sort(key=_get_score, reverse=True)
    top_articles = articles[:top_n]

    logger.info(
        "[Digest] 日期 %s，共 %d 条，取 Top %d",
        date,
        len(articles),
        len(top_articles),
    )

    # 生成 Markdown 简报
    md_parts = [f"# 📰 每日 AI 知识简报 — {date}", ""]
    for idx, article in enumerate(top_articles, start=1):
        md_parts.append(f"## {idx}. {article.get('title', '')}")
        md_parts.append("")
        score = _get_score(article)
        emoji = _score_emoji(score)
        md_parts.append(
            f"- **来源**: {article.get('source', '')} | "
            f"**相关性**: {emoji} {score:.2f}"
        )
        summary = article.get("summary", "")
        if summary:
            md_parts.append("")
            md_parts.append(summary)
        url = _get_url(article)
        if url:
            md_parts.append("")
            md_parts.append(f"[原文链接]({url})")
        md_parts.append("")
    markdown_digest = "\n".join(md_parts).rstrip() + "\n"

    # 生成 Telegram 简报
    tg_parts = [
        f"*{_escape_telegram('📰 每日 AI 知识简报')}*",
        f"*{_escape_telegram(date)}*",
        "",
    ]
    for idx, article in enumerate(top_articles, start=1):
        title = _escape_telegram(article.get("title", ""))
        url = _get_url(article)
        if url:
            title_line = f"[{title}]({_escape_telegram(url)})"
        else:
            title_line = title
        score = _get_score(article)
        emoji = _score_emoji(score)
        score_str = _escape_telegram(f"{score:.2f}")
        summary = _escape_telegram(article.get("summary", ""))
        source = _escape_telegram(article.get("source", ""))

        tg_parts.append(f"*{idx}\\.{title_line}*")
        tg_parts.append("")
        tg_parts.append(summary)
        tg_parts.append("")
        tg_parts.append(f"相关性: {emoji} {score_str} \\| 来源: {source}")
        tg_parts.append("")
    telegram_digest = "\n".join(tg_parts).rstrip()

    # 生成飞书卡片简报
    feishu_elements: list[dict[str, Any]] = [
        {
            "tag": "markdown",
            "content": f"**{date}** 共收录 {len(articles)} 条，"
            f"精选 Top {len(top_articles)} 条",
        },
        {"tag": "hr"},
    ]

    for idx, article in enumerate(top_articles, start=1):
        title = article.get("title", "")
        score = _get_score(article)
        emoji = _score_emoji(score)
        color = _score_feishu_color(score)
        summary = article.get("summary", "")
        url = _get_url(article)
        tags = article.get("tags", [])
        source = article.get("source", "")

        # 标题 (带序号和评分色块)
        feishu_elements.append({
            "tag": "markdown",
            "content": f"**{idx}. {title}** {emoji}",
        })

        if summary:
            feishu_elements.append({
                "tag": "markdown",
                "content": summary,
            })

        # 元信息行
        meta_parts = [f"来源: {source}", f"相关性: {score:.2f}"]
        if tags:
            meta_parts.append("标签: " + " ".join(f"`{t}`" for t in tags))
        feishu_elements.append({
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": "  |  ".join(meta_parts),
                }
            ],
        })

        if url:
            feishu_elements.append({
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {
                            "tag": "lark_md",
                            "content": "🔗 查看原文",
                        },
                        "url": url,
                        "type": "primary" if color == "green" else "default",
                    }
                ],
            })

        if idx < len(top_articles):
            feishu_elements.append({"tag": "hr"})

    # 使用最高分文章的颜色作为 header 颜色
    header_color = (
        _score_feishu_color(_get_score(top_articles[0]))
        if top_articles
        else "blue"
    )

    feishu_card: dict[str, Any] = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": "📰 每日 AI 知识简报",
            },
            "template": header_color,
        },
        "elements": feishu_elements,
    }

    feishu_digest = {"msg_type": "interactive", "card": feishu_card}

    return {
        "markdown": markdown_digest,
        "telegram": telegram_digest,
        "feishu": feishu_digest,
    }
