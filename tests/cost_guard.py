"""多 Agent LLM 调用成本预算守卫。"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


logger = logging.getLogger(__name__)


class BudgetExceededError(Exception):
    """预算超限时抛出的异常。"""


@dataclass(frozen=True)
class CostRecord:
    """记录单次 LLM 调用成本。"""

    timestamp: str
    node_name: str
    prompt_tokens: int
    completion_tokens: int
    cost_yuan: float
    model: str


class CostGuard:
    """多 Agent LLM 调用预算守卫。"""

    def __init__(
        self,
        budget_yuan: float = 1.0,
        alert_threshold: float = 0.8,
        input_price_per_million: float = 1.0,
        output_price_per_million: float = 2.0,
    ) -> None:
        """初始化预算守卫。

        Args:
            budget_yuan: 总预算，单位为人民币元。
            alert_threshold: 预警阈值，按预算使用比例计算。
            input_price_per_million: 每百万输入 token 价格。
            output_price_per_million: 每百万输出 token 价格。
        """
        self.budget_yuan = budget_yuan
        self.alert_threshold = alert_threshold
        self.input_price_per_million = input_price_per_million
        self.output_price_per_million = output_price_per_million
        self.records: list[CostRecord] = []
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_cost_yuan = 0.0

    def record(
        self,
        node_name: str,
        usage: dict[str, int],
        model: str = "",
    ) -> CostRecord:
        """记录一次 LLM 调用的 token 用量与成本。

        Args:
            node_name: 触发调用的节点名称。
            usage: token 用量，格式为 ``{"prompt_tokens": int,
                "completion_tokens": int}``。
            model: 模型名称。

        Returns:
            本次调用的成本记录。
        """
        prompt_tokens = int(usage.get("prompt_tokens", 0))
        completion_tokens = int(usage.get("completion_tokens", 0))
        cost_yuan = self._calculate_cost(prompt_tokens, completion_tokens)
        record = CostRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            node_name=node_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_yuan=cost_yuan,
            model=model,
        )

        self.records.append(record)
        self.total_prompt_tokens += prompt_tokens
        self.total_completion_tokens += completion_tokens
        self.total_cost_yuan += cost_yuan
        return record

    def check(self) -> dict[str, Any]:
        """检查当前预算状态。

        Returns:
            包含状态、总成本、预算、使用比例和消息的字典。

        Raises:
            BudgetExceededError: 当总成本超过预算时抛出。
        """
        usage_ratio = self._usage_ratio()
        if self.total_cost_yuan > self.budget_yuan:
            message = (
                f"预算已超限：当前成本 {self.total_cost_yuan:.6f} 元，"
                f"预算 {self.budget_yuan:.6f} 元。"
            )
            raise BudgetExceededError(message)

        if usage_ratio >= self.alert_threshold:
            status = "warning"
            message = (
                f"成本接近预算：当前使用率 {usage_ratio:.2%}，"
                f"预警阈值 {self.alert_threshold:.2%}。"
            )
        else:
            status = "ok"
            message = "成本在预算范围内。"

        return {
            "status": status,
            "total_cost": self.total_cost_yuan,
            "budget": self.budget_yuan,
            "usage_ratio": usage_ratio,
            "message": message,
        }

    def get_report(self) -> dict[str, Any]:
        """生成按节点分组的成本报告。

        Returns:
            成本报告字典。
        """
        nodes: dict[str, dict[str, Any]] = {}
        for record in self.records:
            node_report = nodes.setdefault(
                record.node_name,
                {
                    "call_count": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "cost_yuan": 0.0,
                    "models": [],
                },
            )
            node_report["call_count"] += 1
            node_report["prompt_tokens"] += record.prompt_tokens
            node_report["completion_tokens"] += record.completion_tokens
            node_report["total_tokens"] += (
                record.prompt_tokens + record.completion_tokens
            )
            node_report["cost_yuan"] += record.cost_yuan
            if record.model and record.model not in node_report["models"]:
                node_report["models"].append(record.model)

        return {
            "budget_yuan": self.budget_yuan,
            "alert_threshold": self.alert_threshold,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_prompt_tokens + self.total_completion_tokens,
            "total_cost_yuan": self.total_cost_yuan,
            "usage_ratio": self._usage_ratio(),
            "record_count": len(self.records),
            "nodes": nodes,
            "records": [asdict(record) for record in self.records],
        }

    def save_report(self, path: str | Path | None = None) -> Path:
        """保存成本报告到 JSON 文件。

        Args:
            path: 目标文件路径；为空时保存到当前目录的 ``cost_report.json``。

        Returns:
            保存后的文件路径。
        """
        report_path = Path(path) if path is not None else Path("cost_report.json")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(self.get_report(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return report_path

    def _calculate_cost(
        self,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> float:
        """根据 token 用量计算人民币成本。"""
        input_cost = prompt_tokens / 1_000_000 * self.input_price_per_million
        output_cost = completion_tokens / 1_000_000 * self.output_price_per_million
        return input_cost + output_cost

    def _usage_ratio(self) -> float:
        """计算预算使用比例。"""
        if self.budget_yuan <= 0:
            return float("inf")
        return self.total_cost_yuan / self.budget_yuan


def test_cost_tracking() -> None:
    """验证成本追踪正确。"""
    guard = CostGuard()
    guard.record(
        "analyzer",
        {"prompt_tokens": 1_000_000, "completion_tokens": 500_000},
        model="test-model",
    )

    assert guard.total_prompt_tokens == 1_000_000
    assert guard.total_completion_tokens == 500_000
    assert guard.total_cost_yuan == 2.0


def test_budget_exceeded() -> None:
    """验证预算超限检测。"""
    guard = CostGuard(budget_yuan=0.1)
    guard.record("reviewer", {"prompt_tokens": 100_000, "completion_tokens": 1})

    try:
        guard.check()
    except BudgetExceededError:
        return

    raise AssertionError("预算超限时应抛出 BudgetExceededError")


def test_warning_threshold() -> None:
    """验证预警阈值触发。"""
    guard = CostGuard(budget_yuan=1.0, alert_threshold=0.8)
    guard.record("planner", {"prompt_tokens": 800_000, "completion_tokens": 0})

    result = guard.check()
    assert result["status"] == "warning"


def test_report_save() -> None:
    """验证报告保存。"""
    guard = CostGuard()
    guard.record("planner", {"prompt_tokens": 1, "completion_tokens": 1})


    with TemporaryDirectory() as temp_dir:
        report_path = guard.save_report(Path(temp_dir) / "cost_report.json")
        saved_report = json.loads(report_path.read_text(encoding="utf-8"))

    assert saved_report["record_count"] == 1
    assert saved_report["nodes"]["planner"]["call_count"] == 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    test_cost_tracking()
    test_budget_exceeded()
    test_warning_threshold()
    test_report_save()

    logger.info("CostGuard 所有检查通过")
