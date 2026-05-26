"""统一 LLM 调用客户端模块。

支持 Zhipu（智谱）和 Qwen（通义千问）两种模型提供商，
通过环境变量切换，使用 httpx 直接调用 OpenAI 兼容 API。

环境变量:
    LLM_PROVIDER: 模型提供商名称，可选 "zhipu" 或 "qwen"，默认 "zhipu"
    ZHIPU_API_KEY: 智谱 API Key
    QWEN_API_KEY: 通义千问 API Key

Example:
    >>> from pipeline.model_client import quick_chat
    >>> response = quick_chat("你好，请介绍一下你自己")
    >>> print(response.content)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class Usage:
    """Token 用量统计。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class LLMResponse:
    """LLM 统一响应结构。

    Attributes:
        content: 模型生成的文本内容。
        usage: Token 用量统计。
        model: 实际使用的模型名称。
        provider: 提供商名称。
        latency_ms: 请求耗时（毫秒）。
    """

    content: str
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    provider: str = ""
    latency_ms: float = 0.0


# ---------------------------------------------------------------------------
# 提供商配置
# ---------------------------------------------------------------------------

# 模型定价表：(输入价格 USD/1K tokens, 输出价格 USD/1K tokens)
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # Zhipu 智谱
    "glm-4.6v": (0.007, 0.007),
    "glm-4-plus": (0.007, 0.007),
    "glm-4": (0.014, 0.014),
    "glm-4-flash": (0.0001, 0.0001),
    "glm-3-turbo": (0.0007, 0.0007),
    # Qwen 通义千问
    "qwen3.6-plus": (0.0005, 0.0015),
    "qwen-turbo": (0.0003, 0.0006),
    "qwen-plus": (0.0005, 0.0015),
    "qwen-max": (0.004, 0.012),
    "qwen-long": (0.0005, 0.002),
}

PROVIDER_CONFIGS: dict[str, dict[str, str]] = {
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4.6v",
        "api_key_env": "ZHIPU_API_KEY",
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen3.6-plus",
        "api_key_env": "QWEN_API_KEY",
    },
}


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------


class LLMProvider(ABC):
    """LLM 提供商抽象基类。"""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> LLMResponse:
        """发送聊天请求并返回响应。

        Args:
            messages: OpenAI 格式的消息列表，
                例如 [{"role": "user", "content": "你好"}]。
            model: 模型名称，为 None 时使用默认模型。
            temperature: 采样温度，范围 [0, 2]。
            max_tokens: 最大生成 token 数。
            **kwargs: 其他模型特定参数。

        Returns:
            LLMResponse 统一响应对象。
        """
        ...

    @abstractmethod
    def get_provider_name(self) -> str:
        """返回提供商名称。"""
        ...


# ---------------------------------------------------------------------------
# OpenAI 兼容实现
# ---------------------------------------------------------------------------


class OpenAICompatibleProvider(LLMProvider):
    """基于 OpenAI 兼容 API 的通用提供商实现。

    使用 httpx 异步客户端直接调用 REST API，无需依赖 openai SDK。
    """

    def __init__(
        self,
        provider_name: str,
        base_url: str,
        api_key: str,
        default_model: str,
        timeout: float = 60.0,
    ) -> None:
        """初始化提供商。

        Args:
            provider_name: 提供商标识名称。
            base_url: API 基础 URL。
            api_key: 认证密钥。
            default_model: 默认使用的模型。
            timeout: 请求超时时间（秒）。
        """
        self._provider_name = provider_name
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._default_model = default_model
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        """获取或创建 httpx 异步客户端（懒初始化）。"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(self._timeout),
            )
        return self._client

    async def close(self) -> None:
        """关闭底层 HTTP 客户端。"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def get_provider_name(self) -> str:
        """返回提供商名称。"""
        return self._provider_name

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> LLMResponse:
        """发送聊天补全请求。

        Args:
            messages: 消息列表。
            model: 模型名称，为 None 时使用默认模型。
            temperature: 采样温度。
            max_tokens: 最大生成 token 数。
            **kwargs: 额外参数，将直接传递给 API。

        Returns:
            LLMResponse 统一响应。

        Raises:
            httpx.HTTPStatusError: API 返回非 2xx 状态码。
            httpx.TimeoutException: 请求超时。
        """
        used_model = model or self._default_model
        payload: dict[str, Any] = {
            "model": used_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **kwargs,
        }

        client = self._get_client()
        start_time = time.perf_counter()

        logger.debug(
            "Sending chat request to %s, model=%s, messages=%d",
            self._provider_name,
            used_model,
            len(messages),
        )

        response = await client.post("/chat/completions", json=payload)
        response.raise_for_status()

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        data = response.json()

        # 解析响应
        choice = data.get("choices", [{}])[0]
        content = choice.get("message", {}).get("content", "")
        usage_data = data.get("usage", {})

        usage = Usage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
        )

        logger.info(
            "Response received: provider=%s, model=%s, tokens=%d, latency=%.0fms",
            self._provider_name,
            used_model,
            usage.total_tokens,
            elapsed_ms,
        )

        return LLMResponse(
            content=content,
            usage=usage,
            model=used_model,
            provider=self._provider_name,
            latency_ms=elapsed_ms,
        )


# ---------------------------------------------------------------------------
# 重试逻辑
# ---------------------------------------------------------------------------


async def chat_with_retry(
    provider: LLMProvider,
    messages: list[dict[str, str]],
    model: str | None = None,
    max_retries: int = 3,
    base_delay: float = 1.0,
    **kwargs: Any,
) -> LLMResponse:
    """带指数退避重试的聊天请求。

    在网络异常或服务端错误时自动重试，最多重试 max_retries 次，
    每次等待时间按指数增长（base_delay * 2^attempt）。

    Args:
        provider: LLM 提供商实例。
        messages: 消息列表。
        model: 模型名称。
        max_retries: 最大重试次数，默认 3。
        base_delay: 基础退避延迟（秒），默认 1.0。
        **kwargs: 传递给 provider.chat() 的额外参数。

    Returns:
        LLMResponse 统一响应。

    Raises:
        Exception: 所有重试均失败后抛出最后一次异常。
    """
    last_exception: Exception | None = None

    for attempt in range(max_retries):
        try:
            return await provider.chat(messages, model=model, **kwargs)
        except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
            last_exception = exc
            if isinstance(exc, httpx.HTTPStatusError):
                # 4xx 客户端错误（非 429）不重试
                if 400 <= exc.response.status_code < 500 and exc.response.status_code != 429:
                    logger.error(
                        "Client error %d, not retrying: %s",
                        exc.response.status_code,
                        exc.response.text,
                    )
                    raise

            delay = base_delay * (2 ** attempt)
            logger.warning(
                "Request failed (attempt %d/%d), retrying in %.1fs: %s",
                attempt + 1,
                max_retries,
                delay,
                exc,
            )
            await asyncio.sleep(delay)
        except Exception as exc:
            last_exception = exc
            logger.error("Unexpected error, not retrying: %s", exc)
            raise

    # 所有重试均失败
    raise last_exception  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Token 消耗估算与成本计算
# ---------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """粗略估算文本的 token 数量。

    对于中文文本，大约每个字符 1.5-2 个 token；
    对于英文文本，大约每 4 个字符 1 个 token。
    此处使用混合估算策略。

    Args:
        text: 待估算的文本。

    Returns:
        估算的 token 数量。
    """
    chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other_chars = len(text) - chinese_chars
    # 中文：约 1.5 token/字；英文及其他：约 0.25 token/字符
    estimated = int(chinese_chars * 1.5 + other_chars * 0.25)
    return max(estimated, 1)


def calculate_cost(usage: Usage, model: str) -> float:
    """根据用量和模型计算费用（USD）。

    Args:
        usage: Token 用量统计。
        model: 模型名称（需在 MODEL_PRICING 中存在）。

    Returns:
        费用（美元），精确到小数点后 6 位。
        如果模型不在定价表中，返回 0.0。
    """
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        logger.warning("Model %r not found in pricing table, cost=0.0", model)
        return 0.0

    input_price_per_k, output_price_per_k = pricing
    cost = (
        usage.prompt_tokens * input_price_per_k / 1000
        + usage.completion_tokens * output_price_per_k / 1000
    )
    return round(cost, 6)


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def create_provider(
    provider_name: str | None = None,
    api_key: str | None = None,
) -> OpenAICompatibleProvider:
    """根据配置创建 LLM 提供商实例。

    Args:
        provider_name: 提供商名称（"zhipu" 或 "qwen"）。
            为 None 时从环境变量 LLM_PROVIDER 读取，默认 "zhipu"。
        api_key: API 密钥。为 None 时从对应环境变量读取。

    Returns:
        配置好的 OpenAICompatibleProvider 实例。

    Raises:
        ValueError: 提供商名称无效或 API Key 未配置。
    """
    name = (provider_name or os.getenv("LLM_PROVIDER", "zhipu")).lower().strip()

    if name not in PROVIDER_CONFIGS:
        raise ValueError(
            f"Unsupported provider: {name!r}. "
            f"Supported: {list(PROVIDER_CONFIGS.keys())}"
        )

    config = PROVIDER_CONFIGS[name]
    resolved_key = api_key or os.getenv(config["api_key_env"], "")

    if not resolved_key:
        raise ValueError(
            f"API key not found. Please set environment variable "
            f"'{config['api_key_env']}' or pass api_key parameter."
        )

    logger.info("Creating LLM provider: %s (model=%s)", name, config["default_model"])

    return OpenAICompatibleProvider(
        provider_name=name,
        base_url=config["base_url"],
        api_key=resolved_key,
        default_model=config["default_model"],
        timeout=60.0,
    )


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------


async def _quick_chat_async(
    prompt: str,
    system: str = "你是一个有帮助的AI助手。",
    model: str | None = None,
    provider_name: str | None = None,
) -> LLMResponse:
    """quick_chat 的异步内部实现。"""
    provider = create_provider(provider_name)
    try:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        return await chat_with_retry(provider, messages, model=model)
    finally:
        await provider.close()


def quick_chat(
    prompt: str,
    system: str = "你是一个有帮助的AI助手。",
    model: str | None = None,
    provider_name: str | None = None,
) -> LLMResponse:
    """一句话调用 LLM 的便捷函数。

    自动处理提供商创建、消息组装和资源清理。
    在同步上下文中可直接调用。

    Args:
        prompt: 用户输入的问题或指令。
        system: 系统提示词，默认为通用助手。
        model: 指定模型名称，为 None 时使用提供商默认模型。
        provider_name: 提供商名称，为 None 时从环境变量读取。

    Returns:
        LLMResponse 统一响应。

    Example:
        >>> resp = quick_chat("1+1等于几？")
        >>> print(resp.content)
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # 已在异步上下文中，创建新任务
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                asyncio.run,
                _quick_chat_async(prompt, system, model, provider_name),
            )
            return future.result()
    else:
        return asyncio.run(
            _quick_chat_async(prompt, system, model, provider_name)
        )


# ---------------------------------------------------------------------------
# 主程序测试
# ---------------------------------------------------------------------------


async def _main() -> None:
    """主测试函数。"""
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # 1. 测试 token 估算
    test_text = "Hello, 你好世界！这是一个测试。"
    estimated = estimate_tokens(test_text)
    logger.info("Token estimation for %r: %d tokens", test_text, estimated)

    # 2. 测试成本计算
    sample_usage = Usage(prompt_tokens=500, completion_tokens=200, total_tokens=700)
    cost = calculate_cost(sample_usage, "glm-4-flash")
    logger.info(
        "Cost for glm-4-flash (500 in + 200 out): $%.6f", cost
    )

    # 3. 测试实际 API 调用
    provider_name = os.getenv("LLM_PROVIDER", "zhipu")
    logger.info("Testing with provider: %s", provider_name)

    try:
        provider = create_provider()
    except ValueError as exc:
        logger.error("Failed to create provider: %s", exc)
        logger.info(
            "Please set the appropriate API key environment variable to test."
        )
        return

    try:
        # 单次调用测试
        messages = [
            {"role": "system", "content": "你是一个简洁的AI助手，回答控制在50字以内。"},
            {"role": "user", "content": "用一句话解释什么是大语言模型。"},
        ]

        logger.info("--- Testing chat_with_retry ---")
        response = await chat_with_retry(provider, messages)
        logger.info("Response content: %s", response.content)
        logger.info(
            "Usage: prompt=%d, completion=%d, total=%d",
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
            response.usage.total_tokens,
        )
        logger.info("Latency: %.0f ms", response.latency_ms)

        cost = calculate_cost(response.usage, response.model)
        logger.info("Estimated cost: $%.6f", cost)

    finally:
        await provider.close()

    # 4. 测试 quick_chat 便捷函数
    logger.info("--- Testing quick_chat ---")
    try:
        resp = quick_chat("Python 的 GIL 是什么？一句话回答。")
        logger.info("quick_chat response: %s", resp.content)
    except ValueError as exc:
        logger.error("quick_chat failed: %s", exc)


if __name__ == "__main__":
    asyncio.run(_main())
