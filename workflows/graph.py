"""LangGraph 工作流编排：完整 7 节点状态图。

工作流结构：
    plan → collect → analyze → review
                                 ↓
                      passed? ──→ organize → save → END
                         ↓
              iter < max_iterations → revise → review（循环改写）
                         ↓
              iter >= max_iterations → human_flag → END（人工介入）
"""

import logging
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中，支持 `python workflows/graph.py` 直接运行
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from langgraph.graph import END, StateGraph

from workflows.human_flag import human_flag_node
from workflows.nodes import (
    analyze_node,
    collect_node,
    organize_node,
    save_node,
)
from workflows.planner import planner_node
from workflows.reviewer import review_node
from workflows.reviser import revise_node
from workflows.state import KBState

logger = logging.getLogger(__name__)


def route_after_review(state: KBState) -> str:
    """审核结果 3 路路由：通过 / 修订 / 人工介入。

    路由逻辑（max_iterations 从 plan 中读取，不再硬编码）：
    - 审核通过 → "organize"（进入整理流程）
    - 未通过且 iteration < max_iterations → "revise"（LLM 改写后重新审核）
    - 未通过且 iteration >= max_iterations → "human_flag"（转人工，终止循环）

    Args:
        state: 当前工作流状态。

    Returns:
        下一个节点名称。
    """
    if state.get("review_passed", False):
        return "organize"

    plan = state.get("plan") or {}
    max_iter = int(plan.get("max_iterations", 3))
    iteration = state.get("iteration", 0)

    if iteration < max_iter:
        return "revise"
    return "human_flag"


def build_graph() -> object:
    """构建并编译 LangGraph 工作流。

    Returns:
        编译后的 LangGraph 应用实例，可调用 invoke/stream 执行。
    """
    graph = StateGraph(KBState)

    # 注册节点
    graph.add_node("plan", planner_node)
    graph.add_node("collect", collect_node)
    graph.add_node("analyze", analyze_node)
    graph.add_node("review", review_node)
    graph.add_node("revise", revise_node)
    graph.add_node("human_flag", human_flag_node)
    graph.add_node("organize", organize_node)
    graph.add_node("save", save_node)

    # 设置入口点
    graph.set_entry_point("plan")

    # 线性边：plan → collect → analyze → review
    graph.add_edge("plan", "collect")
    graph.add_edge("collect", "analyze")
    graph.add_edge("analyze", "review")

    # 条件边：review 之后 3 路分支
    graph.add_conditional_edges(
        "review",
        route_after_review,
        {
            "organize": "organize",
            "revise": "revise",
            "human_flag": "human_flag",
        },
    )

    # 修订循环：revise → review
    graph.add_edge("revise", "review")

    # 异常终点：human_flag → END
    graph.add_edge("human_flag", END)

    # 线性边：organize → save → END
    graph.add_edge("organize", "save")
    graph.add_edge("save", END)

    # 编译
    app = graph.compile()
    logger.info("工作流编译完成")
    return app


if __name__ == "__main__":
    # 未设置环境变量时默认用 lite 模式（快速测试）
    import os

    os.environ.setdefault("PLANNER_TARGET_COUNT", "5")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    logger.info("启动知识库采集工作流")

    app = build_graph()

    # 初始状态
    initial_state: KBState = {
        "plan": {},
        "sources": [],
        "analyses": [],
        "articles": [],
        "review_feedback": "",
        "review_passed": False,
        "iteration": 0,
        "needs_human_review": False,
        "cost_tracker": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "estimated_cost_usd": 0.0,
        },
    }

    # 流式执行，打印每个节点的关键输出
    for event in app.stream(initial_state):
        for node_name, node_output in event.items():
            logger.info("=" * 60)
            logger.info("节点完成: %s", node_name)

            if node_name == "plan":
                plan = node_output.get("plan", {})
                logger.info(
                    "  策略: %s (target=%s)",
                    plan.get("tier", "unknown"),
                    plan.get("target_count", "?"),
                )

            elif node_name == "collect":
                count = len(node_output.get("sources", []))
                logger.info("  采集到 %d 条原始数据", count)

            elif node_name == "analyze":
                count = len(node_output.get("analyses", []))
                logger.info("  分析完成 %d 条", count)

            elif node_name == "organize":
                count = len(node_output.get("articles", []))
                logger.info("  整理后 %d 条知识条目", count)

            elif node_name == "review":
                passed = node_output.get("review_passed", False)
                iteration = node_output.get("iteration", 0)
                feedback = node_output.get("review_feedback", "")
                logger.info(
                    "  第 %d 轮审核: %s",
                    iteration,
                    "通过" if passed else "未通过",
                )
                if feedback:
                    logger.info("  反馈: %s", feedback[:100])

            elif node_name == "revise":
                count = len(node_output.get("analyses", []))
                logger.info("  修订完成 %d 条", count)

            elif node_name == "human_flag":
                logger.warning("  ⚠ 转入人工审核")
                fb = node_output.get("review_feedback", "")
                if fb:
                    logger.info("  说明: %s", fb[:100])

            elif node_name == "save":
                count = len(node_output.get("articles", []))
                logger.info("  保存 %d 条到 knowledge/articles/", count)

    # 打印 Token 用量统计
    logger.info("=" * 60)
    logger.info("工作流执行完毕")
