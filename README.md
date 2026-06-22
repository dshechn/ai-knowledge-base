# AI 知识库系统

> 基于多 Agent 协作的 AI 技术知识库——自动采集、智能分析、定时推送

---

## 架构概览

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           AI 知识库系统 架构                              │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                      分发层 (Distribution)                          │ │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │ │
│  │  │ Telegram Bot │  │ 飞书 Webhook │  │  MCP Knowledge Server    │  │ │
│  │  │  (实时推送)   │  │  (批量通知)   │  │  (本地知识库查询 API)     │  │ │
│  │  └──────────────┘  └──────────────┘  └──────────────────────────┘  │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                    │                                     │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                      工程层 (Engineering)                            │ │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌──────────────┐ │ │
│  │  │  Hooks     │  │  Tests     │  │  Patterns  │  │  GitHub       │ │ │
│  │  │  质量检查   │  │  测试/评估  │  │  Router    │  │  Actions      │ │ │
│  │  │  JSON 校验  │  │  安全审计   │  │ Supervisor │  │  CI/CD       │ │ │
│  │  └────────────┘  └────────────┘  └────────────┘  └──────────────┘ │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                    │                                     │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                      Pipeline 层 (Workflow)                         │ │
│  │  ┌──────────────────────────────────────────────────────────────┐  │ │
│  │  │                   LangGraph 状态图编排                          │  │ │
│  │  │  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐  │  │ │
│  │  │  │ Collector │ → │ Analyzer │ → │ Reviewer │ → │ Reviser  │  │  │ │
│  │  │  │   采集节点  │   │   分析节点  │   │   审核节点  │   │   修订节点  │  │  │ │
│  │  │  └──────────┘   └──────────┘   └──────────┘   └──────────┘  │  │ │
│  │  └──────────────────────────────────────────────────────────────┘  │  │
│  │  ┌─────────────────────┐  ┌───────────────────────────────────┐   │  │
│  │  │  OpenClaw 爬虫引擎   │  │  RSS 多源订阅 (rss_sources.yaml)   │   │  │
│  │  └─────────────────────┘  └───────────────────────────────────┘   │  │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                    │                                     │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                       Agent 层 (AI 编排)                             │ │
│  │  ┌────────────────┐  ┌────────────────┐  ┌────────────────────┐   │ │
│  │  │   collector    │  │   analyzer     │  │    organizer       │   │ │
│  │  │   采集 Agent    │  │   分析 Agent    │  │    整理 Agent       │   │ │
│  │  │                │  │                │  │                    │   │ │
│  │  │  GitHub Trending│  │  摘要生成      │  │  去重检查           │   │ │
│  │  │  Hacker News   │  │  标签提取      │  │  质量审核           │   │ │
│  │  │  RSS 源        │  │  相关性评分    │  │  状态流转           │   │ │
│  │  │                │  │  分类归档      │  │  触发分发           │   │ │
│  │  └────────────────┘  └────────────────┘  └────────────────────┘   │ │
│  │                        Skills 技能包                                 │ │
│  │  ┌──────────────────────┐  ┌──────────────────────────────────┐   │ │
│  │  │   github-trending    │  │        tech-summary              │   │ │
│  │  │   GitHub 热门采集     │  │   技术内容深度分析总结             │   │ │
│  │  └──────────────────────┘  └──────────────────────────────────┘   │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 目录结构

| 目录 / 文件 | 说明 | 版本 / 状态 |
|---|---|---|
| `.opencode/agents/` | OpenCode Agent 定义（collector / analyzer / organizer） | V4 |
| `.opencode/skills/` | OpenCode Skills 技能包（github-trending / tech-summary） | V4 |
| `.opencode/plugins/` | OpenCode 插件（validate-articles.ts） | V4 |
| `.github/workflows/` | GitHub Actions CI/CD | V4 |
| `pipeline/` | 数据管道（LangGraph 编排 + RSS 多源配置） | V3 - V4 |
| `workflows/` | 工作流核心（graph / nodes / planner / reviewer / reviser） | V3 |
| `v4-production/` | V4 生产环境完整代码 | V4 |
| `v4-production/bot/` | Telegram Bot 实现（实时推送） | V4 |
| `v4-production/openclaw/` | OpenClaw 爬虫引擎（daily_digest / distribution / skills） | V4 |
| `v4-production/workflows/` | V4 工作流（增强版 graph / nodes / planner） | V4 |
| `v4-production/pipeline/` | V4 数据管道 | V4 |
| `v4-production/patterns/` | V4 设计模式（Router / Supervisor） | V4 |
| `v4-production/hooks/` | V4 质量钩子（check_quality / validate_json） | V4 |
| `v4-production/tests/` | V4 测试套件（评估 / 安全 / 成本监控） | V4 |
| `v4-production/scripts/` | V4 运维脚本 | V4 |
| `hooks/` | 全局质量钩子（check_quality / validate_json） | V3 |
| `patterns/` | 通用设计模式（Router / Supervisor） | V3 |
| `tests/` | 通用测试套件 | V3 |
| `knowledge/raw/` | 原始采集数据（未经 AI 处理） | V1+ |
| `knowledge/articles/` | AI 分析后的结构化知识条目（JSON） | V1+ |
| `mcp_knowledge_server.py` | MCP 知识库搜索服务（JSON-RPC over stdio） | V4 |
| `opencode.json` | OpenCode 配置文件（权限 / MCP） | V4 |
| `requirements.txt` | Python 依赖清单 | V4 |
| `AGENTS.md` | Agent 行为规范与编码约定 | V4 |

---

## 技术栈

| 层级 | 技术选型 | 说明 |
|---|---|---|
| AI 编排 | **OpenCode** | 多 Agent 协作框架，定义 Agent 角色与 Skills |
| 工作流引擎 | **LangGraph** | 有状态 DAG 工作流编排，支撑采集→分析→审核→修订全过程 |
| 大语言模型 | **DeepSeek / Qwen** | 国产大模型，用于摘要生成、标签提取、相关性评分 |
| 爬虫框架 | **OpenClaw** | 多源内容采集引擎 |
| 运行时 | **Python 3.12** | 主力开发语言 |
| 数据格式 | **JSON** | 知识条目标准存储格式 |
| 容器化 | **Docker** | 生产环境部署 |
| 分发渠道 | **Telegram Bot API** | 实时消息推送 |
| 分发渠道 | **飞书 Webhook** | 批量通知推送 |
| 协议服务 | **MCP (JSON-RPC 2.0)** | 知识库查询服务协议 |
| CI/CD | **GitHub Actions** | 自动化测试与部署 |

---

## 版本历史

### V1 — 基础采集 (Foundation)

- 手动触发采集 GitHub Trending / Hacker News
- 基于关键词规则过滤 AI/LLM 相关内容
- 原始数据以 JSON 格式存储于 `knowledge/raw/`
- 单一 Agent 角色（collector）

### V2 — AI 分析 (Intelligence)

- 引入大模型（DeepSeek）进行自动摘要生成
- 标签自动提取与相关性评分
- 结构化知识条目输出至 `knowledge/articles/`
- 新增 analyzer Agent

### V3 — 工作流编排 (Orchestration)

- 基于 LangGraph 构建多节点工作流（采集 → 分析 → 审核 → 修订）
- 引入 reviewer / reviser 质检环节，提升内容质量
- 支持 Router / Supervisor 设计模式
- Hooks 质量钩子（JSON 校验 / 质量检查）
- 新增 organizer Agent（去重 / 状态流转）

### V4 — 全自动生产 (Production)

- OpenClaw 爬虫引擎集成，支持多源 RSS 订阅
- Telegram Bot 实时推送与飞书 Webhook 批量分发
- MCP Knowledge Server 本地知识库查询服务
- Docker 容器化部署
- GitHub Actions CI/CD 全流程自动化
- 定时调度（daily_digest）+ 成本监控
- 完整测试套件（评估 / 安全 / 注入检测）

---

## 月度成本估算

| 项目 | 单价 | 月用量估算 | 月费用 (CNY) |
|---|---|---|---|
| DeepSeek API (V3) | ¥1 / 1M tokens | ~5M tokens / 月 | **¥5** |
| 备用大模型 (Qwen) | ¥2 / 1M tokens | ~1M tokens / 月 | **¥2** |
| 云服务器 (2C4G) | ¥50 - 80 / 月 | 1 台 | **¥50 - 80** |
| **合计** | | | **≈ ¥57 - 87** |

> 注：实际费用取决于采集频率、文章数量和摘要长度。以上按每日采集 10 条、每条摘要约 300 tokens 估算。

---

## License

MIT
