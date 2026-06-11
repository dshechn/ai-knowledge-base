"""验证 KBState 定义的完整性和默认实例化。"""

from workflows.state import KBState

# 期望的字段及其类型描述
EXPECTED_FIELDS: dict[str, str] = {
    "sources": "list[dict]",
    "analyses": "list[dict]",
    "articles": "list[dict]",
    "review_feedback": "str",
    "review_passed": "bool",
    "iteration": "int",
    "cost_tracker": "dict",
}


def make_initial_state() -> KBState:
    """创建一个符合 schema 的初始状态实例。"""
    return KBState(
        sources=[],
        analyses=[],
        articles=[],
        review_feedback="",
        review_passed=False,
        iteration=0,
        cost_tracker={
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "estimated_cost_usd": 0.0,
        },
    )


def test_field_completeness() -> None:
    """确保 KBState 包含所有预期字段且无多余字段。"""
    annotations = KBState.__annotations__
    assert set(annotations.keys()) == set(EXPECTED_FIELDS.keys()), (
        f"字段不匹配: {set(annotations.keys()) ^ set(EXPECTED_FIELDS.keys())}"
    )


def test_instance_creation() -> None:
    """确保初始状态可以正常创建并访问。"""
    state = make_initial_state()
    assert state["iteration"] == 0
    assert state["review_passed"] is False
    assert isinstance(state["sources"], list)
    assert state["cost_tracker"]["total_tokens"] == 0


if __name__ == "__main__":
    test_field_completeness()
    test_instance_creation()

    # 打印摘要信息
    annotations = KBState.__annotations__
    print("KBState 字段：")
    for name, type_hint in annotations.items():
        print(f"  {name}: {type_hint}")
    print(f"\n共 {len(annotations)} 个字段")

    state = make_initial_state()
    print(f"实例创建成功，iteration = {state['iteration']}")
    print("✓ 所有检查通过")
