---
name: top-rated
description: 当用户要"推荐 / 高分 / 最佳 / score 最高"的知识库文章时触发。基于本地 knowledge/articles/ 目录，不需要联网。
allowed-tools:
  - Read
---

# 高分推荐

## 触发词

- 推荐 / 推荐几个
- 最值得看的 / 最有价值的
- score 最高 / 评分最高 / 高分
- top N / 前 N / 排名

## 做法

### Step 1: 获取文件列表

`Read knowledge/articles/` 获取全部文章文件列表。

> 每个文件名即文章 JSON，直接从列表提取 `id`，无需再 Read 目录。

### Step 2: 读取并排序

对目录中的 JSON 文件逐个 `Read knowledge/articles/{filename}`，读取 `title` / `relevance_score` / `category` / `tags` / `source`。

**注意**: 评分字段可能是 `relevance_score`(0-1 刻度) 或 `score`(0-10 刻度)。
若只有 `score`，将其除以 10 归一化到 0-1 后比较。

1. 过滤: 只保留 `relevance_score >= 0.85` 的文章
2. 排序: 按评分降序
3. 去重: 同一个 `title` 只保留评分最高的一条
4. 取 Top N (用户给数字就用用户的，默认 5)

### Step 3: 回复格式

```markdown
⭐ 高分推荐 Top N:

1. <title> · score <score> · <category> · <source>
   tags: <tag1> <tag2>
   id: <id>

2. ...
```

若不足 N 篇高分文章，如实说明:

```markdown
⭐ 高分推荐

仅找到 M 篇 score >= 0.85 的文章:

1. ...
```

## 禁止

- 别返回低于 0.85 评分的文章（不算高分）
- 同一 title 只保留评分最高的一条
- 别联网搜索，只用本地 knowledge/ 目录
