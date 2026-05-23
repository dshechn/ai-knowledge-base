---
name: tech-summary
description: 当需要对采集的技术内容进行深度分析总结时使用此技能
allowed-tools:
  - Read
  - Grep
  - Glob
  - WebFetch
---

# 技术内容深度分析技能

## 使用场景

- 对 `knowledge/raw/` 中已采集的原始数据进行深度分析
- 需要为每条技术动态生成评分、亮点、标签建议
- 需要发现多条内容之间的技术趋势和共同主题
- 将分析结果结构化输出，供后续整理 Agent 使用

## 执行步骤

### 1. 读取最新采集文件

读取 `knowledge/raw/` 目录下最新的采集文件（按文件名日期排序取最近一份）：

```
knowledge/raw/github-trending-YYYY-MM-DD.json
```

解析 JSON，提取 `items` 数组中的所有条目作为分析输入。

### 2. 逐条深度分析

对每条采集内容执行以下分析，必要时通过 WebFetch 访问项目 README 获取补充信息：

#### a. 精炼摘要

- 长度限制：**不超过 50 字**
- 要求：一句话说清楚项目是什么、解决什么问题
- 格式：`{项目名} — {核心功能/定位}`

示例：
> LangGraph — 基于有向图的多 Agent 工作流编排框架

#### b. 技术亮点

- 提取 **2-3 个**具体技术亮点
- 用事实说话，避免空泛形容词
- 每条亮点需包含可验证的技术细节

示例：
```json
[
  "原生支持状态持久化，Agent 执行可中断恢复",
  "内置人机交互节点，支持 Human-in-the-loop 审批流程",
  "与 LangChain 生态无缝集成，支持 100+ Tool 调用"
]
```

#### c. 评分（1-10）

按以下标准打分，**必须附上一句评分理由**：

| 分数区间 | 含义 | 典型特征 |
|----------|------|----------|
| 9-10 | 改变格局 | 开创新范式、解决行业级难题、可能重塑工作流 |
| 7-8 | 直接有帮助 | 可立即用于生产、显著提升效率、填补重要空白 |
| 5-6 | 值得了解 | 有学习价值、代表某种趋势、但暂无直接应用场景 |
| 1-4 | 可略过 | 同质化严重、缺乏创新、或与 AI/LLM/Agent 相关性低 |

**约束：15 个项目中，9-10 分不超过 2 个。** 高分必须有充分理由，宁可保守不可通胀。

#### d. 标签建议

- 推荐 2-5 个标签，小写英文
- 优先使用已有标签体系中的词汇（从 `knowledge/articles/` 已有文件中提取）
- 新标签需具备通用性，避免过于具体

### 3. 趋势发现

在完成逐条分析后，综合所有条目进行宏观趋势识别：

#### a. 共同主题

- 识别本批次中 3 个以上项目涉及的共同技术方向
- 用一句话概括该趋势

示例：
> 本周多个项目聚焦 "Agent 编排"，从 LangGraph 到 CrewAI 均在探索多 Agent 协作的标准化接口。

#### b. 新兴概念

- 识别首次出现或近期快速升温的技术概念
- 简要解释其含义和潜在影响

示例：
> "MCP (Model Context Protocol)" 概念集中涌现，试图为 LLM 工具调用建立统一协议层。

### 4. 输出分析结果

将完整分析结果写入 `knowledge/raw/tech-summary-YYYY-MM-DD.json`。

## 输出格式

```json
{
  "source": "tech_summary",
  "skill": "tech-summary",
  "analyzed_at": "2025-05-21T12:00:00Z",
  "input_file": "github-trending-2025-05-21.json",
  "analyses": [
    {
      "name": "langchain-ai/langgraph",
      "url": "https://github.com/langchain-ai/langgraph",
      "summary": "LangGraph — 基于有向图的多 Agent 工作流编排框架",
      "highlights": [
        "原生支持状态持久化，Agent 执行可中断恢复",
        "内置人机交互节点，支持 Human-in-the-loop 审批流程",
        "与 LangChain 生态无缝集成，支持 100+ Tool 调用"
      ],
      "score": 8,
      "score_reason": "多 Agent 编排已成刚需，LangGraph 提供了生产级解决方案且生态成熟",
      "tags": ["agent", "workflow", "langchain", "orchestration"]
    }
  ],
  "trends": {
    "common_themes": [
      "本周多个项目聚焦 Agent 编排，探索多 Agent 协作的标准化接口"
    ],
    "emerging_concepts": [
      "MCP (Model Context Protocol) 概念集中涌现，试图为 LLM 工具调用建立统一协议层"
    ]
  }
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `source` | string | 固定值 `"tech_summary"` |
| `skill` | string | 固定值 `"tech-summary"` |
| `analyzed_at` | string | 分析完成时间，ISO 8601 格式 |
| `input_file` | string | 输入的原始采集文件名 |
| `analyses` | array | 逐条分析结果 |
| `analyses[].name` | string | 项目全名 |
| `analyses[].url` | string | 项目链接 |
| `analyses[].summary` | string | 精炼摘要，不超过 50 字 |
| `analyses[].highlights` | array | 技术亮点，2-3 条 |
| `analyses[].score` | number | 评分 1-10 |
| `analyses[].score_reason` | string | 评分理由，一句话 |
| `analyses[].tags` | array | 标签建议，2-5 个 |
| `trends` | object | 趋势发现 |
| `trends.common_themes` | array | 共同主题列表 |
| `trends.emerging_concepts` | array | 新兴概念列表 |

## 注意事项

1. **评分纪律**：严格遵守 "9-10 分不超过 2 个" 的约束，评分通胀会降低知识库的筛选价值。
2. **摘要克制**：50 字以内必须说清楚，多一个字都是冗余。不用"非常"、"极其"等程度副词。
3. **亮点用事实**：禁止使用 "功能强大"、"性能优异" 等空泛描述，必须给出具体技术细节或数据。
4. **标签复用**：优先复用已有标签，保持知识库标签体系的一致性和可聚合性。
5. **趋势客观**：趋势发现基于本批次数据的客观归纳，不做过度推测或预测。
6. **禁止删除**：不得删除 `knowledge/` 目录下的任何已有文件，只允许新增。
