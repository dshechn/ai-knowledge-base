---
name: github-trending
description: 当需要采集 GitHub 热门开源项目时使用此技能
allowed-tools:
  - Read
  - Grep
  - Glob
  - WebFetch
---

# GitHub Trending 采集技能

## 使用场景

- 用户要求采集 GitHub 上的热门开源项目
- 需要获取 AI/LLM/Agent 领域的最新技术动态
- 定期更新知识库中的 GitHub Trending 数据

## 执行步骤

### 1. 搜索热门仓库

通过 GitHub API 搜索近期（过去 7 天内创建或更新）的热门仓库，按 stars 排序：

```
GET https://api.github.com/search/repositories?q=stars:>100+pushed:>{7天前日期}&sort=stars&order=desc&per_page=50
```

同时可结合关键词搜索：`AI`、`LLM`、`agent`、`RAG`、`transformer`、`fine-tuning` 等。

### 2. 提取关键信息

从 API 返回结果中提取以下字段：

- 仓库名称（full_name）
- 仓库 URL（html_url）
- 描述（description）
- Star 数量（stargazers_count）
- 主要语言（language）
- Topics 标签（topics）
- 创建时间（created_at）
- 最近更新时间（pushed_at）

### 3. 过滤筛选

**纳入条件：**
- 项目与 AI、LLM、Agent、RAG、NLP、机器学习、深度学习等领域相关
- topics 或 description 中包含相关关键词

**排除条件：**
- Awesome 列表类项目（名称或描述中包含 "awesome-" 或 "awesome list"）
- 纯资源聚合类仓库（无实质性代码）
- Star 数量异常暴涨但内容空洞的仓库

### 4. 去重检查

读取 `knowledge/raw/` 目录下已有的 `github-trending-*.json` 文件，检查本次采集的仓库 URL 是否已存在于近 7 天的采集记录中。已存在的条目跳过，避免重复入库。

### 5. 撰写中文摘要

为每个入选项目撰写简洁的中文摘要，遵循公式：

> **{项目名}** + 做什么 + 为什么值得关注

摘要长度控制在 50-150 字，突出项目的核心功能和技术亮点。

示例：
> LangGraph 是 LangChain 团队推出的多 Agent 工作流编排框架，支持以有向图方式定义复杂的 Agent 协作流程。值得关注的是其原生支持状态持久化和人机交互节点，适合构建生产级 AI 应用。

### 6. 排序并取 Top 5-10

按以下综合权重排序，取前 5-10 个项目：

- Star 数量（权重 40%）
- 与 AI/LLM/Agent 的相关度（权重 35%）
- 项目活跃度（最近更新时间）（权重 25%）

### 7. 输出 JSON 文件

将结果写入 `knowledge/raw/github-trending-YYYY-MM-DD.json`，其中日期为当天日期。

## 输出格式

```json
{
  "source": "github_trending",
  "skill": "github-trending",
  "collected_at": "2025-05-21T10:00:00Z",
  "items": [
    {
      "name": "langchain-ai/langgraph",
      "url": "https://github.com/langchain-ai/langgraph",
      "summary": "LangGraph 是多 Agent 工作流编排框架，支持有向图定义协作流程，原生支持状态持久化，适合生产级 AI 应用。",
      "stars": 12500,
      "language": "Python",
      "topics": ["agent", "langchain", "workflow", "llm"]
    }
  ]
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `source` | string | 固定值 `"github_trending"` |
| `skill` | string | 固定值 `"github-trending"` |
| `collected_at` | string | 采集时间，ISO 8601 格式 |
| `items` | array | 项目列表，5-10 条 |
| `items[].name` | string | 仓库全名（owner/repo） |
| `items[].url` | string | 仓库 GitHub 链接 |
| `items[].summary` | string | AI 生成的中文摘要 |
| `items[].stars` | number | Star 数量 |
| `items[].language` | string | 主要编程语言 |
| `items[].topics` | array | 仓库的 topics 标签列表 |

## 注意事项

1. **Rate Limit**：GitHub API 未认证请求限制为 60 次/小时，认证后为 5000 次/小时。采集时需注意频率控制。
2. **数据时效性**：优先采集最近 7 天内有更新的项目，确保数据新鲜度。
3. **摘要质量**：中文摘要需准确、简洁、有信息量，避免直接翻译 description，应结合 README 内容提炼亮点。
4. **去重严格性**：以仓库 URL 为唯一键进行去重，同一仓库 7 天内不重复采集。
5. **文件命名**：输出文件严格遵循 `github-trending-YYYY-MM-DD.json` 格式，确保日期正确。
6. **禁止删除**：不得删除 `knowledge/` 目录下的任何已有文件，只允许新增。
