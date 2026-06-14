"""LangGraph 工作流共享状态定义。

采用"报告式通信"原则：各节点通过结构化摘要传递信息，
而非传递原始数据，以降低 Token 消耗并提升可观测性。
"""

from typing import TypedDict


class KBState(TypedDict):
    """知识库工作流的共享状态。

    各字段均为结构化摘要，遵循报告式通信原则——
    节点间传递的是经过提炼的结构化结果，而非未加工的原始内容。
    """

    # 采集到的原始数据摘要列表
    # 每个 dict 包含: title, source, source_url, description, collected_at
    sources: list[dict]

    # LLM 分析后的结构化结果列表
    # 每个 dict 包含: title, summary, tags, category, relevance_score
    analyses: list[dict]

    # 格式化、去重后的知识条目列表
    # 每个 dict 遵循 knowledge/articles/ 下的 JSON Schema 规范
    articles: list[dict]

    # 审核反馈意见（由审核节点填写，供修订节点参考）
    review_feedback: str

    # 审核是否通过（True 表示可进入发布流程）
    review_passed: bool

    # 当前审核循环次数（最多 3 次，超出后强制通过并标记）
    iteration: int

    # 是否需要人工审核（HumanFlag 节点设为 True，表示审核循环超限）
    needs_human_review: bool

    # Token 用量追踪
    # 包含: prompt_tokens, completion_tokens, total_tokens, estimated_cost_usd
    cost_tracker: dict
