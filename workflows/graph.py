"""LangGraph 工作流编排：组装采集→分析→审核→整理→保存的状态图。

工作流结构：
    collect → analyze → review
                          ↓
               passed? ──→ organize → save → END
                  ↓
                False → analyze（回到分析节点修正）
"""

import logging
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中，支持 `python workflows/graph.py` 直接运行
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from langgraph.graph import END, StateGraph

from workflows.nodes import (
    analyze_node,
    collect_node,
    organize_node,
    save_node,
)
from workflows.reviewer import review_node
from workflows.state import KBState

logger = logging.getLogger(__name__)


def _review_router(state: KBState) -> str:
    """审核结果路由：根据 review_passed 决定下一步。

    Args:
        state: 当前工作流状态。

    Returns:
        "organize" 如果审核通过，否则 "analyze" 回到分析节点修正。
    """
    if state.get("review_passed", False):
        return "organize"
    return "analyze"


def build_graph() -> object:
    """构建并编译 LangGraph 工作流。

    Returns:
        编译后的 LangGraph 应用实例，可调用 invoke/stream 执行。
    """
    graph = StateGraph(KBState)

    # 注册节点
    graph.add_node("collect", collect_node)
    graph.add_node("analyze", analyze_node)
    graph.add_node("organize", organize_node)
    graph.add_node("review", review_node)
    graph.add_node("save", save_node)

    # 设置入口点
    graph.set_entry_point("collect")

    # 线性边：collect → analyze → review
    graph.add_edge("collect", "analyze")
    graph.add_edge("analyze", "review")

    # 条件边：review 之后根据 review_passed 分支
    graph.add_conditional_edges(
        "review",
        _review_router,
        {
            "organize": "organize",
            "analyze": "analyze",
        },
    )

    # 线性边：organize → save → END
    graph.add_edge("organize", "save")
    graph.add_edge("save", END)

    # 编译
    app = graph.compile()
    logger.info("工作流编译完成")
    return app


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    logger.info("启动知识库采集工作流")

    app = build_graph()

    # 初始状态
    initial_state: KBState = {
        "sources": [],
        "analyses": [],
        "articles": [],
        "review_feedback": "",
        "review_passed": False,
        "iteration": 0,
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

            if node_name == "collect":
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

            elif node_name == "save":
                count = len(node_output.get("articles", []))
                logger.info("  保存 %d 条到 knowledge/articles/", count)

    # 打印 Token 用量统计
    logger.info("=" * 60)
    logger.info("工作流执行完毕")
