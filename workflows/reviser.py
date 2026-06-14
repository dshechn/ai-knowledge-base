"""修订节点：根据审核反馈对 analyses 进行改写优化。

当 review_node 给出未通过的反馈后，revise_node 读取 feedback 并注入
修改 prompt，调用 LLM 对每条 analysis 进行创造性改写，提升质量。
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

# 修订用 temperature（允许创造性改写）
REVISE_TEMPERATURE: float = 0.4


def revise_node(state: KBState) -> dict:
    """修订节点：结合审核反馈改写 analyses，提升内容质量。

    读取 state["analyses"] 和 state["review_feedback"]，将反馈注入
    修改 prompt，调用 LLM 返回优化后的 analyses 列表。

    跳过条件：
    - analyses 为空
    - review_feedback 为空

    Args:
        state: 当前工作流状态。

    Returns:
        包含 analyses 和 cost_tracker 的部分状态更新字典。
        跳过时返回空字典 {}。
    """
    analyses = state.get("analyses", [])
    feedback = state.get("review_feedback", "")
    tracker = dict(state.get("cost_tracker") or {})

    # 跳过条件：analyses 或 feedback 为空
    if not analyses or not feedback:
        logger.info(
            "[ReviseNode] 跳过修订（analyses=%d 条, feedback=%s）",
            len(analyses),
            "有" if feedback else "空",
        )
        return {}

    logger.info(
        "[ReviseNode] 开始修订 %d 条 analyses，基于审核反馈",
        len(analyses),
    )

    system_prompt = (
        "你是 AI/LLM 领域知识库的内容优化专家。\n"
        "你的任务是根据审核反馈对知识分析条目进行改写优化。\n\n"
        "改写要求：\n"
        "1. 保持原始条目的核心信息不变（title、source_url 等元数据不修改）\n"
        "2. 根据反馈重点改进 summary（摘要质量、技术深度、信息量）\n"
        "3. 优化 tags（确保小写英文、覆盖关键技术点）\n"
        "4. 校正 category（framework/model/paper/tool/tutorial）\n"
        "5. 重新评估 relevance_score（0.0-1.0，与 AI/LLM/Agent 领域相关性）\n\n"
        "请输出 JSON 数组，每个元素包含完整的改写后条目，字段与输入一致。\n"
        "请直接输出 JSON 数组，不要包含 markdown 代码块标记。"
    )

    prompt = (
        f"## 审核反馈\n\n{feedback}\n\n"
        f"## 待修订条目（共 {len(analyses)} 条）\n\n"
        f"{json.dumps(analyses, ensure_ascii=False, indent=2)}\n\n"
        "请根据上述审核反馈对每条条目进行针对性改写优化，"
        "输出改写后的完整 JSON 数组。"
    )

    try:
        result, usage = chat_json(
            prompt, system=system_prompt, temperature=REVISE_TEMPERATURE
        )
        accumulate_usage(tracker, usage)
    except Exception as exc:
        logger.error("[ReviseNode] LLM 调用失败: %s，保留原始 analyses", exc)
        return {"analyses": analyses, "cost_tracker": tracker}

    # 解析校验：确保返回有效的列表
    if not result or not isinstance(result, list):
        logger.warning(
            "[ReviseNode] LLM 返回结果非列表，保留原始 analyses"
        )
        return {"analyses": analyses, "cost_tracker": tracker}

    # 过滤无效条目（必须是 dict 且含 title 字段）
    improved = [
        item for item in result
        if isinstance(item, dict) and item.get("title")
    ]

    if not improved:
        logger.warning("[ReviseNode] 修订后无有效条目，保留原始 analyses")
        return {"analyses": analyses, "cost_tracker": tracker}

    logger.info(
        "[ReviseNode] 修订完成，输出 %d 条（原始 %d 条）",
        len(improved),
        len(analyses),
    )

    return {"analyses": improved, "cost_tracker": tracker}
