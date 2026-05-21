# AGENTS.md

## 项目概述

AI Knowledge Base Assistant 是一个自动化知识采集与分发系统。它从 GitHub Trending 和 Hacker News 等源站自动抓取 AI/LLM/Agent 领域的技术动态，经由大模型分析后生成结构化摘要，以 JSON 格式存储于本地知识库，并支持通过 Telegram Bot 和飞书 Webhook 等渠道进行多端分发。

---

## 技术栈

| 层级 | 技术选型 |
|------|----------|
| 运行时 | Python 3.12 |
| AI 编排 | OpenCode + 国产大模型（DeepSeek / Qwen） |
| 工作流 | LangGraph（多 Agent 状态图编排） |
| 爬虫框架 | OpenClaw |
| 数据格式 | JSON |
| 分发渠道 | Telegram Bot API、飞书 Webhook |

---

## 编码规范

1. **代码风格**：严格遵循 PEP 8，行宽上限 88 字符（与 Black 兼容）。
2. **命名约定**：所有变量、函数、模块使用 `snake_case`；类名使用 `PascalCase`；常量使用 `UPPER_SNAKE_CASE`。
3. **文档字符串**：使用 Google 风格 docstring，所有公开函数和类必须编写 docstring。
4. **日志输出**：禁止使用裸 `print()` 语句，统一使用 `logging` 模块或项目封装的 logger。
5. **类型标注**：所有函数签名必须包含类型注解（参数 + 返回值）。
6. **导入顺序**：标准库 → 第三方库 → 本地模块，各组之间空一行。

---

## 项目结构

```
ai-knowledge-base/
├── AGENTS.md                   # 本文件 — Agent 行为规范
├── README.md                   # 项目说明
├── .opencode/
│   ├── agents/                 # OpenCode Agent 定义
│   │   ├── collector.yaml      # 采集 Agent
│   │   ├── analyzer.yaml       # 分析 Agent
│   │   └── curator.yaml        # 整理 Agent
│   └── skills/                 # OpenCode Skills 定义
│       ├── crawl_github.py
│       ├── crawl_hackernews.py
│       ├── summarize.py
│       └── distribute.py
├── knowledge/
│   ├── raw/                    # 原始采集数据（未经 AI 处理）
│   └── articles/               # AI 分析后的结构化知识条目
├── src/                        # 核心业务逻辑
│   ├── crawlers/               # 爬虫模块
│   ├── analyzers/              # AI 分析模块
│   ├── distributors/           # 分发模块
│   └── utils/                  # 工具函数
├── tests/                      # 单元测试
├── pyproject.toml              # 项目依赖与元数据
└── .env.example                # 环境变量模板
```

---

## 知识条目 JSON 格式

每条知识条目存储在 `knowledge/articles/` 目录下，文件名格式为 `{id}.json`。

```json
{
  "id": "20250521-github-001",
  "title": "LangGraph 发布 v0.3：支持多 Agent 协作",
  "source": "github_trending",
  "source_url": "https://github.com/langchain-ai/langgraph",
  "published_at": "2025-05-21T08:30:00Z",
  "collected_at": "2025-05-21T10:00:00Z",
  "summary": "LangGraph v0.3 引入了原生多 Agent 协作机制，支持...",
  "tags": ["langgraph", "multi-agent", "workflow"],
  "category": "framework",
  "relevance_score": 0.92,
  "status": "published",
  "distributed_to": ["telegram", "feishu"]
}
```

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | string | 是 | 唯一标识，格式：`YYYYMMDD-{source}-{seq}` |
| `title` | string | 是 | 条目标题 |
| `source` | string | 是 | 来源标识：`github_trending` / `hackernews` |
| `source_url` | string | 是 | 原文链接 |
| `published_at` | string | 否 | 原文发布时间（ISO 8601） |
| `collected_at` | string | 是 | 采集时间（ISO 8601） |
| `summary` | string | 是 | AI 生成的中文摘要（200-500 字） |
| `tags` | array | 是 | 标签列表，小写英文，至少 1 个 |
| `category` | string | 是 | 分类：`framework` / `model` / `paper` / `tool` / `tutorial` |
| `relevance_score` | float | 是 | AI 评分的相关性得分（0.0-1.0） |
| `status` | string | 是 | 状态：`draft` / `reviewed` / `published` / `archived` |
| `distributed_to` | array | 否 | 已分发的渠道列表 |

---

## Agent 角色概览

| 角色 | 名称 | 职责 | 输入 | 输出 |
|------|------|------|------|------|
| 采集 Agent | `collector` | 从 GitHub Trending 和 Hacker News 抓取 AI/LLM/Agent 相关内容的标题、链接、描述 | 数据源 URL + 过滤关键词 | `knowledge/raw/` 下的原始 JSON 文件 |
| 分析 Agent | `analyzer` | 调用大模型对原始数据进行摘要生成、标签提取、相关性评分、分类 | `knowledge/raw/` 中的原始数据 | `knowledge/articles/` 下的结构化知识条目 |
| 整理 Agent | `curator` | 去重、质量审核、状态流转管理，并触发多渠道分发 | `knowledge/articles/` 中的条目 | 更新条目 status 字段 + 触发分发 |

---

## 红线（绝对禁止的操作）

以下操作在任何情况下都 **禁止执行**：

1. **禁止硬编码密钥**：API Key、Token、密码等敏感信息绝不允许出现在代码或配置文件中，必须通过环境变量注入。
2. **禁止未经过滤的用户输入拼接**：所有外部输入必须经过校验和清洗，防止注入攻击。
3. **禁止删除 `knowledge/` 目录下的已有数据**：只允许追加和更新状态，不允许物理删除知识条目文件。
4. **禁止绕过 status 状态机**：条目状态只能按 `draft → reviewed → published → archived` 流转，不允许跳跃或逆向。
5. **禁止无限制并发请求**：爬虫必须设置合理的 rate limit（建议 ≤ 2 req/s），不得对目标站点造成压力。
6. **禁止在 Agent 中使用裸 `print()`**：所有输出必须通过 logging 模块。
7. **禁止提交包含 `.env` 文件的 commit**：`.env` 必须在 `.gitignore` 中。
8. **禁止未经测试直接推送到 main 分支**：所有变更需通过 PR 流程。
