"""
供需匹配测试 (test_05_supply_demand.py)

验证 supply-demand-matcher 子 Skill。
双输出模式适配：供给端使用 extracted_summary.parquet。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


# ============================================================================
# 测试 5.1：供需匹配分析
# ============================================================================

def test_supply_demand_matcher(
    scripts_base_dir: Path,
    output_dir: Path,
    demand_data: Path,
) -> None:
    """测试 supply_demand_matcher.py 的供需匹配和缺口计算。"""
    script_path: Path = scripts_base_dir / "supply-demand-matcher" / "scripts" / "supply_demand_matcher.py"
    summary_path: Path = output_dir / "extracted_summary.parquet"

    assert summary_path.exists(), f"前置条件不满足: {summary_path} 不存在。"
    assert demand_data.exists(), f"前置条件不满足: {demand_data} 不存在。"

    result: subprocess.CompletedProcess = subprocess.run(
        [
            sys.executable, str(script_path),
            "--supply", str(summary_path),
            "--demand", str(demand_data),
            "--output", str(output_dir / "supply_demand_gap.json"),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"supply_demand_matcher.py 执行失败。\nstderr: {result.stderr}"
    )

    output_path: Path = output_dir / "supply_demand_gap.json"
    assert output_path.exists(), f"产出文件不存在: {output_path}"

    with open(output_path, "r", encoding="utf-8") as fp:
        data: dict[str, Any] = json.load(fp)

    assert "supply_demand_matching" in data, "缺少 supply_demand_matching"
    sdm: dict[str, Any] = data["supply_demand_matching"]
    assert sdm["total_demand"] > 0, "总需求量为 0"
    assert sdm["total_supply"] > 0, "总供给量为 0"
    assert "shortage_items" in sdm, "缺少 shortage_items"
    assert "surplus_items" in sdm, "缺少 surplus_items"

    for item in sdm["all_items"][:5]:
        assert "供需状态" in item, f"缺少供需状态: {item.get('物料编码')}"
        assert item["供需状态"] in ("充足", "偏紧", "短缺"), (
            f"无效的供需状态: {item['供需状态']}"
        )