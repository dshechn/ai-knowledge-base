"""知识库交互模块。

提供搜索引擎、订阅管理、权限控制和意图识别，
作为知识库 Bot 的统一入口。

架构：
    KnowledgeBot (主入口)
    ├── KnowledgeSearchEngine (搜索)
    ├── SubscriptionManager (订阅)
    └── PermissionManager (权限)
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_KNOWLEDGE_DIR = "knowledge/articles"


class Intent(Enum):
    """用户意图枚举。"""

    SEARCH = auto()
    TODAY = auto()
    TOP = auto()
    SUBSCRIBE = auto()
    HELP = auto()
    UNKNOWN = auto()


class Permission(Enum):
    """三级权限等级。"""

    READ = "read"
    WRITE = "write"
    DELETE = "delete"


@dataclass
class SearchResult:
    """单条搜索结果。"""

    article_id: str
    title: str
    source: str
    source_url: str
    summary: str
    tags: list[str]
    category: str
    relevance_score: float
    collected_at: str


class KnowledgeSearchEngine:
    """知识库搜索引擎。

    支持关键词、标签、日期范围过滤，
    从 knowledge/articles/ 目录加载 JSON 条目。

    Attributes:
        knowledge_dir: 知识条目存储目录路径。
    """

    def __init__(self, knowledge_dir: str | Path = _DEFAULT_KNOWLEDGE_DIR):
        """初始化搜索引擎。

        Args:
            knowledge_dir: 知识条目目录路径。
        """
        self.knowledge_dir = Path(knowledge_dir)

    def _load_all_articles(self) -> list[dict[str, Any]]:
        """加载全部知识条目。

        Returns:
            解析后的文章 dict 列表。
        """
        articles: list[dict[str, Any]] = []
        if not self.knowledge_dir.exists():
            logger.warning("知识库目录不存在: %s", self.knowledge_dir)
            return articles

        for file_path in sorted(
            self.knowledge_dir.glob("*.json")
        ):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    article = json.load(f)
                if isinstance(article, dict):
                    articles.append(article)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(
                    "跳过解析失败的文件 %s: %s", file_path, exc
                )

        return articles

    def _to_search_result(self, article: dict[str, Any]) -> SearchResult:
        """将文章 dict 转换为 SearchResult。

        Args:
            article: 知识条目 dict。

        Returns:
            SearchResult 对象。
        """
        score_value = article.get(
            "relevance_score"
        ) or article.get("score")
        if score_value is not None and (
            article.get("relevance_score") is None
        ):
            score_value = float(score_value) / 10.0
        else:
            score_value = float(score_value or 0)

        return SearchResult(
            article_id=article.get("id", ""),
            title=article.get("title", ""),
            source=article.get("source", ""),
            source_url=article.get(
                "source_url"
            ) or article.get("url", ""),
            summary=article.get("summary", ""),
            tags=article.get("tags", []),
            category=article.get("category", ""),
            relevance_score=score_value,
            collected_at=article.get("collected_at", ""),
        )

    def search(
        self,
        keyword: str | None = None,
        tags: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        category: str | None = None,
        min_score: float | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        """按条件搜索知识条目。

        所有条件为 AND 关系，未指定的条件不做过滤。

        Args:
            keyword: 关键词，匹配标题和摘要。
            tags: 标签列表，条目需包含至少一个指定标签。
            date_from: 起始日期 (YYYY-MM-DD)。
            date_to: 结束日期 (YYYY-MM-DD)。
            category: 分类过滤。
            min_score: 最低相关性分数阈值 (0.0-1.0)。
            limit: 最大返回条数。

        Returns:
            匹配的 SearchResult 列表，按相关性降序排列。
        """
        results: list[SearchResult] = []
        keyword_lower = keyword.lower() if keyword else None

        for article in self._load_all_articles():
            if keyword_lower:
                title = article.get("title", "").lower()
                summary = article.get("summary", "").lower()
                if keyword_lower not in title and keyword_lower not in summary:
                    continue

            if tags:
                article_tags = article.get("tags", [])
                if not any(t in article_tags for t in tags):
                    continue

            if date_from:
                collected = article.get("collected_at", "")
                if collected < date_from:
                    continue

            if date_to:
                collected = article.get("collected_at", "")
                if collected > date_to + "T23:59:59":
                    continue

            if category:
                if article.get("category", "") != category:
                    continue

            if min_score is not None:
                sr = self._to_search_result(article)
                if sr.relevance_score < min_score:
                    continue

            results.append(self._to_search_result(article))

        results.sort(key=lambda r: r.relevance_score, reverse=True)
        return results[:limit]

    def get_today(self, date: str | None = None) -> list[SearchResult]:
        """获取当日知识条目。

        Args:
            date: 日期 (YYYY-MM-DD)，None 为当天。

        Returns:
            当日条目列表，按相关性降序排列。
        """
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        date_compact = date.replace("-", "")
        pattern = f"github-{date_compact}-*.json"

        results: list[SearchResult] = []
        for file_path in sorted(
            self.knowledge_dir.glob(pattern)
        ):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    article = json.load(f)
                if isinstance(article, dict):
                    results.append(self._to_search_result(article))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(
                    "跳过解析失败的文件 %s: %s", file_path, exc
                )

        results.sort(key=lambda r: r.relevance_score, reverse=True)
        return results

    def get_top(
        self, top_n: int = 5, min_score: float | None = None
    ) -> list[SearchResult]:
        """获取 Top N 高质量条目。

        Args:
            top_n: 返回条数。
            min_score: 最低相关性分数阈值。

        Returns:
            Top N 条目列表。
        """
        all_articles = self._load_all_articles()
        results = [
            self._to_search_result(a) for a in all_articles
        ]

        if min_score is not None:
            results = [
                r for r in results if r.relevance_score >= min_score
            ]

        results.sort(key=lambda r: r.relevance_score, reverse=True)
        return results[:top_n]

    @property
    def total_count(self) -> int:
        """知识库文章总数。"""
        return len(list(self.knowledge_dir.glob("*.json")))


class SubscriptionManager:
    """用户订阅管理器。

    管理用户对标签/分类的订阅关系，
    使用内存 dict 存储，可扩展为持久化存储。

    Attributes:
        subscriptions: user_id → set of topics 的映射。
    """

    def __init__(self) -> None:
        """初始化订阅管理器。"""
        self._subscriptions: dict[str, set[str]] = defaultdict(set)

    def add(self, user_id: str, topic: str) -> bool:
        """添加订阅。

        Args:
            user_id: 用户 ID。
            topic: 订阅主题（标签或分类名）。

        Returns:
            True 表示新增，False 表示已存在。
        """
        if topic in self._subscriptions[user_id]:
            return False
        self._subscriptions[user_id].add(topic)
        logger.info("[Subscription] %s 订阅了 %s", user_id, topic)
        return True

    def remove(self, user_id: str, topic: str) -> bool:
        """取消订阅。

        Args:
            user_id: 用户 ID。
            topic: 订阅主题。

        Returns:
            True 表示成功取消，False 表示未订阅。
        """
        if topic not in self._subscriptions[user_id]:
            return False
        self._subscriptions[user_id].discard(topic)
        logger.info("[Subscription] %s 取消订阅 %s", user_id, topic)
        return True

    def list_topics(self, user_id: str) -> list[str]:
        """列出用户所有订阅。

        Args:
            user_id: 用户 ID。

        Returns:
            订阅主题列表。
        """
        return sorted(self._subscriptions.get(user_id, set()))

    def has_subscription(self, user_id: str, topic: str) -> bool:
        """检查用户是否订阅了指定主题。

        Args:
            user_id: 用户 ID。
            topic: 订阅主题。

        Returns:
            True 表示已订阅。
        """
        return topic in self._subscriptions.get(user_id, set())


class PermissionManager:
    """三级权限控制器。

    权限等级 (由低到高):
        READ   — 搜索、查看
        WRITE  — 订阅管理
        DELETE — 删除条目

    高等级权限自动包含低等级权限。

    Attributes:
        user_permissions: user_id → Permission 的映射。
    """

    _LEVEL_ORDER = {
        Permission.READ: 0,
        Permission.WRITE: 1,
        Permission.DELETE: 2,
    }

    def __init__(self) -> None:
        """初始化权限管理器，默认新用户为 READ 权限。"""
        self._permissions: dict[str, Permission] = {}
        self._default_permission = Permission.READ

    def set_permission(self, user_id: str, permission: Permission) -> None:
        """设置用户权限等级。

        Args:
            user_id: 用户 ID。
            permission: 权限等级。
        """
        self._permissions[user_id] = permission
        logger.info(
            "[Permission] %s 权限设为 %s", user_id, permission.value
        )

    def get_permission(self, user_id: str) -> Permission:
        """获取用户权限等级。

        Args:
            user_id: 用户 ID。

        Returns:
            用户权限等级，未显式设置的用户返回 READ。
        """
        return self._permissions.get(user_id, self._default_permission)

    def can(self, user_id: str, required: Permission) -> bool:
        """检查用户是否满足最低权限要求。

        Args:
            user_id: 用户 ID。
            required: 所需的最低权限等级。

        Returns:
            True 表示权限足够。
        """
        user_level = self._LEVEL_ORDER[
            self.get_permission(user_id)
        ]
        required_level = self._LEVEL_ORDER[required]
        return user_level >= required_level


@dataclass
class BotResponse:
    """Bot 响应结构。

    Attributes:
        text: 回复文本。
        success: 操作是否成功。
        intent: 匹配到的意图。
    """

    text: str
    success: bool = True
    intent: Intent = Intent.UNKNOWN


class KnowledgeBot:
    """知识库 Bot 主入口。

    整合搜索引擎、订阅管理和权限控制，
    提供 handle_message() 统一入口。

    Attributes:
        search_engine: 搜索引擎实例。
        subscription_manager: 订阅管理实例。
        permission_manager: 权限控制实例。
    """

    _SEARCH_PREFIXES = {
        "/search": Intent.SEARCH,
        "/today": Intent.TODAY,
        "/top": Intent.TOP,
        "/subscribe": Intent.SUBSCRIBE,
        "/help": Intent.HELP,
    }

    _NL_KEYWORDS: dict[str, Intent] = {
        "搜索": Intent.SEARCH,
        "查询": Intent.SEARCH,
        "查找": Intent.SEARCH,
        "找": Intent.SEARCH,
        "今天": Intent.TODAY,
        "今日": Intent.TODAY,
        "简报": Intent.TODAY,
        "每日": Intent.TODAY,
        "top": Intent.TOP,
        "排行": Intent.TOP,
        "热门": Intent.TOP,
        "高分": Intent.TOP,
        "订阅": Intent.SUBSCRIBE,
        "取消订阅": Intent.SUBSCRIBE,
        "退订": Intent.SUBSCRIBE,
        "帮助": Intent.HELP,
        "help": Intent.HELP,
        "怎么用": Intent.HELP,
    }

    def __init__(
        self,
        knowledge_dir: str | Path = _DEFAULT_KNOWLEDGE_DIR,
    ) -> None:
        """初始化 KnowledgeBot。

        Args:
            knowledge_dir: 知识条目目录路径。
        """
        self.search_engine = KnowledgeSearchEngine(knowledge_dir)
        self.subscription_manager = SubscriptionManager()
        self.permission_manager = PermissionManager()
        logger.info(
            "[KnowledgeBot] 初始化完成，知识库: %s", knowledge_dir
        )

    def recognize_intent(self, text: str) -> tuple[Intent, str]:
        """识别用户意图。

        优先匹配命令前缀，再匹配自然语言关键词。

        Args:
            text: 用户输入文本。

        Returns:
            (Intent, 参数字符串) 元组。
        """
        stripped = text.strip()
        if not stripped:
            return Intent.UNKNOWN, ""

        lower = stripped.lower()

        for prefix, intent in self._SEARCH_PREFIXES.items():
            if lower.startswith(prefix):
                param = stripped[len(prefix):].strip()
                return intent, param

        for keyword, intent in self._NL_KEYWORDS.items():
            if keyword in lower:
                return intent, stripped

        return Intent.UNKNOWN, stripped

    def handle_message(self, user_id: str, text: str) -> BotResponse:
        """统一消息处理入口。

        根据意图识别结果分发到对应处理器。

        Args:
            user_id: 用户 ID。
            text: 用户输入文本。

        Returns:
            BotResponse 包含回复文本和状态。
        """
        intent, param = self.recognize_intent(text)
        logger.info(
            "[KnowledgeBot] user=%s intent=%s param=%r",
            user_id,
            intent.name,
            param,
        )

        if not self.permission_manager.can(user_id, Permission.READ):
            return BotResponse(
                text="⛔ 权限不足，需要 READ 权限",
                success=False,
                intent=intent,
            )

        handlers = {
            Intent.SEARCH: self._handle_search,
            Intent.TODAY: self._handle_today,
            Intent.TOP: self._handle_top,
            Intent.SUBSCRIBE: self._handle_subscribe,
            Intent.HELP: self._handle_help,
            Intent.UNKNOWN: self._handle_unknown,
        }

        handler = handlers.get(intent, self._handle_unknown)
        return handler(user_id, param)

    def _handle_search(self, user_id: str, param: str) -> BotResponse:
        """处理搜索意图。

        Args:
            user_id: 用户 ID。
            param: 搜索关键词。

        Returns:
            BotResponse。
        """
        keyword = param or ""

        if not keyword:
            stats = (
                f"ℹ️ 知识库共有 {self.search_engine.total_count}"
                f" 篇文章。输入关键词开始搜索，如:\n"
                f"`/search agent` — 搜索 agent 相关内容"
            )
            return BotResponse(text=stats, intent=Intent.SEARCH)

        results = self.search_engine.search(keyword=keyword, limit=5)

        if not results:
            return BotResponse(
                text=f"🔍 未找到与「{keyword}」相关的文章",
                success=True,
                intent=Intent.SEARCH,
            )

        lines = [
            f"🔍 搜索「{keyword}」找到 {len(results)} 条结果:\n"
        ]
        for i, r in enumerate(results, 1):
            score_emoji = (
                "🟢"
                if r.relevance_score >= 0.8
                else "🟡"
                if r.relevance_score >= 0.6
                else "🔴"
            )
            lines.append(
                f"**{i}. {r.title}** {score_emoji} "
                f"({r.relevance_score:.2f})"
            )
            if r.summary:
                summary = (
                    r.summary[:120] + "..."
                    if len(r.summary) > 120
                    else r.summary
                )
                lines.append(summary)
            lines.append(
                f"标签: {' '.join(f'`{t}`' for t in r.tags)} | "
                f"来源: {r.source}"
            )
            if r.source_url:
                lines.append(f"🔗 {r.source_url}")
            lines.append("")

        return BotResponse(
            text="\n".join(lines),
            intent=Intent.SEARCH,
        )

    def _handle_today(self, user_id: str, param: str) -> BotResponse:
        """处理今日简报意图。

        Args:
            user_id: 用户 ID。
            param: 可选的日期参数。

        Returns:
            BotResponse。
        """
        date = param if param else None
        results = self.search_engine.get_today(date=date)

        if not results:
            display_date = date or datetime.now(
                timezone.utc
            ).strftime("%Y-%m-%d")
            return BotResponse(
                text=f"📭 {display_date} 暂无新增知识条目",
                success=True,
                intent=Intent.TODAY,
            )

        display_date = date or datetime.now(timezone.utc).strftime(
            "%Y-%m-%d"
        )

        quality = [
            r for r in results if r.relevance_score >= 0.6
        ]
        display = quality[:5] if quality else results[:5]

        lines = [
            f"# 📰 每日 AI 知识简报 — {display_date}\n",
            f"共收录 {len(results)} 条，精选 Top {len(display)}\n",
        ]

        for i, r in enumerate(display, 1):
            score_emoji = (
                "🟢"
                if r.relevance_score >= 0.8
                else "🟡"
                if r.relevance_score >= 0.6
                else "🔴"
            )
            lines.append(
                f"## {i}. {r.title} {score_emoji}"
            )
            lines.append("")
            lines.append(
                f"- **来源**: {r.source} | "
                f"**相关性**: {r.relevance_score:.2f}"
            )
            if r.tags:
                lines.append(
                    f"- **标签**: {' '.join(f'`{t}`' for t in r.tags)}"
                )
            if r.summary:
                lines.append("")
                lines.append(r.summary)
            if r.source_url:
                lines.append("")
                lines.append(f"[原文链接]({r.source_url})")
            lines.append("")

        return BotResponse(
            text="\n".join(lines),
            intent=Intent.TODAY,
        )

    def _handle_top(self, user_id: str, param: str) -> BotResponse:
        """处理 Top N 排行意图。

        Args:
            user_id: 用户 ID。
            param: 可选的数量参数。

        Returns:
            BotResponse。
        """
        top_n = 5
        if param:
            try:
                top_n = int(param)
                top_n = max(1, min(top_n, 20))
            except ValueError:
                pass

        results = self.search_engine.get_top(
            top_n=top_n, min_score=0.6
        )

        if not results:
            return BotResponse(
                text="📭 暂无符合质量要求的文章",
                success=True,
                intent=Intent.TOP,
            )

        lines = [
            f"🏆 知识库 Top {len(results)} 高分文章:\n"
        ]
        for i, r in enumerate(results, 1):
            score_emoji = (
                "🟢"
                if r.relevance_score >= 0.8
                else "🟡"
                if r.relevance_score >= 0.6
                else "🔴"
            )
            lines.append(
                f"**{i}. {r.title}** {score_emoji} "
                f"({r.relevance_score:.2f})"
            )
            lines.append(
                f"标签: {' '.join(f'`{t}`' for t in r.tags)} | "
                f"来源: {r.source}"
            )
            lines.append("")

        return BotResponse(
            text="\n".join(lines),
            intent=Intent.TOP,
        )

    def _handle_subscribe(
        self, user_id: str, param: str
    ) -> BotResponse:
        """处理订阅管理意图。

        Args:
            user_id: 用户 ID。
            param: 订阅参数字符串。

        Returns:
            BotResponse。
        """
        if not self.permission_manager.can(user_id, Permission.WRITE):
            return BotResponse(
                text="⛔ 订阅功能需要 WRITE 权限，当前仅 READ",
                success=False,
                intent=Intent.SUBSCRIBE,
            )

        param_lower = param.lower() if param else ""

        if not param_lower:
            topics = self.subscription_manager.list_topics(user_id)
            if not topics:
                return BotResponse(
                    text=(
                        "📋 你目前没有订阅任何主题。\n"
                        "用法: `/subscribe agent` — 订阅 agent 标签\n"
                        "用法: `/subscribe -` — 查看已订阅\n"
                        "用法: `/subscribe -agent` — 取消订阅 agent"
                    ),
                    intent=Intent.SUBSCRIBE,
                )
            topic_list = " ".join(f"`{t}`" for t in topics)
            return BotResponse(
                text=f"📋 你的订阅: {topic_list}",
                intent=Intent.SUBSCRIBE,
            )

        if param_lower == "-":
            topics = self.subscription_manager.list_topics(user_id)
            if not topics:
                return BotResponse(
                    text="📋 你目前没有订阅任何主题",
                    intent=Intent.SUBSCRIBE,
                )
            topic_list = " ".join(f"`{t}`" for t in topics)
            return BotResponse(
                text=f"📋 你的订阅: {topic_list}",
                intent=Intent.SUBSCRIBE,
            )

        if param_lower.startswith("-"):
            topic = param_lower[1:]
            removed = self.subscription_manager.remove(user_id, topic)
            if removed:
                return BotResponse(
                    text=f"✅ 已取消订阅 `{topic}`",
                    intent=Intent.SUBSCRIBE,
                )
            return BotResponse(
                text=f"⚠️ 你未订阅 `{topic}`",
                intent=Intent.SUBSCRIBE,
            )

        added = self.subscription_manager.add(user_id, param_lower)
        if added:
            return BotResponse(
                text=f"✅ 已订阅 `{param_lower}`",
                intent=Intent.SUBSCRIBE,
            )
        return BotResponse(
            text=f"⚠️ 你已经订阅了 `{param_lower}`",
            intent=Intent.SUBSCRIBE,
        )

    def _handle_help(self, user_id: str, param: str) -> BotResponse:
        """处理帮助意图。

        Args:
            user_id: 用户 ID。
            param: 忽略。

        Returns:
            BotResponse。
        """
        help_text = (
            "🤖 **AI 知识库 Bot — 使用指南**\n\n"
            "**命令列表:**\n"
            "`/search <关键词>` — 搜索知识条目\n"
            "`/today [日期]` — 查看当日简报\n"
            "`/top [数量]` — 查看 Top N 高分文章\n"
            "`/subscribe [主题]` — 管理订阅\n"
            "`/help` — 显示帮助\n\n"
            "**自然语言也支持:**\n"
            "「搜索 agent」「今天有什么」「热门排行」「订阅 LLM」\n\n"
            f"📊 知识库共 {self.search_engine.total_count} 篇文章"
        )
        return BotResponse(text=help_text, intent=Intent.HELP)

    def _handle_unknown(
        self, user_id: str, param: str
    ) -> BotResponse:
        """处理无法识别的意图。

        Args:
            user_id: 用户 ID。
            param: 用户输入原文。

        Returns:
            BotResponse。
        """
        return BotResponse(
            text=(
                f"🤔 无法理解「{param}」\n"
                f"输入 `/help` 查看可用命令"
            ),
            success=False,
            intent=Intent.UNKNOWN,
        )


def create_bot(
    knowledge_dir: str | Path = _DEFAULT_KNOWLEDGE_DIR,
) -> KnowledgeBot:
    """工厂函数，创建预配置的 KnowledgeBot 实例。

    Args:
        knowledge_dir: 知识条目目录路径。

    Returns:
        KnowledgeBot 实例。
    """
    return KnowledgeBot(knowledge_dir=knowledge_dir)
