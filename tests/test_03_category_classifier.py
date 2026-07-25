"""
分类与策略测试 (test_03_category_classifier.py)

验证 category-classifier 子 Skill 的全部脚本。
双输出模式适配：
    - ABC 分类使用 extracted_summary.parquet（汇总数据）
    - XYZ 分类使用 extracted_weekly.parquet（周度数据）
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


# ============================================================================
# 测试 3.1：ABC 分类（使用汇总数据）
# ============================================================================

def test_abc_classifier(
    scripts_base_dir: Path,
    output_dir: Path,
) -> None:
    """测试 abc_classifier.py 的 ABC 分类功能。"""
    script_path: Path = scripts_base_dir / "category-classifier" / "scripts" / "abc_classifier.py"
    summary_path: Path = output_dir / "extracted_summary.parquet"

    assert summary_path.exists(), f"前置条件不满足: {summary_path} 不存在。"

    result: subprocess.CompletedProcess = subprocess.run(
        [
            sys.executable, str(script_path),
            "--input", str(summary_path),
            "--output", str(output_dir / "abc_xyz_result.json"),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"abc_classifier.py 执行失败。\nstderr: {result.stderr}"
    )

    output_path: Path = output_dir / "abc_xyz_result.json"
    assert output_path.exists(), f"产出文件不存在: {output_path}"

    with open(output_path, "r", encoding="utf-8") as fp:
        data: dict[str, Any] = json.load(fp)

    assert "abc_classification" in data, "缺少 abc_classification"
    abc: dict[str, Any] = data["abc_classification"]
    assert abc["total_items"] > 0, "物料数为 0"
    assert abc["a_count"] + abc["b_count"] + abc["c_count"] == abc["total_items"], (
        "A+B+C 分类数量总和不等于总物料数"
    )


# ============================================================================
# 测试 3.2：XYZ 分类 + 组合矩阵（使用周度数据）
# ============================================================================

def test_xyz_classifier(
    scripts_base_dir: Path,
    output_dir: Path,
) -> None:
    """测试 xyz_classifier.py 的 XYZ 分类和组合矩阵。"""
    script_path: Path = scripts_base_dir / "category-classifier" / "scripts" / "xyz_classifier.py"
    weekly_path: Path = output_dir / "extracted_weekly.parquet"

    assert weekly_path.exists(), f"前置条件不满足: {weekly_path} 不存在。"

    result: subprocess.CompletedProcess = subprocess.run(
        [
            sys.executable, str(script_path),
            "--input", str(weekly_path),
            "--output", str(output_dir / "abc_xyz_result.json"),
            "--append",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"xyz_classifier.py 执行失败。\nstderr: {result.stderr}"
    )

    output_path: Path = output_dir / "abc_xyz_result.json"
    with open(output_path, "r", encoding="utf-8") as fp:
        data: dict[str, Any] = json.load(fp)

    assert "xyz_classification" in data, "缺少 xyz_classification"
    assert "abc_xyz_matrix" in data, "缺少 abc_xyz_matrix"

    xyz: dict[str, Any] = data["xyz_classification"]
    assert xyz["total_items"] > 0, "物料数为 0"
    assert xyz["x_count"] + xyz["y_count"] + xyz["z_count"] + xyz["insufficient_data_count"] == xyz["total_items"], (
        "X+Y+Z+数据不足 分类数量总和不等于总物料数"
    )

    matrix: dict[str, Any] = data["abc_xyz_matrix"]
    if matrix.get("status") == "completed":
        assert len(matrix.get("strategy_items", [])) > 0, "组合矩阵策略项为空"
        for item in matrix["strategy_items"]:
            assert "管控策略" in item or "补货机制" in item or "服务水平" in item, (
                f"缺少策略信息: {item.get('物料编码')}"
            )