---
name: knowledge-query
description: 当用户询问知识库内容时使用此技能，包括统计信息、关键词搜索、文件数量、标签分布等
allowed-tools:
  - Read
  - Grep
  - Glob
  - knowledge_search_articles
  - knowledge_get_article
  - knowledge_knowledge_stats
---

# 知识库查询技能

## 数据目录

本技能在 `v4-production/openclaw/` 工作区运行，`knowledge/` 为软链接，指向项目根目录的实际知识库。

| 路径 | 用途 |
|------|------|
| `knowledge/articles/*.json` | AI 分析后的结构化文章 |
| `knowledge/raw/*.json` | 原始采集数据 |

## 使用场景

- 用户询问知识库的统计信息（文件数量、来源分布、热门标签等）
- 用户按关键词搜索知识库中的文章（如"关于 agent 的文章"）
- 用户需要查看某篇文章的完整内容
- 用户询问特定主题的知识覆盖情况
- 用户询问近期入库的技术动态

## 执行步骤

### 1. 识别查询类型

根据用户问题判断属于哪种查询：

| 查询类型 | 示例 | 使用工具 |
|----------|------|----------|
| 统计查询 | "知识库一共多少文件？" | `knowledge_knowledge_stats` + `Glob` |
| 关键词搜索 | "知识库有多少关于 agent 的？" | `knowledge_search_articles` |
| 文章详情 | "看一下那篇 LangGraph 的文章" | `knowledge_get_article` |
| 文件数量 | "articles 目录下有多少文件？" | `Glob` 列出 `knowledge/articles/*.json` |
| 内容检索 | "有哪些文章提到了 RAG？" | `Grep` 在 `knowledge/articles/` 中搜索 |

### 2. 统计查询

对于统计类问题，优先使用 `knowledge_knowledge_stats` 获取：
- 文章总数
- 来源分布（github_trending / hackernews）
- 热门标签 Top N
- 平均评分

对于文件数量类问题，使用 `Glob` 工具：
```
Glob 模式: knowledge/articles/*.json    → 统计文章数
Glob 模式: knowledge/raw/*.json         → 统计原始采集文件数
```

### 3. 关键词搜索

对于主题/关键词类问题，使用 `knowledge_search_articles`：
```
keyword: "agent"    → 搜索含 agent 标签或标题摘要包含 agent 的文章
limit: 10           → 默认返回 5 条，可调整
```

搜索结果会返回匹配文章的 ID、标题、摘要、标签等信息。如用户要求了解详情，再用 `knowledge_get_article` 获取全文。

### 4. 结果呈现

回答时遵循以下格式：

- 先给出**总数**（如"知识库中共有 42 篇文章"）
- 再列出**匹配结果**摘要（标题 + 一句话描述 + 标签）
- 如果结果较多（>10 条），只列出前 10 条并提示还有更多
- 可根据用户需求补充统计分布（来源、分类、评分分布等）

### 5. 回答示例

**用户问**："知识库有多少关于 agent 的文章？"

**回答格式**：

```
知识库中与 "agent" 相关的文章共 15 篇，占文章总数的 35.7%。

Top 5:
1. [20250521-github-001] LangGraph v0.3：支持多 Agent 协作 — 多 Agent 工作流编排框架
   标签: agent, workflow, orchestration | 评分: 9.2
2. [20250520-github-003] CrewAI：多 Agent 协作框架 ...
   ...
```

**用户问**："知识库一共多少文件？"

**回答格式**：

```
知识库概况：
- 文章总数: 42 篇 (knowledge/articles/)
- 原始采集文件: 8 个 (knowledge/raw/)
- 来源: GitHub Trending (35), Hacker News (7)
- 平均评分: 7.8/10
- 热门标签: agent, llm, rag, workflow, python
```

## 注意事项

1. **优先使用专用工具**：`knowledge_knowledge_stats` 比手动遍历文件更高效，应优先使用。
2. **搜索前预处理**：中文搜索时提取核心关键词，英文搜索时使用小写词根。
3. **禁止删除**：不得删除 `knowledge/` 目录下的任何文件，只读操作。
4. **结果数控制**：如果匹配结果较多，默认展示前 10 条，询问用户是否查看更多。
5. **统计精确**：文件数量以 `Glob` 结果为准，统计信息以 `knowledge_knowledge_stats` 为准。
