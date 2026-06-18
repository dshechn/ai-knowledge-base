#!/usr/bin/env python3
"""校验知识条目 JSON 文件的格式与内容。

用法:
    python hooks/validate_json.py <json_file> [json_file2 ...]
    python hooks/validate_json.py data/*.json

校验通过 exit 0，失败 exit 1 并输出错误列表及汇总统计。
"""

import json
import re
import sys
from pathlib import Path

# 必填字段: 字段名 -> 期望类型
REQUIRED_FIELDS: dict[str, type] = {
    "id": str,
    "title": str,
    "source_url": str,
    "summary": str,
    "tags": list,
    "status": str,
}

VALID_STATUSES = {"draft", "review", "published", "archived"}
VALID_AUDIENCES = {"beginner", "intermediate", "advanced"}

ID_PATTERN = re.compile(r"^[a-z]+-\d{8}-\d{3}$")
URL_PATTERN = re.compile(r"^https?://.+")

MIN_SUMMARY_LENGTH = 20
MIN_TAGS_COUNT = 1
SCORE_RANGE = (1, 10)


def validate_entry(data: dict, file_path: Path) -> list[str]:
    """校验单个知识条目，返回错误消息列表。"""
    errors: list[str] = []

    # 必填字段存在性与类型检查
    for field, expected_type in REQUIRED_FIELDS.items():
        if field not in data:
            errors.append(f"缺少必填字段: {field}")
        elif not isinstance(data[field], expected_type):
            errors.append(
                f"字段 '{field}' 类型错误: "
                f"期望 {expected_type.__name__}, "
                f"实际 {type(data[field]).__name__}"
            )

    # 以下校验仅在字段存在且类型正确时执行
    # ID 格式检查
    if "id" in data and isinstance(data["id"], str):
        if not ID_PATTERN.match(data["id"]):
            errors.append(
                f"ID 格式错误: '{data['id']}', "
                f"期望格式 {{source}}-{{YYYYMMDD}}-{{NNN}} "
                f"(如 github-20260317-001)"
            )

    # status 值检查
    if "status" in data and isinstance(data["status"], str):
        if data["status"] not in VALID_STATUSES:
            errors.append(
                f"status 值无效: '{data['status']}', "
                f"允许值: {', '.join(sorted(VALID_STATUSES))}"
            )

    # URL 格式检查
    if "source_url" in data and isinstance(data["source_url"], str):
        if not URL_PATTERN.match(data["source_url"]):
            errors.append(
                f"source_url 格式错误: '{data['source_url']}', "
                f"期望 https?://... 格式"
            )

    # 摘要长度检查
    if "summary" in data and isinstance(data["summary"], str):
        if len(data["summary"]) < MIN_SUMMARY_LENGTH:
            errors.append(
                f"摘要过短: {len(data['summary'])} 字, "
                f"最少需要 {MIN_SUMMARY_LENGTH} 字"
            )

    # 标签数量检查
    if "tags" in data and isinstance(data["tags"], list):
        if len(data["tags"]) < MIN_TAGS_COUNT:
            errors.append(
                f"标签不足: {len(data['tags'])} 个, "
                f"至少需要 {MIN_TAGS_COUNT} 个"
            )

    # 可选字段: score
    if "score" in data:
        score = data["score"]
        if not isinstance(score, (int, float)):
            errors.append(
                f"score 类型错误: 期望数值, 实际 {type(score).__name__}"
            )
        elif not (SCORE_RANGE[0] <= score <= SCORE_RANGE[1]):
            errors.append(
                f"score 超出范围: {score}, "
                f"允许范围 {SCORE_RANGE[0]}-{SCORE_RANGE[1]}"
            )

    # 可选字段: audience
    if "audience" in data:
        audience = data["audience"]
        if not isinstance(audience, str):
            errors.append(
                f"audience 类型错误: 期望 str, 实际 {type(audience).__name__}"
            )
        elif audience not in VALID_AUDIENCES:
            errors.append(
                f"audience 值无效: '{audience}', "
                f"允许值: {', '.join(sorted(VALID_AUDIENCES))}"
            )

    return errors


def validate_file(file_path: Path) -> list[str]:
    """校验单个 JSON 文件，返回错误消息列表。"""
    errors: list[str] = []

    if not file_path.exists():
        errors.append(f"文件不存在: {file_path}")
        return errors

    if not file_path.is_file():
        errors.append(f"不是文件: {file_path}")
        return errors

    # JSON 解析检查
    try:
        text = file_path.read_text(encoding="utf-8")
        data = json.loads(text)
    except json.JSONDecodeError as e:
        errors.append(f"JSON 解析失败: {e}")
        return errors

    if not isinstance(data, dict):
        errors.append(
            f"JSON 顶层结构应为对象(dict), 实际为 {type(data).__name__}"
        )
        return errors

    errors.extend(validate_entry(data, file_path))
    return errors


def main() -> int:
    """主入口函数，返回退出码。"""
    if len(sys.argv) < 2:
        print(f"用法: python {sys.argv[0]} <json_file> [json_file2 ...]")
        print(f"示例: python {sys.argv[0]} data/*.json")
        return 1

    # 收集所有文件路径（支持通配符由 shell 展开）
    files: list[Path] = []
    for arg in sys.argv[1:]:
        path = Path(arg)
        files.append(path)

    if not files:
        print("错误: 未找到任何文件")
        return 1

    # 统计
    total_files = len(files)
    passed_files = 0
    failed_files = 0
    total_errors = 0
    all_results: list[tuple[Path, list[str]]] = []

    for file_path in files:
        errors = validate_file(file_path)
        all_results.append((file_path, errors))
        if errors:
            failed_files += 1
            total_errors += len(errors)
        else:
            passed_files += 1

    # 输出结果
    for file_path, errors in all_results:
        if errors:
            print(f"\n✗ {file_path}")
            for error in errors:
                print(f"  - {error}")
        else:
            print(f"✓ {file_path}")

    # 汇总统计
    print("\n" + "=" * 50)
    print(f"汇总: {total_files} 个文件, "
          f"{passed_files} 通过, {failed_files} 失败, "
          f"{total_errors} 个错误")
    print("=" * 50)

    return 0 if failed_files == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
