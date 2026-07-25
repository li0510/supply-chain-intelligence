"""
采购决策建议测试 (test_07_purchase_advisor.py)

验证 purchase-advisor 子 Skill。
双输出模式适配：使用 alert_list.json、supply_demand_gap.json 等中间文件。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


# ============================================================================
# 测试 7.1：采购计划生成
# ============================================================================

def test_purchase_planner(
    scripts_base_dir: Path,
    output_dir: Path,
) -> None:
    """测试 purchase_planner.py 的采购优先级和供应商分配。"""
    script_path: Path = scripts_base_dir / "purchase-advisor" / "scripts" / "purchase_planner.py"
    alert_path: Path = output_dir / "alert_list.json"
    sd_path: Path = output_dir / "supply_demand_gap.json"
    plan_path: Path = output_dir / "inventory_plan.json"

    assert alert_path.exists(), f"前置条件不满足: alert_list.json 不存在。"

    cmd: list[str] = [
        sys.executable, str(script_path),
        "--alerts", str(alert_path),
        "--output", str(output_dir / "purchase_plan.json"),
    ]

    if sd_path.exists():
        cmd.extend(["--supply-demand", str(sd_path)])
    if plan_path.exists():
        cmd.extend(["--inventory-plan", str(plan_path)])

    result: subprocess.CompletedProcess = subprocess.run(
        cmd, capture_output=True, text=True
    )

    assert result.returncode == 0, (
        f"purchase_planner.py 执行失败。\nstderr: {result.stderr}"
    )

    output_path: Path = output_dir / "purchase_plan.json"
    assert output_path.exists(), f"产出文件不存在: {output_path}"

    with open(output_path, "r", encoding="utf-8") as fp:
        data: dict[str, Any] = json.load(fp)

    assert "purchase_plan" in data, "缺少 purchase_plan"
    pp: dict[str, Any] = data["purchase_plan"]
    assert pp["total_items"] >= 0, "采购项数量异常"
    assert "budget_estimation" in pp, "缺少 budget_estimation"
    assert "eoq_moq_warnings" in pp, "缺少 eoq_moq_warnings"

    if pp.get("purchase_items"):
        scores: list[int] = [item["优先级分数"] for item in pp["purchase_items"]]
        assert scores == sorted(scores, reverse=True), "采购项未按优先级排序"


# ============================================================================
# 测试 7.2：综合报告生成
# ============================================================================

def test_report_generator(
    scripts_base_dir: Path,
    output_dir: Path,
) -> None:
    """测试 report_generator.py 的综合报告和行动闭环。"""
    script_path: Path = scripts_base_dir / "purchase-advisor" / "scripts" / "report_generator.py"

    result: subprocess.CompletedProcess = subprocess.run(
        [
            sys.executable, str(script_path),
            "--project-dir", str(output_dir),
            "--output", str(output_dir / "final_report.json"),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"report_generator.py 执行失败。\nstderr: {result.stderr}"
    )

    output_path: Path = output_dir / "final_report.json"
    assert output_path.exists(), f"综合报告不存在: {output_path}"

    with open(output_path, "r", encoding="utf-8") as fp:
        data: dict[str, Any] = json.load(fp)

    assert "executive_summary" in data, "缺少 executive_summary"
    assert "key_metrics" in data, "缺少 key_metrics"
    assert "incomplete_items" in data, "缺少 incomplete_items"
    assert "next_actions" in data, "缺少 next_actions"

    # 验证行动记录
    history_path: Path = output_dir / "action_history.json"
    if history_path.exists():
        with open(history_path, "r", encoding="utf-8") as fp:
            history: dict[str, Any] = json.load(fp)
        assert "history" in history, "缺少 history 字段"
        assert len(history["history"]) >= 1, "行动记录为空"