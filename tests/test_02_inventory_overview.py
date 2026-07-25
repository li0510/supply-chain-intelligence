"""
库存全景分析测试 (test_02_inventory_overview.py)

验证 inventory-overview 子 Skill 的全部脚本。
双输出模式适配：使用 extracted_summary.parquet（汇总数据）
和 extracted_weekly.parquet（周度数据，用于周转率计算）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


# ============================================================================
# 测试 2.1：库存全景分析（使用汇总数据）
# ============================================================================

def test_data_aggregator(
    scripts_base_dir: Path,
    output_dir: Path,
) -> None:
    """测试 data_aggregator.py 的存量和流量总览。"""
    script_path: Path = scripts_base_dir / "inventory-overview" / "scripts" / "data_aggregator.py"
    summary_path: Path = output_dir / "extracted_summary.parquet"

    assert summary_path.exists(), (
        f"前置条件不满足: {summary_path} 不存在。"
    )

    result: subprocess.CompletedProcess = subprocess.run(
        [
            sys.executable, str(script_path),
            "--input", str(summary_path),
            "--output", str(output_dir / "inventory_overview.json"),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"data_aggregator.py 执行失败。\nstderr: {result.stderr}"
    )

    output_path: Path = output_dir / "inventory_overview.json"
    assert output_path.exists(), f"产出文件不存在: {output_path}"

    with open(output_path, "r", encoding="utf-8") as fp:
        data: dict[str, Any] = json.load(fp)

    assert "inventory_summary" in data, "缺少 inventory_summary"
    assert "flow_summary" in data, "缺少 flow_summary"
    assert data["inventory_summary"]["total_inventory"] > 0, "总库存量为 0"


# ============================================================================
# 测试 2.2：周转效率分析（使用汇总数据 + 周度数据）
# ============================================================================

def test_inventory_turnover(
    scripts_base_dir: Path,
    output_dir: Path,
) -> None:
    """测试 inventory_turnover.py 的周转率和呆滞识别。"""
    script_path: Path = scripts_base_dir / "inventory-overview" / "scripts" / "inventory_turnover.py"
    summary_path: Path = output_dir / "extracted_summary.parquet"
    weekly_path: Path = output_dir / "extracted_weekly.parquet"

    assert summary_path.exists(), f"前置条件不满足: {summary_path} 不存在。"
    assert weekly_path.exists(), f"前置条件不满足: {weekly_path} 不存在。"

    result: subprocess.CompletedProcess = subprocess.run(
        [
            sys.executable, str(script_path),
            "--input", str(summary_path),
            "--weekly", str(weekly_path),
            "--output", str(output_dir / "efficiency_cost_report.json"),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"inventory_turnover.py 执行失败。\nstderr: {result.stderr}"
    )

    output_path: Path = output_dir / "efficiency_cost_report.json"
    assert output_path.exists(), f"产出文件不存在: {output_path}"

    with open(output_path, "r", encoding="utf-8") as fp:
        data: dict[str, Any] = json.load(fp)

    assert "turnover_analysis" in data, "缺少 turnover_analysis"
    ta: dict[str, Any] = data["turnover_analysis"]
    assert ta["total_items"] > 0, "物料数为 0"
    assert "average_turnover_rate" in ta, "缺少 average_turnover_rate"
    assert "slow_moving_count" in ta, "缺少 slow_moving_count"
    assert "slow_moving_ratio_pct" in ta, "缺少 slow_moving_ratio_pct"
    assert "库存持有天数(DOH)" in str(ta["item_details"][0]), "缺少 DOH 字段"


# ============================================================================
# 测试 2.3：成本与资金分析（使用汇总数据）
# ============================================================================

def test_cost_analyzer(
    scripts_base_dir: Path,
    output_dir: Path,
) -> None:
    """测试 cost_analyzer.py 的资金分析和产品流。"""
    script_path: Path = scripts_base_dir / "inventory-overview" / "scripts" / "cost_analyzer.py"
    summary_path: Path = output_dir / "extracted_summary.parquet"

    assert summary_path.exists(), f"前置条件不满足: {summary_path} 不存在。"

    result: subprocess.CompletedProcess = subprocess.run(
        [
            sys.executable, str(script_path),
            "--input", str(summary_path),
            "--output", str(output_dir / "efficiency_cost_report.json"),
            "--append",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"cost_analyzer.py 执行失败。\nstderr: {result.stderr}"
    )

    output_path: Path = output_dir / "efficiency_cost_report.json"
    with open(output_path, "r", encoding="utf-8") as fp:
        data: dict[str, Any] = json.load(fp)

    assert "capital_analysis" in data, "缺少 capital_analysis"
    assert "product_flow_analysis" in data, "缺少 product_flow_analysis"