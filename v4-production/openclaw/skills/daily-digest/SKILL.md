---
name: daily-digest
description: 生成今日 AI 技术简报，汇总当天采集的 Top 5 知识条目，按相关性排序
allowed-tools:
  - Read
---

# 每日简报技能

## 触发条件

当用户想要查看今日 / 本周 AI 技术汇总时激活。
典型触发词：简报、摘要、今日、daily、digest、briefing

## 生成流程

> **重要：只允许 Read**。本技能不能用 Glob / Grep / Bash。所有操作从 Read 开始。

### Step 1: 定位今日数据

用 `Read` 读 `knowledge/articles/` 目录，在内存中筛选以今日日期（YYYYMMDD）为前缀的 JSON 文件。

使用 `Read knowledge/articles/` 列出文件列表，然后筛选前缀 `github-{YYYYMMDD}-` 匹配的文件。
今日无数据则回退到最近 7 天。

> **不要尝试 Glob 或 grep**：Read 目录即可获取文件列表，一次 Read 就够了。

### Step 2: 内存过滤 + 排序

对筛选出的文件，逐个 `Read knowledge/articles/{filename}.json` 获取完整内容：

1. 过滤 `relevance_score`（若为 0-1 刻度则 >= 0.6；若为 0-10 刻度则 >= 6）
2. 按 relevance 降序排序
3. 取 Top 5

**只读最终要进简报的 Top 5**，不要批量读全部。

### Step 3: 按 category 分组生成 Markdown 简报

输出格式：

```markdown
# 📰 每日 AI 知识简报 — YYYY-MM-DD

共收录 N 条，精选 Top 5

## 1. 标题 🟢
- **来源**: github_trending | **相关性**: 0.92
- **标签**: agent, workflow, orchestration

摘要内容...

[原文链接](https://...)

---

## 2. 标题 🟡
...
```

评分 emoji 规则：
- >= 0.8 → 🟢（高质量）
- >= 0.6 → 🟡（中等）
- < 0.6 → 🔴（低质量，已在过滤阶段剔除）

## 与 Publisher 的分工

- **本 Skill**：格式化文本，生成 Markdown 简报并直接返回给用户
- **distribution/publisher.py**：把简报推送到 Telegram / 飞书

Skill 负责"写"，Publisher 负责"发"。

如需推送到飞书等渠道，在生成简报后提示用户可执行：
```bash
python3 daily_digest.py --date YYYY-MM-DD
```
