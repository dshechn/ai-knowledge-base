"""AI 知识库分析质量评估测试。"""

from __future__ import annotations

import json
import os
import re
import sys
import warnings
from pathlib import Path
from typing import Any

import pytest

try:
    from dotenv import load_dotenv
except ImportError:

    def load_dotenv(dotenv_path: str | Path = ".env") -> bool:
        """在未安装 python-dotenv 时加载简单 KEY=VALUE 格式的 .env。"""
        path = Path(dotenv_path)
        if not path.exists():
            return False

        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"\''))
        return True


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")
warnings.filterwarnings("ignore", category=pytest.PytestUnknownMarkWarning)


PROVIDER_KEY_ENV: dict[str, str] = {
    "zhipu": "ZHIPU_API_KEY",
    "qwen": "QWEN_API_KEY",
}


def _sync_provider_key_from_llm_api_key() -> None:
    """将通用 LLM_API_KEY 同步到当前 provider 对应的环境变量。"""
    llm_api_key = os.getenv("LLM_API_KEY")
    if not llm_api_key:
        return

    provider = os.getenv("LLM_PROVIDER", "zhipu").lower().strip()
    provider_key_env = PROVIDER_KEY_ENV.get(provider)
    if provider_key_env:
        os.environ.setdefault(provider_key_env, llm_api_key)


_sync_provider_key_from_llm_api_key()


def _has_llm_api_key() -> bool:
    """判断当前环境是否具备运行 LLM 测试的 API Key。"""
    provider = os.getenv("LLM_PROVIDER", "zhipu").lower().strip()
    provider_key_env = PROVIDER_KEY_ENV.get(provider, "")
    return bool(os.getenv("LLM_API_KEY") or os.getenv(provider_key_env))


EVAL_CASES: list[dict[str, Any]] = [
    {
        "name": "positive_technical_article",
        "input": (
            "LangGraph 发布新版本，增强了多 Agent 工作流编排能力，"
            "支持状态图、条件路由、人类反馈节点和工具调用追踪。"
            "该更新适合构建可观测、可恢复的企业级 AI Agent 系统。"
        ),
        "expected": {
            "summary_min_chars": 20,
            "keywords_min_count": 2,
            "relevance_min": 0.6,
            "filter_values": [False],
        },
    },
    {
        "name": "negative_irrelevant_content",
        "input": (
            "今天晚餐准备番茄炒蛋和米饭，饭后去公园散步，"
            "顺便购买洗衣液和水果。"
        ),
        "expected": {
            "summary_min_chars": 0,
            "keywords_min_count": 0,
            "relevance_max": 0.4,
            "filter_values": [True],
        },
    },
    {
        "name": "boundary_very_short_input",
        "input": "AI",
        "expected": {
            "summary_min_chars": 0,
            "keywords_min_count": 0,
            "relevance_min": 0.0,
            "relevance_max": 1.0,
            "filter_values": [True, False],
        },
    },
]


ANALYSIS_SYSTEM_PROMPT = """你是 AI 技术知识库评估器。
请判断输入内容是否适合进入 AI/LLM/Agent 技术知识库。
评分规则：
- 只有明确涉及 AI、LLM、Agent、机器学习模型、开发框架、论文、工具或工程实践时，才算高相关。
- 日常生活、饮食、购物、娱乐等无关内容必须 should_filter=true，relevance_score 必须 <= 0.2。
- 内容过短或信息不足时不得崩溃，可给出低置信摘要并按信息量判断是否过滤。
必须只输出 JSON，不要输出 markdown。JSON 字段：
summary: 中文摘要字符串；
keywords: 关键词数组；
relevance_score: 0 到 1 的相关性分数；
should_filter: 是否应过滤的布尔值。
"""


JUDGE_SYSTEM_PROMPT = """你是严格但公平的 LLM-as-Judge。
请评估分析结果是否忠实反映输入内容，并适合 AI 技术知识库。
必须只输出 JSON，不要输出 markdown。JSON 字段：score，整数 1 到 10。
"""


def _extract_json(text: str) -> dict[str, Any]:
    """从 LLM 响应中提取 JSON 对象。"""
    content = text.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            raise
        data = json.loads(match.group(0))

    assert isinstance(data, dict)
    return data


def _analyze_with_llm(article: str) -> dict[str, Any]:
    """调用 LLM 对知识条目候选内容进行分析。"""
    from workflows.model_client import chat

    text, usage = chat(article, system=ANALYSIS_SYSTEM_PROMPT, temperature=0.1)
    result = _extract_json(text)
    result["usage"] = usage
    return result


def _judge_with_llm(article: str, analysis: dict[str, Any]) -> int:
    """调用 LLM-as-Judge 对分析结果打分。"""
    from workflows.model_client import chat

    prompt = json.dumps(
        {"input": article, "analysis": analysis},
        ensure_ascii=False,
        indent=2,
    )
    text, _usage = chat(prompt, system=JUDGE_SYSTEM_PROMPT, temperature=0.0)
    result = _extract_json(text)
    score = int(result.get("score", 0))
    assert 1 <= score <= 10
    return score


def _assert_expected_ranges(
    analysis: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    """使用范围条件验证分析结果。"""
    summary = str(analysis.get("summary", ""))
    keywords = analysis.get("keywords", [])
    relevance_score = float(analysis.get("relevance_score", 0.0))
    should_filter = bool(analysis.get("should_filter", False))

    assert len(summary) >= expected["summary_min_chars"]
    assert isinstance(keywords, list)
    assert len(keywords) >= expected["keywords_min_count"]
    assert relevance_score >= expected.get("relevance_min", 0.0)
    assert relevance_score <= expected.get("relevance_max", 1.0)
    assert should_filter in expected["filter_values"]


def test_eval_cases_structure() -> None:
    """本地验证 EVAL_CASES 结构，不调用 LLM。"""
    assert len(EVAL_CASES) >= 3

    names: set[str] = set()
    for case in EVAL_CASES:
        assert case["name"] not in names
        names.add(case["name"])

        assert isinstance(case["name"], str)
        assert len(case["name"]) >= 1
        assert isinstance(case["input"], str)
        assert len(case["input"]) >= 1

        expected = case["expected"]
        assert isinstance(expected, dict)
        assert expected["summary_min_chars"] >= 0
        assert expected["keywords_min_count"] >= 0
        assert expected.get("relevance_min", 0.0) >= 0.0
        assert expected.get("relevance_max", 1.0) <= 1.0
        assert set(expected["filter_values"]) <= {True, False}


@pytest.mark.slow
@pytest.mark.skipif(
    not _has_llm_api_key(),
    reason="需要 LLM_API_KEY 或当前 provider 对应的 API Key",
)
@pytest.mark.parametrize(
    "case",
    EVAL_CASES,
    ids=[case["name"] for case in EVAL_CASES],
)
def test_ai_knowledge_eval_cases(case: dict[str, Any]) -> None:
    """使用 LLM 分析评估用例，并用范围断言验证结果。"""
    analysis = _analyze_with_llm(case["input"])
    _assert_expected_ranges(analysis, case["expected"])


@pytest.mark.slow
@pytest.mark.skipif(
    not _has_llm_api_key(),
    reason="需要 LLM_API_KEY 或当前 provider 对应的 API Key",
)
def test_llm_as_judge_score() -> None:
    """使用 LLM-as-Judge 对正面案例分析结果打分。"""
    positive_case = EVAL_CASES[0]
    analysis = _analyze_with_llm(positive_case["input"])
    score = _judge_with_llm(positive_case["input"], analysis)

    assert score >= 5
