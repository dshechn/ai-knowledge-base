"""knowledge_bot.py 手动测试脚本。

用法:
    python3 test_bot.py
"""

from bot.knowledge_bot import (
    Intent,
    KnowledgeBot,
    Permission,
)
from pathlib import Path

_KNOWLEDGE_DIR = str(
    Path(__file__).resolve().parent.parent / "knowledge" / "articles"
)

bot = KnowledgeBot(knowledge_dir=_KNOWLEDGE_DIR)

# ── 意图识别测试 ──────────────────────────────────
intent_tests = [
    ("/search MCP", Intent.SEARCH, "MCP"),
    ("/today", Intent.TODAY, ""),
    ("/today 2026-06-20", Intent.TODAY, "2026-06-20"),
    ("/top", Intent.TOP, ""),
    ("/top 10", Intent.TOP, "10"),
    ("/subscribe agent", Intent.SUBSCRIBE, "agent"),
    ("/subscribe", Intent.SUBSCRIBE, ""),
    ("/subscribe -agent", Intent.SUBSCRIBE, "-agent"),
    ("/help", Intent.HELP, ""),
    ("搜索 Agent 文章", Intent.SEARCH, "搜索 Agent 文章"),
    ("今天有什么新内容", Intent.TODAY, "今天有什么新内容"),
    ("热门排行", Intent.TOP, "热门排行"),
    ("订阅 LLM", Intent.SUBSCRIBE, "订阅 LLM"),
    ("帮助", Intent.HELP, "帮助"),
    ("随便聊聊", Intent.UNKNOWN, "随便聊聊"),
]

print("=" * 60)
print("意图识别测试")
print("=" * 60)
for text, expected_intent, _ in intent_tests:
    intent, args = bot.recognize_intent(text)
    status = "✅" if intent == expected_intent else "❌"
    print(f'{status} "{text}" → {intent.name} (args={args!r})')

# ── 权限测试 ──────────────────────────────────────
print()
print("=" * 60)
print("权限控制测试")
print("=" * 60)

user_a, user_b = "alice", "bob"
print(f"默认权限: {bot.permission_manager.get_permission(user_a)}")
print(f"alice can READ:  {bot.permission_manager.can(user_a, Permission.READ)}")
print(f"alice can WRITE: {bot.permission_manager.can(user_a, Permission.WRITE)}")

bot.permission_manager.set_permission(user_a, Permission.WRITE)
print(f"升级后 alice can WRITE: {bot.permission_manager.can(user_a, Permission.WRITE)}")

resp = bot.handle_message(user_b, "/subscribe agent")
print(f"bob(READ) 订阅: {resp.success} — {resp.text}")

# ── Bot 消息处理测试 ──────────────────────────────
print()
print("=" * 60)
print("消息处理测试")
print("=" * 60)

messages = [
    "/help",
    "/today 2026-06-20",
    "/search langgraph",
    "/search",
    "/top 3",
    "热门排行",
    "随便聊聊",
]

for text in messages:
    print(f"\n>>> {text}")
    resp = bot.handle_message(user_a, text)
    print(f"    success={resp.success} intent={resp.intent.name}")
    print(f"    {resp.text[:200]}{'...' if len(resp.text) > 200 else ''}")

# ── 订阅流程测试 ──────────────────────────────────
print()
print("=" * 60)
print("订阅流程测试")
print("=" * 60)

resp = bot.handle_message(user_a, "/subscribe agent")
print(f"订阅 agent: {resp.text}")

resp = bot.handle_message(user_a, "/subscribe llm")
print(f"订阅 llm:   {resp.text}")

resp = bot.handle_message(user_a, "/subscribe")
print(f"查看订阅:   {resp.text}")

resp = bot.handle_message(user_a, "/subscribe -agent")
print(f"取消 agent: {resp.text}")

resp = bot.handle_message(user_a, "/subscribe")
print(f"查看订阅:   {resp.text}")

print()
print("=" * 60)
print("测试完成")
print("=" * 60)
