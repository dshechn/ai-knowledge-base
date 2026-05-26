#!/usr/bin/env python3
"""MCP Knowledge Server — 本地知识库搜索服务。

通过 JSON-RPC 2.0 over stdio 协议提供以下工具：
  - search_articles(keyword, limit=5): 按关键词搜索文章标题和摘要
  - get_article(article_id): 按 ID 获取文章完整内容
  - knowledge_stats(): 返回统计信息（文章总数、来源分布、热门标签）

无第三方依赖，仅使用 Python 标准库。

Usage:
    python mcp_knowledge_server.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

SERVER_NAME = "knowledge-server"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"

ARTICLES_DIR = Path(__file__).resolve().parent / "knowledge" / "articles"

# ---------------------------------------------------------------------------
# 知识库加载
# ---------------------------------------------------------------------------


def load_articles() -> list[dict[str, Any]]:
    """加载 knowledge/articles/ 下所有 JSON 文件。"""
    articles: list[dict[str, Any]] = []
    if not ARTICLES_DIR.exists():
        return articles

    for filepath in sorted(ARTICLES_DIR.glob("*.json")):
        try:
            data = json.loads(filepath.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("id"):
                articles.append(data)
        except (json.JSONDecodeError, OSError):
            continue

    return articles


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------


def search_articles(keyword: str, limit: int = 5) -> list[dict[str, Any]]:
    """按关键词搜索文章标题和摘要。"""
    articles = load_articles()
    keyword_lower = keyword.lower()
    results: list[dict[str, Any]] = []

    for article in articles:
        title = article.get("title", "").lower()
        summary = article.get("summary", "").lower()
        tags = " ".join(article.get("tags", [])).lower()

        if keyword_lower in title or keyword_lower in summary or keyword_lower in tags:
            results.append({
                "id": article.get("id", ""),
                "title": article.get("title", ""),
                "source": article.get("source", ""),
                "summary": article.get("summary", "")[:200],
                "score": article.get("score", 0),
                "tags": article.get("tags", []),
            })

    # 按 score 降序排列
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results[:limit]


def get_article(article_id: str) -> dict[str, Any] | None:
    """按 ID 获取文章完整内容。"""
    articles = load_articles()
    for article in articles:
        if article.get("id") == article_id:
            return article
    return None


def knowledge_stats() -> dict[str, Any]:
    """返回知识库统计信息。"""
    articles = load_articles()

    if not articles:
        return {
            "total": 0,
            "sources": {},
            "top_tags": [],
            "avg_score": 0,
        }

    # 来源分布
    source_counter: Counter[str] = Counter()
    tag_counter: Counter[str] = Counter()
    total_score = 0.0

    for article in articles:
        source_counter[article.get("source", "unknown")] += 1
        for tag in article.get("tags", []):
            tag_counter[tag] += 1
        total_score += float(article.get("score", 0))

    return {
        "total": len(articles),
        "sources": dict(source_counter.most_common()),
        "top_tags": [{"tag": tag, "count": count} for tag, count in tag_counter.most_common(10)],
        "avg_score": round(total_score / len(articles), 1) if articles else 0,
    }


# ---------------------------------------------------------------------------
# MCP 工具定义
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "search_articles",
        "description": "按关键词搜索知识库文章的标题、摘要和标签，返回匹配结果列表",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "搜索关键词",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回结果数量上限，默认 5",
                    "default": 5,
                },
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "get_article",
        "description": "按文章 ID 获取完整内容",
        "inputSchema": {
            "type": "object",
            "properties": {
                "article_id": {
                    "type": "string",
                    "description": "文章唯一 ID",
                },
            },
            "required": ["article_id"],
        },
    },
    {
        "name": "knowledge_stats",
        "description": "返回知识库统计信息：文章总数、来源分布、热门标签、平均评分",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]

# ---------------------------------------------------------------------------
# JSON-RPC 2.0 处理
# ---------------------------------------------------------------------------


def make_response(id: Any, result: Any) -> dict[str, Any]:
    """构造 JSON-RPC 成功响应。"""
    return {"jsonrpc": "2.0", "id": id, "result": result}


def make_error(id: Any, code: int, message: str) -> dict[str, Any]:
    """构造 JSON-RPC 错误响应。"""
    return {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}


def handle_initialize(id: Any, _params: dict[str, Any]) -> dict[str, Any]:
    """处理 initialize 请求。"""
    return make_response(id, {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {
            "tools": {},
        },
        "serverInfo": {
            "name": SERVER_NAME,
            "version": SERVER_VERSION,
        },
    })


def handle_tools_list(id: Any, _params: dict[str, Any]) -> dict[str, Any]:
    """处理 tools/list 请求。"""
    return make_response(id, {"tools": TOOLS})


def handle_tools_call(id: Any, params: dict[str, Any]) -> dict[str, Any]:
    """处理 tools/call 请求。"""
    tool_name = params.get("name", "")
    arguments = params.get("arguments", {})

    if tool_name == "search_articles":
        keyword = arguments.get("keyword", "")
        limit = arguments.get("limit", 5)
        if not keyword:
            return make_error(id, -32602, "Missing required parameter: keyword")
        results = search_articles(keyword, limit)
        content = json.dumps(results, ensure_ascii=False, indent=2)
        return make_response(id, {
            "content": [{"type": "text", "text": content}],
        })

    elif tool_name == "get_article":
        article_id = arguments.get("article_id", "")
        if not article_id:
            return make_error(id, -32602, "Missing required parameter: article_id")
        article = get_article(article_id)
        if article is None:
            content = json.dumps({"error": f"Article not found: {article_id}"}, ensure_ascii=False)
        else:
            content = json.dumps(article, ensure_ascii=False, indent=2)
        return make_response(id, {
            "content": [{"type": "text", "text": content}],
        })

    elif tool_name == "knowledge_stats":
        stats = knowledge_stats()
        content = json.dumps(stats, ensure_ascii=False, indent=2)
        return make_response(id, {
            "content": [{"type": "text", "text": content}],
        })

    else:
        return make_error(id, -32601, f"Unknown tool: {tool_name}")


# 方法路由表
METHOD_HANDLERS = {
    "initialize": handle_initialize,
    "tools/list": handle_tools_list,
    "tools/call": handle_tools_call,
}


def handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    """处理单个 JSON-RPC 请求。"""
    method = request.get("method", "")
    id = request.get("id")
    params = request.get("params", {})

    # 通知（无 id）：不返回响应
    if id is None:
        # notifications/initialized 等通知直接忽略
        return None

    handler = METHOD_HANDLERS.get(method)
    if handler is None:
        return make_error(id, -32601, f"Method not found: {method}")

    return handler(id, params)


# ---------------------------------------------------------------------------
# stdio 主循环
# ---------------------------------------------------------------------------


def main() -> None:
    """JSON-RPC over stdio 主循环。"""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            error = make_error(None, -32700, "Parse error")
            sys.stdout.write(json.dumps(error) + "\n")
            sys.stdout.flush()
            continue

        response = handle_request(request)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
