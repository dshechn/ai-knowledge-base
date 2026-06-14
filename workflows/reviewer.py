"""审核节点：5 维度加权评分，决定 analyses 是否通过质量门槛。

评分维度及权重：
- summary_quality (摘要质量): 25%
- technical_depth (技术深度): 25%
- relevance (相关性): 20%
- originality (原创性): 15%
- formatting (格式规范): 15%

加权总分 >= 7.0 为通过。
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from workflows.model_client import accumulate_usage, chat_json
from workflows.state import KBState

logger = logging.getLogger(__name__)

# 评分维度权重配置
DIMENSION_WEIGHTS: dict[str, float] = {
    "summary_quality": 0.25,
    "technical_depth": 0.25,
    "relevance": 0.20,
    "originality": 0.15,
    "formatting": 0.15,
}

# 通过阈值
PASS_THRESHOLD: float = 7.0

# 最大审核条目数（控 token 消耗）
MAX_REVIEW_ITEMS: int = 5

# 审核用 temperature（评分一致性）
REVIEW_TEMPERATURE: float = 0.1

# 最大迭代次数
MAX_ITERATIONS: int = 3


def _compute_weighted_score(scores: dict[str, float]) -> float:
    """根据维度权重计算加权总分。

    Args:
        scores: 各维度得分字典，key 为维度名称，value 为 1-10 分。

    Returns:
        加权总分（0-10 范围）。
    """
    total = 0.0
    for dimension, weight in DIMENSION_WEIGHTS.items():
        score = scores.get(dimension, 0.0)
        # 防御：LLM 可能返回非数值类型（如 dict、list）
        if not isinstance(score, (int, float)):
            logger.warning(
                "[ReviewNode] 维度 %s 的值不是数值: %s，按 5.0 计",
                dimension,
                type(score).__name__,
            )
            score = 5.0
        # 将分值限制在 1-10 范围内
        clamped = max(1.0, min(10.0, float(score)))
        total += clamped * weight
    return round(total, 2)


def review_node(state: KBState) -> dict:
    """审核节点：对 analyses 进行 5 维度 LLM 评分，代码重算加权总分。

    评分维度（每维 1-10 分）：
    - summary_quality: 摘要是否准确、完整、有信息量
    - technical_depth: 技术内容深度与专业性
    - relevance: 与 AI/LLM/Agent 领域的相关性
    - originality: 内容的原创性和独特视角
    - formatting: 格式规范性（字段完整、标签合规等）

    加权总分 >= 7.0 为通过。
    iteration >= MAX_ITERATIONS 时强制通过，避免无限循环。
    LLM 调用失败时自动通过，不阻塞流程。

    Args:
        state: 当前工作流状态。

    Returns:
        包含 review_passed, review_feedback, iteration, cost_tracker
        的部分状态更新。
    """
    plan = state.get("plan") or {}
    max_iterations = int(plan.get("max_iterations", MAX_ITERATIONS))

    iteration = state.get("iteration", 0) + 1
    analyses = state.get("analyses", [])
    tracker = dict(state.get("cost_tracker") or {})

    logger.info(
        "[ReviewNode] 第 %d 轮审核，共 %d 条 analyses (max_iterations=%d)",
        iteration,
        len(analyses),
        max_iterations,
    )

    # 强制通过：避免无限循环
    if iteration >= max_iterations:
        logger.info("[ReviewNode] 已达最大迭代次数 (%d)，强制通过", max_iterations)
        return {
            "review_passed": True,
            "review_feedback": "已达最大审核次数，强制通过。",
            "iteration": iteration,
            "cost_tracker": tracker,
        }

    # 无数据时直接通过
    if not analyses:
        logger.warning("[ReviewNode] analyses 为空，自动通过")
        return {
            "review_passed": True,
            "review_feedback": "无待审核条目。",
            "iteration": iteration,
            "cost_tracker": tracker,
        }

    # 只审核前 MAX_REVIEW_ITEMS 条（控 token 消耗）
    review_batch = analyses[:MAX_REVIEW_ITEMS]

    system_prompt = (
        "你是 AI/LLM 领域知识库的质量审核员。\n"
        "你将收到一批知识分析条目，请对这批条目进行**整体**评分（不要逐条打分）。\n\n"
        "评分维度（每维 1-10 的整数）：\n"
        "1. summary_quality：摘要是否准确、完整、有信息量\n"
        "2. technical_depth：技术内容的专业性和深度\n"
        "3. relevance：与 AI/LLM/Agent 领域的契合程度\n"
        "4. originality：内容的独特视角和新颖程度\n"
        "5. formatting：字段完整性、标签格式、分类合理性\n\n"
        "严格按以下 JSON 格式输出，scores 的每个值必须是整数，不要嵌套对象：\n"
        "```\n"
        '{"scores": {"summary_quality": 8, "technical_depth": 7, '
        '"relevance": 9, "originality": 6, "formatting": 8}, '
        '"feedback": "整体改进建议"}\n'
        "```\n"
        "请直接输出 JSON，不要包含 markdown 代码块标记。"
    )

    # 构造审核输入：只发送关键字段，避免 Token 浪费
    items_for_review = [
        {
            "title": item.get("title", ""),
            "summary": item.get("summary", "")[:300],
            "tags": item.get("tags", []),
            "category": item.get("category", ""),
            "relevance_score": item.get("relevance_score", 0),
        }
        for item in review_batch
    ]

    prompt = (
        f"以下是本批次 {len(items_for_review)} 条知识分析条目"
        f"（摘要截取前 300 字）：\n"
        f"{json.dumps(items_for_review, ensure_ascii=False, indent=2)}"
    )

    # LLM 调用，失败时自动通过（不阻塞流程）
    try:
        result, usage = chat_json(
            prompt, system=system_prompt, temperature=REVIEW_TEMPERATURE
        )
        accumulate_usage(tracker, usage)
    except Exception as exc:
        logger.error("[ReviewNode] LLM 调用失败: %s，自动通过", exc)
        return {
            "review_passed": True,
            "review_feedback": f"LLM 审核调用异常，自动通过: {exc}",
            "iteration": iteration,
            "cost_tracker": tracker,
        }

    # 解析失败时自动通过
    if not result or not isinstance(result, dict):
        logger.warning("[ReviewNode] LLM 返回结果解析失败，自动通过")
        return {
            "review_passed": True,
            "review_feedback": "审核结果解析失败，自动通过。",
            "iteration": iteration,
            "cost_tracker": tracker,
        }

    # 提取各维度分数
    raw_scores = result.get("scores", {})
    if not isinstance(raw_scores, dict):
        logger.warning("[ReviewNode] scores 字段格式异常，自动通过")
        return {
            "review_passed": True,
            "review_feedback": "审核 scores 格式异常，自动通过。",
            "iteration": iteration,
            "cost_tracker": tracker,
        }

    # 用代码重算加权总分（不信任模型算术）
    weighted_score = _compute_weighted_score(raw_scores)
    passed = weighted_score >= PASS_THRESHOLD

    feedback = result.get("feedback", "")
    # 在 feedback 中附加评分明细
    score_detail = (
        f"[评分明细] "
        f"摘要质量={raw_scores.get('summary_quality', 'N/A')}, "
        f"技术深度={raw_scores.get('technical_depth', 'N/A')}, "
        f"相关性={raw_scores.get('relevance', 'N/A')}, "
        f"原创性={raw_scores.get('originality', 'N/A')}, "
        f"格式规范={raw_scores.get('formatting', 'N/A')}, "
        f"加权总分={weighted_score}/10.0"
    )
    full_feedback = f"{feedback}\n{score_detail}" if feedback else score_detail

    logger.info(
        "[ReviewNode] 审核结果: %s (加权总分 %.2f, 阈值 %.1f)",
        "通过" if passed else "未通过",
        weighted_score,
        PASS_THRESHOLD,
    )

    return {
        "review_passed": passed,
        "review_feedback": full_feedback,
        "iteration": iteration,
        "cost_tracker": tracker,
    }
