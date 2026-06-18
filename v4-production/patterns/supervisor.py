"""Supervisor 监督模式：Worker 执行 + Supervisor 审核循环。

工作流程:
    1. Worker Agent 接收任务，输出 JSON 格式的分析报告
    2. Supervisor Agent 对 Worker 的输出进行质量审核
       - 评分维度：准确性(1-10)、深度(1-10)、格式(1-10)
       - 输出 JSON: {"passed": bool, "score": int, "feedback": str}
    3. 审核循环：
       - 通过（score >= 7）→ 返回结果
       - 不通过 → 带反馈重做（最多 max_retries 轮）
       - 超过上限 → 强制返回 + 警告

依赖:
    - pipeline.model_client.quick_chat (LLM 调用)

Example:
    >>> from patterns.supervisor import supervisor
    >>> result = supervisor("分析 LangChain 框架的优缺点")
    >>> print(result["output"])
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

# 确保项目根目录在 sys.path 中，支持直接 python3 执行
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from pipeline.model_client import LLMResponse, quick_chat

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM 调用适配层
# ---------------------------------------------------------------------------


def chat(prompt: str, system: str = "你是一个有帮助的AI助手。") -> tuple[str, Any]:
    """调用 LLM 并返回 (text, usage) 元组。

    Args:
        prompt: 用户提示词。
        system: 系统提示词。

    Returns:
        (回复文本, Usage 对象) 元组。
    """
    response: LLMResponse = quick_chat(prompt, system=system)
    return response.content, response.usage


def chat_json(
    prompt: str,
    system: str = "你是一个有帮助的AI助手。",
) -> tuple[dict[str, Any], Any]:
    """调用 LLM 并将返回内容解析为 JSON。

    Args:
        prompt: 用户提示词（应要求模型返回 JSON）。
        system: 系统提示词。

    Returns:
        (解析后的 dict, Usage 对象) 元组。

    Raises:
        json.JSONDecodeError: 模型返回内容无法解析为 JSON。
    """
    text, usage = chat(prompt, system=system)
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = lines[1:]  # 去掉 ```json
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines)
    parsed: dict[str, Any] = json.loads(cleaned)
    return parsed, usage


# ---------------------------------------------------------------------------
# Worker Agent
# ---------------------------------------------------------------------------

_WORKER_SYSTEM_PROMPT = (
    "你是一个专业的技术分析师。请根据用户给出的任务，输出一份 JSON 格式的分析报告。\n"
    "报告必须严格为 JSON 对象，包含以下字段：\n"
    '  - "title": 报告标题（字符串）\n'
    '  - "analysis": 详细分析内容（字符串，200-500字）\n'
    '  - "key_points": 关键要点列表（字符串数组，3-5条）\n'
    '  - "conclusion": 结论（字符串，50-100字）\n'
    "不要包含任何 JSON 之外的文字。"
)

_WORKER_RETRY_SYSTEM_PROMPT = (
    "你是一个专业的技术分析师。上一次你的输出未通过质量审核。\n"
    "请根据审核反馈改进你的分析报告。\n"
    "报告必须严格为 JSON 对象，包含以下字段：\n"
    '  - "title": 报告标题（字符串）\n'
    '  - "analysis": 详细分析内容（字符串，200-500字）\n'
    '  - "key_points": 关键要点列表（字符串数组，3-5条）\n'
    '  - "conclusion": 结论（字符串，50-100字）\n'
    "不要包含任何 JSON 之外的文字。"
)


def _worker_execute(task: str, feedback: str | None = None) -> dict[str, Any]:
    """Worker Agent 执行任务，生成 JSON 分析报告。

    Args:
        task: 用户给出的分析任务描述。
        feedback: Supervisor 的审核反馈（重做时提供）。

    Returns:
        解析后的 JSON 报告字典。

    Raises:
        json.JSONDecodeError: Worker 输出无法解析为 JSON。
    """
    if feedback:
        prompt = (
            f"原始任务：{task}\n\n"
            f"审核反馈（请据此改进）：{feedback}\n\n"
            "请重新输出改进后的 JSON 分析报告。"
        )
        system = _WORKER_RETRY_SYSTEM_PROMPT
    else:
        prompt = f"请对以下任务进行分析：{task}"
        system = _WORKER_SYSTEM_PROMPT

    logger.info("Worker executing task: %r (has_feedback=%s)", task, feedback is not None)
    result, _usage = chat_json(prompt, system=system)
    logger.info("Worker produced report: title=%r", result.get("title", ""))
    return result


# ---------------------------------------------------------------------------
# Supervisor Agent
# ---------------------------------------------------------------------------

_SUPERVISOR_SYSTEM_PROMPT = (
    "你是一个严格的质量审核员。你需要对 Worker 生成的分析报告进行质量审核。\n"
    "评分维度（每项 1-10 分）：\n"
    "  - 准确性：信息是否准确、有无错误\n"
    "  - 深度：分析是否有深度、是否有独到见解\n"
    "  - 格式：JSON 结构是否完整、字段是否齐全、内容长度是否合适\n\n"
    "请只返回一个 JSON 对象，格式如下：\n"
    "{\n"
    '  "accuracy": <1-10>,\n'
    '  "depth": <1-10>,\n'
    '  "format": <1-10>,\n'
    '  "passed": <true/false，总分(三项之和/3)>=7 为 true>,\n'
    '  "score": <总分，三项均分取整>,\n'
    '  "feedback": "<改进建议，passed 为 true 时可简短表示认可>"\n'
    "}\n"
    "不要包含任何 JSON 之外的文字。"
)


def _supervisor_review(task: str, worker_output: dict[str, Any]) -> dict[str, Any]:
    """Supervisor Agent 对 Worker 输出进行质量审核。

    Args:
        task: 原始任务描述（供审核员理解上下文）。
        worker_output: Worker 生成的 JSON 报告。

    Returns:
        审核结果字典，包含 passed, score, feedback 等字段。

    Raises:
        json.JSONDecodeError: Supervisor 输出无法解析为 JSON。
    """
    prompt = (
        f"原始任务：{task}\n\n"
        f"Worker 输出的分析报告：\n"
        f"{json.dumps(worker_output, ensure_ascii=False, indent=2)}\n\n"
        "请对该报告进行质量审核。"
    )

    logger.info("Supervisor reviewing worker output...")
    review, _usage = chat_json(prompt, system=_SUPERVISOR_SYSTEM_PROMPT)

    # 确保关键字段存在并归一化
    accuracy = review.get("accuracy", 5)
    depth = review.get("depth", 5)
    fmt = review.get("format", 5)
    avg_score = round((accuracy + depth + fmt) / 3)

    # 以计算值为准，覆盖模型可能的计算错误
    review["score"] = avg_score
    review["passed"] = avg_score >= 7
    review.setdefault("feedback", "")

    logger.info(
        "Supervisor review: accuracy=%d, depth=%d, format=%d, "
        "score=%d, passed=%s",
        accuracy, depth, fmt, avg_score, review["passed"],
    )
    return review


# ---------------------------------------------------------------------------
# 主入口：Supervisor 监督循环
# ---------------------------------------------------------------------------


def supervisor(task: str, max_retries: int = 3) -> dict[str, Any]:
    """Supervisor 监督模式入口：Worker 执行 + Supervisor 审核循环。

    工作流程:
        1. Worker 执行任务生成报告
        2. Supervisor 审核报告质量
        3. 通过（score >= 7）→ 返回；不通过 → 带反馈重做
        4. 超过 max_retries 轮 → 强制返回并附带警告

    Args:
        task: 需要分析的任务描述。
        max_retries: 最大重试轮数，默认 3。

    Returns:
        结果字典，包含:
            - output: Worker 生成的最终报告 (dict)
            - attempts: 实际执行轮数 (int)
            - final_score: 最终审核得分 (int)
            - warning: 可选，超过重试次数时的警告信息 (str)
    """
    if not task or not task.strip():
        return {
            "output": {},
            "attempts": 0,
            "final_score": 0,
            "warning": "任务描述为空，无法执行。",
        }

    task = task.strip()
    logger.info("Supervisor started: task=%r, max_retries=%d", task, max_retries)

    feedback: str | None = None
    worker_output: dict[str, Any] = {}
    final_score: int = 0

    for attempt in range(1, max_retries + 1):
        logger.info("--- Attempt %d/%d ---", attempt, max_retries)

        # Worker 执行
        try:
            worker_output = _worker_execute(task, feedback=feedback)
        except (json.JSONDecodeError, Exception) as exc:
            logger.error("Worker failed at attempt %d: %s", attempt, exc)
            feedback = f"Worker 输出格式错误：{exc}。请确保输出为合法 JSON。"
            continue

        # Supervisor 审核
        try:
            review = _supervisor_review(task, worker_output)
        except (json.JSONDecodeError, Exception) as exc:
            logger.error("Supervisor review failed at attempt %d: %s", attempt, exc)
            # 审核失败时给予通用反馈，继续下一轮
            feedback = "审核过程出错，请确保输出格式正确并提升内容质量。"
            continue

        final_score = review["score"]

        if review["passed"]:
            logger.info(
                "Task passed at attempt %d with score %d", attempt, final_score
            )
            return {
                "output": worker_output,
                "attempts": attempt,
                "final_score": final_score,
            }

        # 未通过，准备反馈进入下一轮
        feedback = review.get("feedback", "质量不达标，请提升分析深度和准确性。")
        logger.info(
            "Task not passed (score=%d), feedback: %s", final_score, feedback
        )

    # 超过最大重试次数，强制返回
    warning_msg = (
        f"已达到最大重试次数（{max_retries}轮），"
        f"最终得分 {final_score} 未达到通过标准（>=7）。"
        "结果可能质量不足，请人工复核。"
    )
    logger.warning(warning_msg)

    return {
        "output": worker_output,
        "attempts": max_retries,
        "final_score": final_score,
        "warning": warning_msg,
    }


# ---------------------------------------------------------------------------
# 测试入口
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    test_tasks = [
        "分析 LangChain 框架的优缺点及适用场景",
        "对比 RAG 和 Fine-tuning 两种方案在企业知识库场景的优劣",
    ]

    logger.info("=" * 60)
    logger.info("  Supervisor 监督模式测试")
    logger.info("=" * 60)

    for task in test_tasks:
        logger.info("\n--- Task: %r ---", task)
        result = supervisor(task, max_retries=3)

        logger.info("Attempts: %d", result["attempts"])
        logger.info("Final score: %d", result["final_score"])

        if "warning" in result:
            logger.warning("Warning: %s", result["warning"])

        output = result["output"]
        if output:
            logger.info(
                "Output:\n%s",
                json.dumps(output, ensure_ascii=False, indent=2),
            )
        else:
            logger.info("Output: (empty)")

        logger.info("")

    logger.info("=" * 60)
    logger.info("  测试完成")
    logger.info("=" * 60)
