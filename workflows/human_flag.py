"""HumanFlag 节点：审核循环超限时的异常终点。

当 analyses 经过 max_iterations 轮审核仍未通过质量门槛时，
说明问题不在"质量改写"而在"数据本身"，需要人工判断。

本节点将问题条目写入独立的 knowledge/pending_review/ 目录，
不污染主知识库（knowledge/articles/），确保数据隔离。
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# 确保项目根目录在 sys.path 中
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from workflows.state import KBState

logger = logging.getLogger(__name__)

# 待审目录（相对于项目根目录）
PENDING_REVIEW_DIR: Path = Path(_project_root) / "knowledge" / "pending_review"


def human_flag_node(state: KBState) -> dict:
    """审核循环超过上限时的兜底节点——写入 pending_review/ 目录等待人工处理。

    将未通过审核的 analyses 连同迭代次数、最后一次反馈等上下文信息
    序列化到 knowledge/pending_review/ 下的 JSON 文件中。

    Args:
        state: 当前工作流状态。

    Returns:
        包含 review_passed=True 的部分状态更新（终止循环），
        以及 cost_tracker 透传。
    """
    analyses = state.get("analyses", [])
    iteration = state.get("iteration", 0)
    feedback = state.get("review_feedback", "")
    tracker = dict(state.get("cost_tracker") or {})

    logger.warning(
        "[HumanFlag] 达到 %d 次审核仍未通过，转入人工审核",
        iteration,
    )
    if feedback:
        logger.info("[HumanFlag] 最后反馈: %s", feedback[:200])

    # 创建 pending_review 目录（幂等）
    PENDING_REVIEW_DIR.mkdir(parents=True, exist_ok=True)

    # 生成带时间戳的文件名，避免冲突
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filepath = PENDING_REVIEW_DIR / f"pending-{timestamp}.json"

    # 序列化上下文信息
    pending_data = {
        "timestamp": timestamp,
        "iterations_used": iteration,
        "last_feedback": feedback,
        "analyses_count": len(analyses),
        "analyses": analyses,
    }

    try:
        filepath.write_text(
            json.dumps(pending_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("[HumanFlag] 已保存到 %s", filepath)
    except OSError as exc:
        logger.error("[HumanFlag] 写入文件失败: %s", exc)

    # 返回 review_passed=True 终止审核循环，避免无限回环
    # analyses 清空，防止后续节点处理有问题的数据
    return {
        "analyses": [],
        "review_passed": True,
        "needs_human_review": True,
        "review_feedback": f"已转入人工审核（{iteration} 次未通过），文件: {filepath.name}",
        "cost_tracker": tracker,
    }
