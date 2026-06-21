"""知识条目推送模块。

定义 BasePublisher 抽象基类以及 Telegram、飞书两个渠道的具体实现，
并提供统一并发推送入口 publish_daily_digest()。

本模块依赖 aiohttp 进行异步 HTTP 请求，
通过环境变量注入各渠道的凭证与地址。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import os
import time as _time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp

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
            if (
                not stripped
                or stripped.startswith("#")
                or "=" not in stripped
            ):
                continue
            key, value = stripped.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))
        return True


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
load_dotenv(str(PROJECT_ROOT / ".env"))

from openclaw.distribution.formatter import generate_daily_digest

logger = logging.getLogger(__name__)

# Telegram Bot API 发送消息端点
_TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

# aiohttp 会话级超时（秒）
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)


@dataclass
class PublishResult:
    """记录单次发布结果。

    Attributes:
        channel: 发布渠道标识，如 "telegram" / "feishu"。
        success: 发布是否成功。
        message_id: 远端返回的消息 ID，失败时为 None。
        error: 失败原因描述，成功时为 None。
    """

    channel: str
    success: bool
    message_id: str | None = None
    error: str | None = None


class BasePublisher(ABC):
    """发布器抽象基类。

    所有发布渠道必须实现 send_message() 和 send_digest() 两个方法。
    """

    @abstractmethod
    async def send_message(self, content: Any) -> PublishResult:
        """发送单条消息到渠道。

        Args:
            content: 消息内容，格式由渠道决定（字符串或 dict）。
        Returns:
            PublishResult 记录本次发送结果。
        """

    @abstractmethod
    async def send_digest(self, content: Any) -> PublishResult:
        """发送结构化简报到渠道。

        Args:
            content: 简报内容，格式由渠道决定（字符串或 dict）。
        Returns:
            PublishResult 记录本次发送结果。
        """


class TelegramPublisher(BasePublisher):
    """Telegram Bot 发布器。

    通过 Telegram Bot API 异步发送 MarkdownV2 格式消息。
    从环境变量读取 TELEGRAM_BOT_TOKEN 和 TELEGRAM_CHAT_ID。

    Attributes:
        bot_token: Telegram Bot Token。
        chat_id: 目标对话 ID。
        session: 复用的 aiohttp 会话。
    """

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        """初始化 Telegram 发布器。

        Args:
            session: 外部传入的 aiohttp 会话，若不传则内部创建。
        Raises:
            ValueError: 缺少必要的环境变量时抛出。
        """
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if not self.bot_token or not self.chat_id:
            raise ValueError(
                "缺少 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID 环境变量"
            )
        self._session = session
        self._own_session = session is None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """确保 aiohttp 会话可用。"""
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT)
        return self._session

    async def close(self) -> None:
        """关闭内部创建的 aiohttp 会话。"""
        if self._own_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def send_message(self, content: str) -> PublishResult:
        """通过 Telegram Bot API 发送 MarkdownV2 消息。

        Args:
            content: 符合 Telegram MarkdownV2 格式的字符串。
        Returns:
            PublishResult 记录本次发送结果。
        """
        url = _TELEGRAM_API_URL.format(token=self.bot_token)
        payload = {
            "chat_id": self.chat_id,
            "text": content,
            "parse_mode": "MarkdownV2",
        }

        session = await self._ensure_session()
        try:
            async with session.post(url, json=payload) as resp:
                data = await resp.json()
                if resp.status == 200 and data.get("ok"):
                    msg_id = str(data["result"]["message_id"])
                    logger.info(
                        "[Telegram] 发送成功 message_id=%s", msg_id
                    )
                    return PublishResult(
                        channel="telegram",
                        success=True,
                        message_id=msg_id,
                    )
                error_desc = data.get("description", "未知错误")
                logger.error(
                    "[Telegram] 发送失败 status=%d error=%s",
                    resp.status,
                    error_desc,
                )
                return PublishResult(
                    channel="telegram",
                    success=False,
                    error=f"HTTP {resp.status}: {error_desc}",
                )
        except aiohttp.ClientError as exc:
            logger.error("[Telegram] 网络异常: %s", exc)
            return PublishResult(
                channel="telegram", success=False, error=str(exc)
            )

    async def send_digest(self, content: str) -> PublishResult:
        """发送 Telegram 简报（与 send_message 逻辑相同）。

        Args:
            content: 符合 Telegram MarkdownV2 格式的简报字符串。
        Returns:
            PublishResult 记录本次发送结果。
        """
        return await self.send_message(content)


class FeishuPublisher(BasePublisher):
    """飞书 Webhook 发布器。

    通过飞书自定义机器人 Webhook 异步发送 interactive 卡片消息。
    从环境变量读取 FEISHU_WEBHOOK_URL，可选读取 FEISHU_SECRET
    用于签名校验。

    Attributes:
        webhook_url: 飞书 Webhook 地址。
        secret: 签名密钥（若配置了安全设置的签名校验）。
        session: 复用的 aiohttp 会话。
    """

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        """初始化飞书发布器。

        Args:
            session: 外部传入的 aiohttp 会话，若不传则内部创建。
        Raises:
            ValueError: 缺少 FEISHU_WEBHOOK_URL 环境变量时抛出。
        """
        self.webhook_url = os.getenv("FEISHU_WEBHOOK_URL", "")
        if not self.webhook_url:
            raise ValueError("缺少 FEISHU_WEBHOOK_URL 环境变量")
        self.secret = os.getenv("FEISHU_SECRET", "")
        self._session = session
        self._own_session = session is None

    def _build_signature(self) -> tuple[str, str]:
        """构建飞书签名校验所需的 timestamp 和 sign。

        签名算法: base64(hmac-sha256(timestamp\\nsecret, ""))，
        与飞书 openapi 文档一致。

        Returns:
            (timestamp, sign) 元组。未配置 FEISHU_SECRET 时
            timestamp 为空字符串，sign 为空字符串。
        """
        if not self.secret:
            return "", ""
        timestamp = str(int(_time.time()))
        string_to_sign = f"{timestamp}\n{self.secret}"
        hmac_code = hmac.new(
            string_to_sign.encode("utf-8"),
            b"",
            hashlib.sha256,
        ).digest()
        sign = base64.b64encode(hmac_code).decode("utf-8")
        return timestamp, sign

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """确保 aiohttp 会话可用。"""
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT)
        return self._session

    async def close(self) -> None:
        """关闭内部创建的 aiohttp 会话。"""
        if self._own_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def send_message(self, content: dict[str, Any]) -> PublishResult:
        """通过飞书 Webhook 发送 interactive 卡片消息。

        若配置了 FEISHU_SECRET，自动注入 timestamp 和 sign
        字段用于安全签名校验。

        Args:
            content: 飞书消息卡片 dict，含 msg_type 和 card。
        Returns:
            PublishResult 记录本次发送结果。
        """
        payload: dict[str, Any] = content
        timestamp, sign = self._build_signature()
        if timestamp and sign:
            payload["timestamp"] = timestamp
            payload["sign"] = sign

        session = await self._ensure_session()
        try:
            async with session.post(self.webhook_url, json=payload) as resp:
                data = await resp.json()
                if resp.status == 200 and data.get("code") == 0:
                    msg_id = data.get("data", {}).get("message_id", "")
                    logger.info(
                        "[Feishu] 发送成功 message_id=%s", msg_id
                    )
                    return PublishResult(
                        channel="feishu",
                        success=True,
                        message_id=msg_id or None,
                    )
                error_msg = data.get("msg", "未知错误")
                logger.error(
                    "[Feishu] 发送失败 status=%d code=%s msg=%s",
                    resp.status,
                    data.get("code"),
                    error_msg,
                )
                return PublishResult(
                    channel="feishu",
                    success=False,
                    error=f"code={data.get('code')}: {error_msg}",
                )
        except aiohttp.ClientError as exc:
            logger.error("[Feishu] 网络异常: %s", exc)
            return PublishResult(
                channel="feishu", success=False, error=str(exc)
            )

    async def send_digest(self, content: dict[str, Any]) -> PublishResult:
        """发送飞书简报（与 send_message 逻辑相同）。

        Args:
            content: 飞书消息卡片 dict。
        Returns:
            PublishResult 记录本次发送结果。
        """
        return await self.send_message(content)


async def publish_daily_digest(
    knowledge_dir: str | None = None,
    date: str | None = None,
    top_n: int = 5,
    channels: list[str] | None = None,
) -> list[PublishResult]:
    """生成当日简报并并发推送到指定渠道。

    调用 generate_daily_digest() 生成三种格式的简报，
    然后并发发布到指定的渠道列表。
    未配置环境变量的渠道会被静默跳过。

    Args:
        knowledge_dir: 知识条目目录，None 使用默认值。
        date: 日期字符串 (YYYY-MM-DD)，None 表示当天。
        top_n: 取前 N 条，默认 5。
        channels: 目标渠道列表，如 ["telegram", "feishu"]。
                  None 表示所有已配置渠道。

    Returns:
        各渠道的 PublishResult 列表。
    """
    if channels is not None:
        channels = [c.lower() for c in channels]
    else:
        channels = ["telegram", "feishu"]

    digest_kwargs: dict[str, Any] = {"top_n": top_n}
    if knowledge_dir is not None:
        digest_kwargs["knowledge_dir"] = knowledge_dir
    if date is not None:
        digest_kwargs["date"] = date

    digest = generate_daily_digest(**digest_kwargs)

    publishers: list[tuple[str, BasePublisher, Any]] = []
    session = aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT)

    try:
        if "telegram" in channels:
            if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
                tg = TelegramPublisher(session=session)
                publishers.append(("telegram", tg, digest["telegram"]))
                logger.info("[Publisher] Telegram 渠道已配置")
            else:
                logger.info("[Publisher] Telegram 渠道未配置，跳过")

        if "feishu" in channels:
            if os.getenv("FEISHU_WEBHOOK_URL"):
                feishu_content = digest["feishu"]
                if isinstance(feishu_content, str):
                    logger.info("[Publisher] 飞书当日无内容，跳过")
                else:
                    feishu = FeishuPublisher(session=session)
                    publishers.append(
                        ("feishu", feishu, feishu_content)
                    )
                    logger.info("[Publisher] 飞书渠道已配置")
            else:
                logger.info("[Publisher] 飞书渠道未配置，跳过")

        if not publishers:
            logger.warning("[Publisher] 无可用发布渠道")
            return []

        tasks = [
            pub.send_digest(content)
            for _, pub, content in publishers
        ]
        results = list(await asyncio.gather(*tasks))

        for result in results:
            status = "成功" if result.success else "失败"
            logger.info(
                "[Publisher] %s 推送%s: %s",
                result.channel,
                status,
                result.message_id or result.error or "",
            )

        return results
    finally:
        await session.close()
