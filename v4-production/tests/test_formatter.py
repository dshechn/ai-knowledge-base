"""formatter.py 单元测试。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from distribution.formatter import (
    _escape_telegram,
    _get_date,
    _get_score,
    _get_url,
    _score_emoji,
    _score_feishu_color,
    generate_daily_digest,
    json_to_feishu,
    json_to_markdown,
    json_to_telegram,
)

SAMPLE_ARTICLE: dict = {
    "id": "2026-04-11-000",
    "title": "langgenius/dify",
    "source": "github",
    "url": "https://github.com/langgenius/dify",
    "collected_at": "2026-04-11T16:03:47.946653+00:00",
    "summary": "Dify 是一个开源 LLM 应用开发平台，支持工作流编排。",
    "tags": ["LLM应用开发", "智能体工作流", "RAG"],
    "relevance_score": 0.9,
    "category": "framework",
    "key_insight": "Dify 通过一体化平台显著降低 AI 工作流开发门槛",
}


def test_get_url() -> None:
    """验证 url / source_url 字段兼容。"""
    assert _get_url(SAMPLE_ARTICLE) == "https://github.com/langgenius/dify"
    assert _get_url({"source_url": "http://x.com"}) == "http://x.com"
    assert _get_url({}) == ""


def test_get_score() -> None:
    """验证 relevance_score / score 字段兼容。"""
    assert _get_score(SAMPLE_ARTICLE) == 0.9
    assert _get_score({"score": 8.0}) == 8.0
    assert _get_score({}) == 0.0
    assert _get_score({"relevance_score": "invalid"}) == 0.0


def test_get_date() -> None:
    """验证日期提取（collected_at 前 10 位）。"""
    assert _get_date(SAMPLE_ARTICLE) == "2026-04-11"
    assert _get_date({"collected_at": "2026-01-01T00:00:00Z"}) == "2026-01-01"
    assert _get_date({}) == ""


def test_score_emoji() -> None:
    """验证评分 emoji 指示灯。"""
    assert _score_emoji(0.8) == "🟢"
    assert _score_emoji(0.95) == "🟢"
    assert _score_emoji(0.6) == "🟡"
    assert _score_emoji(0.79) == "🟡"
    assert _score_emoji(0.59) == "🔴"
    assert _score_emoji(0.0) == "🔴"


def test_score_feishu_color() -> None:
    """验证飞书卡片颜色映射。"""
    assert _score_feishu_color(0.8) == "green"
    assert _score_feishu_color(0.6) == "yellow"
    assert _score_feishu_color(0.59) == "red"


def test_escape_telegram() -> None:
    """验证 Telegram MarkdownV2 特殊字符转义。"""
    assert _escape_telegram("hello") == "hello"
    assert _escape_telegram("a_b") == "a\\_b"
    assert _escape_telegram("a*b") == "a\\*b"
    assert _escape_telegram("a.b!c") == "a\\.b\\!c"
    # 所有特殊字符都应被转义
    special = "_*[]()~`>#+-=|{}.!"
    escaped = _escape_telegram(special)
    assert escaped == "".join(f"\\{c}" for c in special)


def test_json_to_markdown() -> None:
    """验证 Markdown 格式化输出。"""
    md = json_to_markdown(SAMPLE_ARTICLE)

    assert "# langgenius/dify" in md
    assert "**来源**: github" in md
    assert "**日期**: 2026-04-11" in md
    assert "🟢" in md
    assert "0.90" in md
    assert "`LLM应用开发`" in md
    assert "Dify 是一个开源 LLM 应用开发平台" in md
    assert "https://github.com/langgenius/dify" in md


def test_json_to_markdown_low_score() -> None:
    """验证低分文章使用 🔴。"""
    article = {**SAMPLE_ARTICLE, "relevance_score": 0.3}
    md = json_to_markdown(article)
    assert "🔴" in md
    assert "🟢" not in md


def test_json_to_telegram() -> None:
    """验证 Telegram MarkdownV2 格式化输出。"""
    tg = json_to_telegram(SAMPLE_ARTICLE)

    # 标题应被转义
    assert "langgenius/dify" in tg
    # URL 中的 . 和 / 应被转义
    assert "github\\.com" in tg
    # 评分行
    assert "🟢" in tg
    # 来源行
    assert "github" in tg
    # 标签: 空格替换为下划线，# 前缀
    assert "#LLM应用开发" in tg
    assert "#智能体工作流" in tg
    assert "#RAG" in tg


def test_json_to_telegram_escaping() -> None:
    """验证 Telegram 特殊字符完整转义。"""
    article = {
        **SAMPLE_ARTICLE,
        "title": "test_project (v2.0)",
        "summary": "使用 *bold* 和 _italic_ 以及 `code`",
    }
    tg = json_to_telegram(article)

    # ( 和 ) 应被转义
    assert "\\(v2\\.0\\)" in tg
    # * 和 _ 应被转义
    assert "\\*" in tg
    assert "\\_" in tg
    # ` 应被转义
    assert "\\`" in tg


def test_json_to_feishu() -> None:
    """验证飞书 interactive 卡片格式。"""
    card = json_to_feishu(SAMPLE_ARTICLE)

    assert card["msg_type"] == "interactive"
    assert "card" in card

    # header 染色
    assert card["card"]["header"]["template"] == "green"
    assert card["card"]["header"]["title"]["content"] == "langgenius/dify"

    # elements 非空
    elements = card["card"]["elements"]
    assert len(elements) > 0

    # 应包含摘要 markdown 元素
    md_elements = [
        e for e in elements if e.get("tag") == "markdown" and "content" in e
    ]
    assert any("Dify 是一个开源" in e["content"] for e in md_elements)

    # 应包含原文链接按钮
    action_elements = [e for e in elements if e.get("tag") == "action"]
    assert len(action_elements) == 1
    button = action_elements[0]["actions"][0]
    assert button["url"] == "https://github.com/langgenius/dify"


def test_json_to_feishu_color_by_score() -> None:
    """验证飞书卡片颜色随评分变化。"""
    high = json_to_feishu({**SAMPLE_ARTICLE, "relevance_score": 0.9})
    mid = json_to_feishu({**SAMPLE_ARTICLE, "relevance_score": 0.65})
    low = json_to_feishu({**SAMPLE_ARTICLE, "relevance_score": 0.3})
    assert high["card"]["header"]["template"] == "green"
    assert mid["card"]["header"]["template"] == "yellow"
    assert low["card"]["header"]["template"] == "red"


def test_generate_daily_digest_empty(tmp_path: Path) -> None:
    """验证当日无文章时的空消息返回。"""
    result = generate_daily_digest(knowledge_dir=tmp_path, date="2026-01-01")

    expected = "📭 2026-01-01 暂无新增知识条目"
    assert result["markdown"] == expected
    assert result["telegram"] == expected
    assert result["feishu"] == expected


def test_generate_daily_digest_with_articles(tmp_path: Path) -> None:
    """验证有文章时的简报生成。"""
    # 创建 3 篇文章，不同评分
    articles_data = [
        {
            **SAMPLE_ARTICLE,
            "id": "2026-04-11-001",
            "relevance_score": 0.95,
            "title": "高分文章",
        },
        {
            **SAMPLE_ARTICLE,
            "id": "2026-04-11-002",
            "relevance_score": 0.50,
            "title": "低分文章",
        },
        {
            **SAMPLE_ARTICLE,
            "id": "2026-04-11-003",
            "relevance_score": 0.75,
            "title": "中分文章",
        },
    ]

    for article in articles_data:
        file_path = tmp_path / f"{article['id']}.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(article, f, ensure_ascii=False)

    result = generate_daily_digest(knowledge_dir=tmp_path, date="2026-04-11", top_n=2)

    # Markdown 简报
    assert "每日 AI 知识简报" in result["markdown"]
    # 应按评分降序，高分文章在前
    md_lines = result["markdown"].split("\n")
    high_idx = next(i for i, l in enumerate(md_lines) if "高分文章" in l)
    mid_idx = next(i for i, l in enumerate(md_lines) if "中分文章" in l)
    assert high_idx < mid_idx
    # 低分文章不应出现 (top_n=2)
    assert "低分文章" not in result["markdown"]

    # Telegram 简报
    assert "高分文章" in result["telegram"]
    assert "中分文章" in result["telegram"]

    # 飞书卡片
    assert result["feishu"]["msg_type"] == "interactive"
    assert "card" in result["feishu"]
    # header 颜色应为最高分文章的颜色
    assert result["feishu"]["card"]["header"]["template"] == "green"


def test_generate_daily_digest_default_today(tmp_path: Path) -> None:
    """验证 date=None 默认今天 (UTC)。"""
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 无文件时应返回今天的日期
    result = generate_daily_digest(knowledge_dir=tmp_path)
    assert today in result["markdown"]


def test_generate_daily_digest_skips_invalid_json(tmp_path: Path) -> None:
    """验证跳过解析失败的 JSON 文件。"""
    # 写入一个有效文件
    valid = {**SAMPLE_ARTICLE, "id": "2026-04-11-001"}
    with open(tmp_path / "2026-04-11-001.json", "w", encoding="utf-8") as f:
        json.dump(valid, f, ensure_ascii=False)

    # 写入一个损坏文件
    with open(tmp_path / "2026-04-11-002.json", "w", encoding="utf-8") as f:
        f.write("{invalid json")

    result = generate_daily_digest(knowledge_dir=tmp_path, date="2026-04-11")
    # 应只处理有效文件
    assert "langgenius/dify" in result["markdown"]


if __name__ == "__main__":
    import tempfile

    # 运行所有测试
    test_get_url()
    test_get_score()
    test_get_date()
    test_score_emoji()
    test_score_feishu_color()
    test_escape_telegram()
    test_json_to_markdown()
    test_json_to_markdown_low_score()
    test_json_to_telegram()
    test_json_to_telegram_escaping()
    test_json_to_feishu()
    test_json_to_feishu_color_by_score()

    with tempfile.TemporaryDirectory() as tmp:
        test_generate_daily_digest_empty(Path(tmp))
        test_generate_daily_digest_with_articles(Path(tmp))
        test_generate_daily_digest_default_today(Path(tmp))
        test_generate_daily_digest_skips_invalid_json(Path(tmp))

    print("✓ formatter 所有测试通过")
