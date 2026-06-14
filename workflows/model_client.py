"""工作流层 LLM 调用封装。

对 pipeline.model_client 的同步化桥接，为 LangGraph 节点提供
简洁的同步接口：chat()、chat_json()、accumulate_usage()。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

# 确保项目根目录在 sys.path 中
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from pipeline.model_client import (
    LLMResponse,
    Usage,
    chat_with_retry,
    create_provider,
)

logger = logging.getLogger(__name__)


def _run_async(coro: Any) -> Any:
    """在同步上下文中运行协程。

    兼容已有事件循环（如 Jupyter）和无事件循环的场景。

    Args:
        coro: 待执行的协程对象。

    Returns:
        协程的返回值。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()
    else:
        return asyncio.run(coro)


def _usage_to_dict(usage: Usage) -> dict[str, int]:
    """将 Usage 数据类转换为 dict，便于 cost_tracker 累加。

    Args:
        usage: pipeline.model_client.Usage 实例。

    Returns:
        包含 prompt_tokens、completion_tokens、total_tokens 的字典。
    """
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
    }


def chat(prompt: str, system: str = "你是一个有帮助的AI助手。") -> tuple[str, dict]:
    """同步调用 LLM 并返回文本响应。

    Args:
        prompt: 用户输入的问题或指令。
        system: 系统提示词。

    Returns:
        (text, usage) 元组：
            - text: 模型生成的文本内容。
            - usage: Token 用量字典。
    """

    async def _call() -> LLMResponse:
        provider = create_provider()
        try:
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ]
            return await chat_with_retry(provider, messages)
        finally:
            await provider.close()

    response = _run_async(_call())
    return response.content, _usage_to_dict(response.usage)


def chat_json(
    prompt: str, system: str = "你是一个有帮助的AI助手。"
) -> tuple[Any, dict]:
    """同步调用 LLM 并将响应解析为 JSON。

    在 system prompt 中应要求模型输出纯 JSON。若解析失败，
    返回 None 作为 parsed 结果。

    Args:
        prompt: 用户输入的问题或指令。
        system: 系统提示词（应包含 JSON 输出要求）。

    Returns:
        (parsed_json, usage) 元组：
            - parsed_json: 解析后的 Python 对象（dict/list），解析失败时为 None。
            - usage: Token 用量字典。
    """
    text, usage = chat(prompt, system=system)

    # 尝试提取 JSON（兼容 markdown 代码块包裹）
    content = text.strip()
    if content.startswith("```"):
        # 去除 ```json ... ``` 包裹
        lines = content.split("\n")
        # 移除首行 ``` 和末行 ```
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines)

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        logger.warning("JSON 解析失败: %s\n原始内容: %s", exc, text[:200])
        parsed = None

    return parsed, usage


def accumulate_usage(tracker: dict, usage: dict) -> None:
    """将一次调用的 usage 累加到 cost_tracker 中。

    Args:
        tracker: 状态中的 cost_tracker 字典，原地修改。
        usage: 单次调用返回的 usage 字典。
    """
    tracker["prompt_tokens"] = (
        tracker.get("prompt_tokens", 0) + usage.get("prompt_tokens", 0)
    )
    tracker["completion_tokens"] = (
        tracker.get("completion_tokens", 0) + usage.get("completion_tokens", 0)
    )
    tracker["total_tokens"] = (
        tracker.get("total_tokens", 0) + usage.get("total_tokens", 0)
    )
