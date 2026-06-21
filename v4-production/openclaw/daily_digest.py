"""每日 AI 知识简报推送入口脚本。

加载当日知识条目，过滤低质量文章（relevance_score < 0.6），
将高质量条目格式化为飞书卡片并推送。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from openclaw.distribution.publisher import FeishuPublisher, PublishResult

logger = logging.getLogger(__name__)

_DEFAULT_KNOWLEDGE_DIR = "knowledge/articles"
_MIN_RELEVANCE_SCORE = 0.6
_DEFAULT_TOP_N = 5

_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _stripped = _line.strip()
        if not _stripped or _stripped.startswith("#") or "=" not in _stripped:
            continue
        _key, _val = _stripped.split("=", 1)
        os.environ.setdefault(
            _key.strip(), _val.strip().strip("'\"")
        )


def _load_articles(
    knowledge_dir: str | Path, date: str
) -> tuple[list[dict[str, Any]], int]:
    """加载指定日期的知识条目。

    Args:
        knowledge_dir: 知识条目存储目录。
        date: 日期字符串 (YYYY-MM-DD)。

    Returns:
        (articles, total_count) 元组。
    """
    dir_path = Path(knowledge_dir)
    date_compact = date.replace("-", "")
    pattern = f"github-{date_compact}-*.json"
    files = sorted(dir_path.glob(pattern))

    logger.info("扫描 %s/%s，找到 %d 个文件", dir_path, pattern, len(files))

    articles: list[dict[str, Any]] = []
    for file_path in files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                article = json.load(f)
            if isinstance(article, dict):
                articles.append(article)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("跳过解析失败的文件 %s: %s", file_path, exc)

    return articles


def _get_score(article: dict[str, Any]) -> float:
    """从条目中提取相关性评分，统一归一化到 0.0-1.0。

    优先使用 relevance_score（0-1 刻度），
    若仅有 score 字段（0-10 刻度）则除以 10 归一化。
    """
    if article.get("relevance_score") is not None:
        return float(article["relevance_score"])
    score = article.get("score")
    if score is not None:
        return float(score) / 10.0
    return 0.0


def _score_emoji(score: float) -> str:
    """相关性评分 → emoji 指示灯。"""
    if score >= 0.8:
        return "🟢"
    if score >= 0.6:
        return "🟡"
    return "🔴"


def _score_feishu_color(score: float) -> str:
    """相关性评分 → 飞书卡片 header 模板颜色。"""
    if score >= 0.8:
        return "green"
    if score >= 0.6:
        return "yellow"
    return "red"


def _filter_quality(
    articles: list[dict[str, Any]], min_score: float
) -> list[dict[str, Any]]:
    """过滤低质量文章，保留 relevance_score >= min_score 的条目。"""
    return [a for a in articles if _get_score(a) >= min_score]


def _build_feishu_digest(
    articles: list[dict[str, Any]], date: str, total_count: int
) -> dict[str, Any]:
    """构建飞书多条目简报卡片。

    Args:
        articles: 已排序、已过滤的高质量条目列表。
        date: 日期字符串。
        total_count: 原始文章总数。

    Returns:
        飞书 interactive 卡片消息 dict（含 msg_type 和 card）。
    """
    elements: list[dict[str, Any]] = [
        {
            "tag": "markdown",
            "content": (
                f"**{date}** 共收录 {total_count} 条，"
                f"精选 Top {len(articles)} 条"
            ),
        },
        {"tag": "hr"},
    ]

    for idx, article in enumerate(articles, start=1):
        title = article.get("title", "")
        score = _get_score(article)
        emoji = _score_emoji(score)
        color = _score_feishu_color(score)
        summary = article.get("summary", "")
        url = article.get("url") or article.get("source_url") or ""
        tags = article.get("tags", [])
        source = article.get("source", "")

        elements.append({
            "tag": "markdown",
            "content": f"**{idx}. {title}** {emoji}",
        })

        if summary:
            elements.append({
                "tag": "markdown",
                "content": summary,
            })

        meta_parts = [f"来源: {source}", f"相关性: {score:.2f}"]
        if tags:
            meta_parts.append("标签: " + " ".join(f"`{t}`" for t in tags))
        elements.append({
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": "  |  ".join(meta_parts),
                }
            ],
        })

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
                        "type": "primary" if color == "green" else "default",
                    }
                ],
            })

        if idx < len(articles):
            elements.append({"tag": "hr"})

    header_color = (
        _score_feishu_color(_get_score(articles[0]))
        if articles
        else "blue"
    )

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": "📰 每日 AI 知识简报",
            },
            "template": header_color,
        },
        "elements": elements,
    }

    return {"msg_type": "interactive", "card": card}


async def run_daily_digest(
    knowledge_dir: str | None = None,
    date: str | None = None,
    top_n: int = _DEFAULT_TOP_N,
    min_score: float = _MIN_RELEVANCE_SCORE,
) -> list[PublishResult]:
    """加载、过滤、推送每日知识简报。

    Args:
        knowledge_dir: 知识条目目录路径。
        date: 日期 (YYYY-MM-DD)，None 为当天。
        top_n: 推送前 N 条。
        min_score: 最低相关性分数阈值。

    Returns:
        PublishResult 列表。
    """
    if date is None:
        date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(
            "%Y-%m-%d"
        )
    if knowledge_dir is None:
        knowledge_dir = _DEFAULT_KNOWLEDGE_DIR

    articles = _load_articles(knowledge_dir, date)
    total_count = len(articles)

    if not articles:
        logger.warning("📭 %s 暂无新增知识条目，跳过推送", date)
        return []

    quality = _filter_quality(articles, min_score)
    logger.info(
        "日期 %s，共 %d 条，%d 条通过质量过滤 (score >= %.1f)",
        date,
        total_count,
        len(quality),
        min_score,
    )

    if not quality:
        logger.warning(
            "⚠️ %s 无高质量文章 (relevance_score >= %.1f)，跳过推送",
            date,
            min_score,
        )
        return []

    quality.sort(key=_get_score, reverse=True)
    top_articles = quality[:top_n]

    if not os.getenv("FEISHU_WEBHOOK_URL"):
        logger.warning("FEISHU_WEBHOOK_URL 未配置，跳过推送")
        return []

    card = _build_feishu_digest(top_articles, date, total_count)

    publisher = FeishuPublisher()
    try:
        result = await publisher.send_message(card)
    finally:
        await publisher.close()

    return [result]


def main() -> None:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(
        description="每日 AI 知识简报推送 — 过滤低质量文章后推送到飞书"
    )
    parser.add_argument(
        "--knowledge-dir",
        default=None,
        help="知识条目目录路径",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="日期 (YYYY-MM-DD)，默认前一天",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=_DEFAULT_TOP_N,
        help=f"推送前 N 条 (默认 {_DEFAULT_TOP_N})",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=_MIN_RELEVANCE_SCORE,
        help=f"最低相关性分数阈值 (默认 {_MIN_RELEVANCE_SCORE})",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="详细日志输出",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    results = asyncio.run(
        run_daily_digest(
            knowledge_dir=args.knowledge_dir,
            date=args.date,
            top_n=args.top_n,
            min_score=args.min_score,
        )
    )

    if not results:
        print("📭 无推送内容")
        sys.exit(0)

    success_count = sum(1 for r in results if r.success)
    fail_count = len(results) - success_count

    print(f"\n=== 推送结果汇总 ===")
    print(f"成功: {success_count} 个渠道")
    print(f"失败: {fail_count} 个渠道")

    for r in results:
        status = "✅" if r.success else "❌"
        detail = r.message_id or r.error or "—"
        print(f"{status} [{r.channel}] {detail}")


if __name__ == "__main__":
    main()
