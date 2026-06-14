"""LangGraph 工作流节点函数。

每个节点是纯函数：接收 KBState，返回 dict（部分状态更新）。
节点通过 model_client 调用 LLM，遵循报告式通信原则。
"""

import json
import logging
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# 确保项目根目录在 sys.path 中
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from workflows.model_client import accumulate_usage, chat, chat_json
from workflows.state import KBState

logger = logging.getLogger(__name__)

# 知识条目存储目录
ARTICLES_DIR = Path("knowledge/articles")

# GitHub Search API 相关常量
GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
GITHUB_SEARCH_QUERY = "AI OR LLM OR agent OR large-language-model"
GITHUB_SEARCH_PARAMS = "?" + urllib.parse.urlencode({
    "q": GITHUB_SEARCH_QUERY,
    "sort": "updated",
    "order": "desc",
    "per_page": "2",
})


def collect_node(state: KBState) -> dict:
    """采集节点：调用 GitHub Search API 获取 AI 相关仓库信息。

    Args:
        state: 当前工作流状态。

    Returns:
        包含 sources 列表的部分状态更新。
    """
    logger.info("[CollectNode] 开始采集 GitHub AI 相关仓库")

    url = f"{GITHUB_SEARCH_URL}{GITHUB_SEARCH_PARAMS}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "AI-Knowledge-Base-Collector/1.0",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        logger.error("[CollectNode] API 请求失败: %s", exc)
        return {"sources": []}

    now = datetime.now(timezone.utc).isoformat()
    sources: list[dict] = []

    for repo in data.get("items", []):
        sources.append({
            "title": repo.get("full_name", ""),
            "source": "github_trending",
            "source_url": repo.get("html_url", ""),
            "description": repo.get("description", "") or "",
            "stars": repo.get("stargazers_count", 0),
            "language": repo.get("language", ""),
            "collected_at": now,
        })

    logger.info("[CollectNode] 采集完成，共 %d 条", len(sources))
    return {"sources": sources}


def analyze_node(state: KBState) -> dict:
    """分析节点：用 LLM 对每条数据生成中文摘要、标签、评分。

    Args:
        state: 当前工作流状态。

    Returns:
        包含 analyses 列表和 cost_tracker 更新的部分状态。
    """
    logger.info("[AnalyzeNode] 开始分析 %d 条数据", len(state["sources"]))

    tracker = dict(state.get("cost_tracker") or {})
    analyses: list[dict] = []

    system_prompt = (
        "你是一位 AI/LLM 领域的技术分析师。"
        "请对以下 GitHub 仓库信息进行分析，输出 JSON 格式，包含：\n"
        "- title: 项目名称\n"
        "- summary: 200-500 字的中文技术摘要，说明项目用途、核心特性和技术亮点\n"
        "- tags: 标签列表（小写英文，3-5 个）\n"
        "- category: 分类，从 framework/model/paper/tool/tutorial 中选一个\n"
        "- relevance_score: 与 AI/LLM/Agent 领域的相关性评分（0.0-1.0）\n"
        "请直接输出 JSON，不要包含 markdown 代码块标记。"
    )

    for source in state["sources"]:
        prompt = (
            f"仓库名称: {source['title']}\n"
            f"描述: {source['description']}\n"
            f"语言: {source.get('language', 'N/A')}\n"
            f"Stars: {source.get('stars', 0)}\n"
            f"链接: {source['source_url']}"
        )

        result, usage = chat_json(prompt, system=system_prompt)
        accumulate_usage(tracker, usage)

        if result:
            # 补充来源信息
            result["source"] = source["source"]
            result["source_url"] = source["source_url"]
            result["collected_at"] = source["collected_at"]
            analyses.append(result)
        else:
            logger.warning(
                "[AnalyzeNode] 解析失败，跳过: %s", source["title"]
            )

    logger.info("[AnalyzeNode] 分析完成，成功 %d 条", len(analyses))
    return {"analyses": analyses, "cost_tracker": tracker}


def organize_node(state: KBState) -> dict:
    """整理节点：过滤低分条目、去重、根据审核反馈修正。

    处理逻辑：
    1. 过滤 relevance_score < 0.6 的条目
    2. 按 source_url 去重（保留最新）
    3. 若有审核反馈（iteration > 0），调用 LLM 定向修正

    Args:
        state: 当前工作流状态。

    Returns:
        包含 articles 列表和 cost_tracker 更新的部分状态。
    """
    logger.info("[OrganizeNode] 开始整理，当前迭代: %d", state["iteration"])

    tracker = dict(state.get("cost_tracker") or {})
    analyses = state["analyses"]

    # 1. 过滤低分条目
    filtered = [
        item for item in analyses if item.get("relevance_score", 0) >= 0.6
    ]
    logger.info(
        "[OrganizeNode] 过滤后剩余 %d 条（原 %d 条）",
        len(filtered),
        len(analyses),
    )

    # 2. 按 source_url 去重（保留后出现的，即更新的）
    seen_urls: dict[str, dict] = {}
    for item in filtered:
        url = item.get("source_url", "")
        if url:
            seen_urls[url] = item
    deduplicated = list(seen_urls.values())

    logger.info(
        "[OrganizeNode] 去重后剩余 %d 条", len(deduplicated)
    )

    # 3. 若有审核反馈，调用 LLM 定向修正
    articles = deduplicated
    feedback = state.get("review_feedback", "")
    iteration = state.get("iteration", 0)

    if iteration > 0 and feedback:
        logger.info("[OrganizeNode] 根据审核反馈进行 LLM 修正")
        system_prompt = (
            "你是知识库质量审核修正专家。"
            "请根据审核反馈意见，对以下知识条目列表进行定向修改。\n"
            "只修改反馈中指出的问题，保持其他内容不变。\n"
            "输出修正后的完整 JSON 数组。"
        )
        prompt = (
            f"审核反馈:\n{feedback}\n\n"
            f"当前条目列表:\n{json.dumps(articles, ensure_ascii=False, indent=2)}"
        )

        revised, usage = chat_json(prompt, system=system_prompt)
        accumulate_usage(tracker, usage)

        if isinstance(revised, list):
            articles = revised
            logger.info("[OrganizeNode] LLM 修正完成")
        else:
            logger.warning("[OrganizeNode] LLM 修正结果格式异常，保留原数据")

    # 4. 格式化为标准知识条目
    now = datetime.now(timezone.utc).strftime("%Y%m%d")
    formatted_articles: list[dict] = []

    for idx, item in enumerate(articles, start=1):
        article = {
            "id": f"{now}-github-{idx:03d}",
            "title": item.get("title", ""),
            "source": item.get("source", "github_trending"),
            "source_url": item.get("source_url", ""),
            "published_at": None,
            "collected_at": item.get("collected_at", ""),
            "summary": item.get("summary", ""),
            "tags": item.get("tags", []),
            "category": item.get("category", "tool"),
            "relevance_score": item.get("relevance_score", 0.0),
            "status": "draft",
            "distributed_to": [],
        }
        formatted_articles.append(article)

    logger.info("[OrganizeNode] 整理完成，输出 %d 条知识条目", len(formatted_articles))
    return {"articles": formatted_articles, "cost_tracker": tracker}


def review_node(state: KBState) -> dict:
    """审核节点：LLM 四维度评分，决定是否通过。

    评分维度：摘要质量、标签准确性、分类合理性、一致性。
    iteration >= 2 时强制通过，避免无限循环。

    Args:
        state: 当前工作流状态。

    Returns:
        包含审核结果和迭代计数的部分状态更新。
    """
    iteration = state.get("iteration", 0) + 1
    logger.info("[ReviewNode] 第 %d 轮审核，共 %d 条", iteration, len(state["articles"]))

    # 强制通过：避免无限循环
    if iteration >= 3:
        logger.info("[ReviewNode] 已达最大迭代次数，强制通过")
        return {
            "review_passed": True,
            "review_feedback": "已达最大审核次数，强制通过。",
            "iteration": iteration,
        }

    tracker = dict(state.get("cost_tracker") or {})

    system_prompt = (
        "你是知识库质量审核员。请从四个维度评估以下知识条目列表：\n"
        "1. 摘要质量（summary_score）：摘要是否准确、完整、有信息量\n"
        "2. 标签准确性（tags_score）：标签是否贴切、覆盖核心概念\n"
        "3. 分类合理性（category_score）：分类是否准确\n"
        "4. 一致性（consistency_score）：各条目间风格、格式是否统一\n\n"
        "请输出 JSON 格式：\n"
        "{\n"
        '  "passed": true/false,  // 总体是否通过（overall_score >= 0.7 为通过）\n'
        '  "overall_score": 0.0-1.0,\n'
        '  "feedback": "具体改进建议，若通过则写简要确认",\n'
        '  "scores": {\n'
        '    "summary_score": 0.0-1.0,\n'
        '    "tags_score": 0.0-1.0,\n'
        '    "category_score": 0.0-1.0,\n'
        '    "consistency_score": 0.0-1.0\n'
        "  }\n"
        "}\n"
        "请直接输出 JSON，不要包含 markdown 代码块标记。"
    )

    # 只发送摘要信息给 LLM，避免 Token 浪费
    articles_summary = [
        {
            "title": a.get("title", ""),
            "summary": a.get("summary", "")[:200],
            "tags": a.get("tags", []),
            "category": a.get("category", ""),
            "relevance_score": a.get("relevance_score", 0),
        }
        for a in state["articles"]
    ]

    prompt = (
        f"以下是本批次 {len(articles_summary)} 条知识条目（摘要截取前 200 字）：\n"
        f"{json.dumps(articles_summary, ensure_ascii=False, indent=2)}"
    )

    result, usage = chat_json(prompt, system=system_prompt)
    accumulate_usage(tracker, usage)

    if not result or not isinstance(result, dict):
        logger.warning("[ReviewNode] LLM 审核结果异常，默认不通过")
        return {
            "review_passed": False,
            "review_feedback": "审核结果解析失败，请重新审核。",
            "iteration": iteration,
            "cost_tracker": tracker,
        }

    passed = bool(result.get("passed", False))
    feedback = result.get("feedback", "")
    overall_score = result.get("overall_score", 0.0)

    logger.info(
        "[ReviewNode] 审核结果: %s (得分 %.2f)",
        "通过" if passed else "未通过",
        overall_score,
    )

    return {
        "review_passed": passed,
        "review_feedback": feedback,
        "iteration": iteration,
        "cost_tracker": tracker,
    }


def save_node(state: KBState) -> dict:
    """保存节点：将 articles 写入 knowledge/articles/ 目录。

    同时更新 index.json 索引文件，记录所有条目的 id、title、status。

    Args:
        state: 当前工作流状态。

    Returns:
        包含状态更新的 articles（status 标记为 reviewed）。
    """
    logger.info("[SaveNode] 开始保存 %d 条知识条目", len(state["articles"]))

    # 确保目录存在
    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)

    saved_articles: list[dict] = []

    for article in state["articles"]:
        # 更新状态为 reviewed
        article_to_save = {**article, "status": "reviewed"}
        file_path = ARTICLES_DIR / f"{article_to_save['id']}.json"

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(article_to_save, f, ensure_ascii=False, indent=2)

        saved_articles.append(article_to_save)
        logger.info("[SaveNode] 已保存: %s", file_path.name)

    # 更新 index.json 索引文件
    index_path = ARTICLES_DIR / "index.json"
    existing_index: list[dict] = []

    if index_path.exists():
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                existing_index = json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning("[SaveNode] index.json 读取失败，将重建索引")

    # 合并索引（按 id 去重，新条目覆盖旧条目）
    index_map: dict[str, dict] = {
        entry["id"]: entry for entry in existing_index
    }
    for article in saved_articles:
        index_map[article["id"]] = {
            "id": article["id"],
            "title": article["title"],
            "source": article["source"],
            "category": article["category"],
            "relevance_score": article["relevance_score"],
            "status": article["status"],
            "collected_at": article["collected_at"],
        }

    # 按 collected_at 倒序排列
    updated_index = sorted(
        index_map.values(),
        key=lambda x: x.get("collected_at", ""),
        reverse=True,
    )

    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(updated_index, f, ensure_ascii=False, indent=2)

    logger.info(
        "[SaveNode] 保存完成，索引共 %d 条记录", len(updated_index)
    )
    return {"articles": saved_articles}
