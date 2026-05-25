#!/usr/bin/env python3
"""知识条目 5 维度质量评分脚本。

用法:
    python hooks/check_quality.py <json_file> [json_file2 ...]
    python hooks/check_quality.py data/*.json

等级标准: A >= 80, B >= 60, C < 60
退出码: 存在 C 级返回 1，否则返回 0。
"""

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ============================================================
# 常量配置
# ============================================================

# 标准标签列表
STANDARD_TAGS = {
    "python", "javascript", "typescript", "rust", "go", "java", "c++",
    "ai", "ml", "llm", "deep-learning", "nlp", "computer-vision",
    "web", "frontend", "backend", "fullstack", "api", "rest", "graphql",
    "database", "sql", "nosql", "redis", "postgresql", "mongodb",
    "devops", "docker", "kubernetes", "ci-cd", "cloud", "aws", "azure",
    "security", "cryptography", "authentication",
    "architecture", "microservices", "distributed-systems",
    "testing", "performance", "monitoring", "observability",
    "open-source", "tooling", "cli", "editor",
    "algorithm", "data-structure", "concurrency", "networking",
    "mobile", "ios", "android", "flutter", "react-native",
}

# 技术关键词（摘要奖励用）
TECH_KEYWORDS = {
    "算法", "架构", "性能", "并发", "分布式", "微服务", "容器",
    "模型", "训练", "推理", "向量", "索引", "缓存", "队列",
    "API", "SDK", "框架", "协议", "加密", "认证", "授权",
    "algorithm", "architecture", "performance", "concurrency",
    "distributed", "microservice", "container", "model", "inference",
}

# 空洞词黑名单
HOLLOW_WORDS_CN = [
    "赋能", "抓手", "闭环", "打通", "全链路", "底层逻辑",
    "颗粒度", "对齐", "拉通", "沉淀", "强大的", "革命性的",
]

HOLLOW_WORDS_EN = [
    "groundbreaking", "revolutionary", "game-changing", "cutting-edge",
    "disruptive", "synergy", "leverage", "paradigm-shifting",
    "best-in-class", "world-class", "next-generation",
]

HOLLOW_WORDS_ALL = HOLLOW_WORDS_CN + HOLLOW_WORDS_EN

# ID 格式正则
ID_PATTERN = re.compile(r"^[a-z]+-\d{8}-\d{3}$")

# URL 格式正则
URL_PATTERN = re.compile(r"^https?://.+")

# 合法 status 值
VALID_STATUSES = {"draft", "review", "published", "archived"}

# 等级阈值
GRADE_A_THRESHOLD = 80
GRADE_B_THRESHOLD = 60

# 进度条宽度
BAR_WIDTH = 20


# ============================================================
# 数据结构
# ============================================================

@dataclass
class DimensionScore:
    """单维度评分结果。"""

    name: str
    score: float
    max_score: float
    details: list[str] = field(default_factory=list)

    @property
    def percentage(self) -> float:
        """得分百分比。"""
        if self.max_score == 0:
            return 0.0
        return self.score / self.max_score * 100


@dataclass
class QualityReport:
    """质量评估报告。"""

    file_path: Path
    dimensions: list[DimensionScore] = field(default_factory=list)

    @property
    def total_score(self) -> float:
        """加权总分（满分 100）。"""
        return sum(d.score for d in self.dimensions)

    @property
    def max_total(self) -> float:
        """满分总计。"""
        return sum(d.max_score for d in self.dimensions)

    @property
    def grade(self) -> str:
        """等级: A/B/C。"""
        score = self.total_score
        if score >= GRADE_A_THRESHOLD:
            return "A"
        elif score >= GRADE_B_THRESHOLD:
            return "B"
        else:
            return "C"


# ============================================================
# 评分维度函数
# ============================================================

def score_summary_quality(data: dict) -> DimensionScore:
    """摘要质量评分（满分 25 分）。"""
    dim = DimensionScore(name="摘要质量", score=0.0, max_score=25.0)

    summary = data.get("summary", "")
    if not isinstance(summary, str):
        dim.details.append("summary 字段类型异常")
        return dim

    length = len(summary)

    # 长度评分（满分 18 分）
    if length >= 50:
        length_score = 18.0
        dim.details.append(f"摘要 {length} 字, 达到优秀标准")
    elif length >= 20:
        length_score = 10.0
        dim.details.append(f"摘要 {length} 字, 达到基本标准")
    elif length > 0:
        length_score = 4.0
        dim.details.append(f"摘要 {length} 字, 过短")
    else:
        length_score = 0.0
        dim.details.append("摘要为空")

    # 技术关键词奖励（满分 7 分）
    found_keywords = [kw for kw in TECH_KEYWORDS if kw in summary]
    keyword_count = len(found_keywords)
    if keyword_count >= 3:
        keyword_score = 7.0
        dim.details.append(f"含 {keyword_count} 个技术关键词 (优秀)")
    elif keyword_count >= 1:
        keyword_score = 4.0
        dim.details.append(f"含 {keyword_count} 个技术关键词")
    else:
        keyword_score = 0.0
        dim.details.append("未发现技术关键词")

    dim.score = min(length_score + keyword_score, dim.max_score)
    return dim


def score_tech_depth(data: dict) -> DimensionScore:
    """技术深度评分（满分 25 分）。基于 score 字段 1-10 映射到 0-25。"""
    dim = DimensionScore(name="技术深度", score=0.0, max_score=25.0)

    score_val = data.get("score")

    if score_val is None:
        dim.score = 12.5  # 无 score 字段给中间分
        dim.details.append("未提供 score 字段, 给予默认中间分")
        return dim

    if not isinstance(score_val, (int, float)):
        dim.details.append(f"score 类型异常: {type(score_val).__name__}")
        return dim

    # 限制到 1-10 范围
    clamped = max(1.0, min(10.0, float(score_val)))
    # 线性映射: 1 -> 2.5, 10 -> 25
    dim.score = clamped / 10.0 * 25.0
    dim.details.append(f"score={score_val}, 映射得分 {dim.score:.1f}/25")

    return dim


def score_format_compliance(data: dict) -> DimensionScore:
    """格式规范评分（满分 20 分）。五项各 4 分。"""
    dim = DimensionScore(name="格式规范", score=0.0, max_score=20.0)
    points = 0.0

    # 1. id 格式（4 分）
    entry_id = data.get("id", "")
    if isinstance(entry_id, str) and ID_PATTERN.match(entry_id):
        points += 4.0
        dim.details.append("id 格式正确 (+4)")
    else:
        dim.details.append("id 格式不正确 (+0)")

    # 2. title 非空（4 分）
    title = data.get("title", "")
    if isinstance(title, str) and len(title.strip()) > 0:
        points += 4.0
        dim.details.append("title 非空 (+4)")
    else:
        dim.details.append("title 缺失或为空 (+0)")

    # 3. source_url 合法（4 分）
    url = data.get("source_url", "")
    if isinstance(url, str) and URL_PATTERN.match(url):
        points += 4.0
        dim.details.append("source_url 格式正确 (+4)")
    else:
        dim.details.append("source_url 格式不正确 (+0)")

    # 4. status 合法（4 分）
    status = data.get("status", "")
    if isinstance(status, str) and status in VALID_STATUSES:
        points += 4.0
        dim.details.append("status 值合法 (+4)")
    else:
        dim.details.append("status 值不合法 (+0)")

    # 5. 时间戳字段存在（4 分）- 检查 created_at 或 updated_at
    has_timestamp = False
    for ts_field in ("created_at", "updated_at", "date", "timestamp"):
        if ts_field in data and data[ts_field]:
            has_timestamp = True
            break
    if has_timestamp:
        points += 4.0
        dim.details.append("时间戳字段存在 (+4)")
    else:
        dim.details.append("未找到时间戳字段 (+0)")

    dim.score = points
    return dim


def score_tag_precision(data: dict) -> DimensionScore:
    """标签精度评分（满分 15 分）。"""
    dim = DimensionScore(name="标签精度", score=0.0, max_score=15.0)

    tags = data.get("tags", [])
    if not isinstance(tags, list):
        dim.details.append("tags 字段类型异常")
        return dim

    tag_count = len(tags)

    if tag_count == 0:
        dim.details.append("无标签")
        return dim

    # 数量评分（满分 8 分）：1-3 个最佳, 4-5 个次之, >5 扣分
    if 1 <= tag_count <= 3:
        count_score = 8.0
        dim.details.append(f"{tag_count} 个标签, 数量适中 (+8)")
    elif 4 <= tag_count <= 5:
        count_score = 5.0
        dim.details.append(f"{tag_count} 个标签, 略多 (+5)")
    else:
        count_score = 3.0
        dim.details.append(f"{tag_count} 个标签, 过多 (+3)")

    # 标准标签匹配度（满分 7 分）
    standard_count = sum(
        1 for t in tags
        if isinstance(t, str) and t.lower() in STANDARD_TAGS
    )
    if tag_count > 0:
        match_ratio = standard_count / tag_count
    else:
        match_ratio = 0.0

    if match_ratio >= 0.8:
        match_score = 7.0
        dim.details.append(f"标准标签匹配率 {match_ratio:.0%} (+7)")
    elif match_ratio >= 0.5:
        match_score = 4.0
        dim.details.append(f"标准标签匹配率 {match_ratio:.0%} (+4)")
    else:
        match_score = 2.0
        dim.details.append(f"标准标签匹配率 {match_ratio:.0%} (+2)")

    dim.score = count_score + match_score
    return dim


def score_hollow_words(data: dict) -> DimensionScore:
    """空洞词检测评分（满分 15 分）。不含空洞词得满分。"""
    dim = DimensionScore(name="空洞词检测", score=15.0, max_score=15.0)

    # 拼接所有文本字段
    text_parts: list[str] = []
    for text_field in ("title", "summary", "description", "content"):
        val = data.get(text_field, "")
        if isinstance(val, str):
            text_parts.append(val)

    full_text = " ".join(text_parts).lower()

    # 检测空洞词
    found_hollow: list[str] = []
    for word in HOLLOW_WORDS_ALL:
        if word.lower() in full_text:
            found_hollow.append(word)

    if not found_hollow:
        dim.details.append("未检测到空洞词 (满分)")
    else:
        # 每发现一个扣 3 分，最多扣到 0
        penalty = min(len(found_hollow) * 3.0, 15.0)
        dim.score = 15.0 - penalty
        dim.details.append(
            f"检测到 {len(found_hollow)} 个空洞词: "
            f"{', '.join(found_hollow)} (-{penalty:.0f})"
        )

    return dim


# ============================================================
# 评估与输出
# ============================================================

def evaluate_file(file_path: Path) -> Optional[QualityReport]:
    """评估单个 JSON 文件，返回质量报告。解析失败返回 None。"""
    report = QualityReport(file_path=file_path)

    if not file_path.exists() or not file_path.is_file():
        return None

    try:
        text = file_path.read_text(encoding="utf-8")
        data = json.loads(text)
    except (json.JSONDecodeError, OSError):
        return None

    if not isinstance(data, dict):
        return None

    # 运行 5 个维度评分
    report.dimensions = [
        score_summary_quality(data),
        score_tech_depth(data),
        score_format_compliance(data),
        score_tag_precision(data),
        score_hollow_words(data),
    ]

    return report


def render_bar(score: float, max_score: float) -> str:
    """渲染可视化进度条。"""
    if max_score == 0:
        ratio = 0.0
    else:
        ratio = score / max_score

    filled = int(ratio * BAR_WIDTH)
    empty = BAR_WIDTH - filled
    bar = "█" * filled + "░" * empty
    return f"[{bar}]"


def render_grade_badge(grade: str) -> str:
    """渲染等级标志。"""
    badges = {"A": "★ A", "B": "● B", "C": "○ C"}
    return badges.get(grade, grade)


def print_report(report: QualityReport) -> None:
    """输出单个文件的质量报告。"""
    print(f"\n{'─' * 56}")
    print(f"  文件: {report.file_path}")
    print(f"{'─' * 56}")

    for dim in report.dimensions:
        bar = render_bar(dim.score, dim.max_score)
        print(
            f"  {dim.name:<8} {bar} "
            f"{dim.score:5.1f}/{dim.max_score:.0f}"
        )
        for detail in dim.details:
            print(f"             {detail}")

    print(f"{'─' * 56}")
    grade_badge = render_grade_badge(report.grade)
    print(
        f"  总分: {report.total_score:.1f}/{report.max_total:.0f}  "
        f"等级: {grade_badge}"
    )
    print(f"{'─' * 56}")


# ============================================================
# 主入口
# ============================================================

def main() -> int:
    """主入口函数，返回退出码。"""
    if len(sys.argv) < 2:
        print(f"用法: python {sys.argv[0]} <json_file> [json_file2 ...]")
        print(f"示例: python {sys.argv[0]} data/*.json")
        return 1

    files: list[Path] = [Path(arg) for arg in sys.argv[1:]]

    if not files:
        print("错误: 未找到任何文件")
        return 1

    # 评估所有文件
    reports: list[QualityReport] = []
    parse_failures: list[Path] = []

    for file_path in files:
        report = evaluate_file(file_path)
        if report is None:
            parse_failures.append(file_path)
        else:
            reports.append(report)

    # 输出解析失败的文件
    for fp in parse_failures:
        print(f"\n✗ {fp} (JSON 解析失败或文件不存在)")

    # 输出每个文件的报告
    for report in reports:
        print_report(report)

    # 汇总统计
    total = len(files)
    failed_parse = len(parse_failures)
    grades = {"A": 0, "B": 0, "C": 0}
    for r in reports:
        grades[r.grade] += 1

    print(f"\n{'═' * 56}")
    print(f"  汇总: {total} 个文件")
    if failed_parse > 0:
        print(f"  解析失败: {failed_parse} 个")
    print(
        f"  等级分布: "
        f"A={grades['A']}  B={grades['B']}  C={grades['C']}"
    )
    if reports:
        avg_score = sum(r.total_score for r in reports) / len(reports)
        print(f"  平均分: {avg_score:.1f}/100")
    print(f"{'═' * 56}")

    # 退出码: 存在 C 级返回 1
    has_c_grade = grades["C"] > 0 or failed_parse > 0
    return 1 if has_c_grade else 0


if __name__ == "__main__":
    sys.exit(main())
