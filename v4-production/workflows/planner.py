"""Planner 节点：根据目标采集量生成采集策略。

三档策略：
- lite (target < 10): 轻量采集，高相关性门槛，单轮审核
- standard (10 <= target < 20): 标准采集，平衡质量与数量
- full (target >= 20): 全量采集，低门槛广撒网，多轮审核
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from workflows.state import KBState

logger = logging.getLogger(__name__)

# 环境变量键名
_ENV_TARGET_COUNT = "PLANNER_TARGET_COUNT"

# 默认目标采集量
_DEFAULT_TARGET_COUNT = 10


def plan_strategy(target_count: int | None = None) -> dict:
    """根据目标采集量生成采集策略字典。

    三档策略：
    - lite: target < 10，轻量快速，高相关性门槛
    - standard: 10 <= target < 20，平衡质量与覆盖面
    - full: target >= 20，全量采集，多轮迭代保证产出

    Args:
        target_count: 目标采集条目数。为 None 时从环境变量
            PLANNER_TARGET_COUNT 读取，默认 10。

    Returns:
        策略字典，包含 tier, target_count, per_source_limit,
        relevance_threshold, max_iterations, rationale 字段。
    """
    if target_count is None:
        env_val = os.environ.get(_ENV_TARGET_COUNT, "")
        try:
            target_count = int(env_val) if env_val.strip() else _DEFAULT_TARGET_COUNT
        except ValueError:
            logger.warning(
                "[Planner] 环境变量 %s=%r 无法解析为整数，使用默认值 %d",
                _ENV_TARGET_COUNT,
                env_val,
                _DEFAULT_TARGET_COUNT,
            )
            target_count = _DEFAULT_TARGET_COUNT

    # 确保 target_count 为正整数
    target_count = max(1, target_count)

    if target_count < 10:
        plan = {
            "tier": "lite",
            "target_count": target_count,
            "per_source_limit": 5,
            "relevance_threshold": 0.7,
            "max_iterations": 1,
            "rationale": (
                f"目标仅 {target_count} 条，采用轻量模式：每源限 5 条、"
                "相关性门槛 0.7 保证精准度、单轮审核快速产出。"
            ),
        }
    elif target_count < 20:
        plan = {
            "tier": "standard",
            "target_count": target_count,
            "per_source_limit": 10,
            "relevance_threshold": 0.5,
            "max_iterations": 2,
            "rationale": (
                f"目标 {target_count} 条，采用标准模式：每源限 10 条、"
                "相关性门槛 0.5 平衡质量与覆盖面、最多 2 轮审核。"
            ),
        }
    else:
        plan = {
            "tier": "full",
            "target_count": target_count,
            "per_source_limit": 20,
            "relevance_threshold": 0.4,
            "max_iterations": 3,
            "rationale": (
                f"目标 {target_count} 条，采用全量模式：每源限 20 条、"
                "相关性门槛 0.4 广泛采集、最多 3 轮审核迭代保证产出量。"
            ),
        }

    logger.info(
        "[Planner] 策略: %s (target=%d, per_source=%d, threshold=%.1f, iters=%d)",
        plan["tier"],
        plan["target_count"],
        plan["per_source_limit"],
        plan["relevance_threshold"],
        plan["max_iterations"],
    )
    return plan


def planner_node(state: KBState) -> dict:
    """Planner LangGraph 节点：生成采集策略并写入 state。

    Args:
        state: 当前工作流状态。

    Returns:
        包含 plan 字段的部分状态更新字典。
    """
    plan = plan_strategy()
    return {"plan": plan}
