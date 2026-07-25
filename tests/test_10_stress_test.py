"""
企业级数据量压力测试 (test_10_stress_test.py)

供应链智能分析平台 — 性能与稳定性验证

功能：使用生成器生成的模拟企业级数据（默认 10,000 SKU × 260 周），
     对核心分析脚本进行性能和稳定性测试。
     验证单脚本执行时间在可接受阈值内，产出文件行数正确。

更新内容：
    - test_inventory_planning_stress 新增 --summary 必需参数
    - test_inventory_alert_stress 补充 --summary 参数
    - 新增 test_hyperparameter_tuner_stress：10,000 SKU 调参性能测试

用法（需要先生成测试数据）:
    uv run python tests/test_data/generate_stress_data.py \
      --skus 10000 --weeks 260 --seed 42 \
      --intermittent-ratio 0.1 \
      --output-dir tests/test_data/stress_data/

    uv run pytest tests/test_10_stress_test.py -v -s

作者: Supply Chain Intelligence Team
版本: 0.2.5
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import polars as pl
import pytest


# ============================================================================
# 配置
# ============================================================================

STRESS_DATA_DIR: str = "tests/test_data/stress_data"
TIME_THRESHOLD_SECONDS: float = 30.0
TUNER_TIME_THRESHOLD_SECONDS: float = 1800
EXPECTED_SKUS: int = 10000


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(scope="module")
def stress_dir(project_root: Path) -> Path:
    """压力测试数据目录。"""
    return project_root / STRESS_DATA_DIR


@pytest.fixture(scope="module")
def output_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """压力测试输出目录。"""
    return tmp_path_factory.mktemp("stress_output")


@pytest.fixture(scope="module")
def scripts_base_dir(project_root: Path) -> Path:
    """脚本基础目录。"""
    return project_root / "skills"


# ============================================================================
# 辅助函数
# ============================================================================

def _run_and_time(script_path: Path, args: list[str]) -> tuple[int, float, str, str]:
    """运行脚本并记录执行时间。"""
    start: float = time.perf_counter()
    result: subprocess.CompletedProcess = subprocess.run(
        [sys.executable, str(script_path)] + args,
        capture_output=True,
        text=True,
    )
    elapsed: float = time.perf_counter() - start
    return result.returncode, elapsed, result.stdout, result.stderr


# ============================================================================
# 测试
# ============================================================================

def test_stress_data_exists(stress_dir: Path) -> None:
    """验证压力测试数据已生成。"""
    required: list[str] = [
        "extracted_weekly.parquet",
        "extracted_summary.parquet",
        "abc_xyz_result.json",
    ]
    for filename in required:
        file_path: Path = stress_dir / filename
        assert file_path.exists(), (
            f"压力测试数据不存在: {file_path}\n"
            f"请先运行: uv run python tests/test_data/generate_stress_data.py "
            f"--skus {EXPECTED_SKUS} --weeks 260 --seed 42 "
            f"--intermittent-ratio 0.1 "
            f"--output-dir {stress_dir}"
        )

    weekly_df: pl.DataFrame = pl.read_parquet(stress_dir / "extracted_weekly.parquet")
    assert weekly_df["物料编码"].n_unique() == EXPECTED_SKUS, (
        f"周度数据 SKU 数量不匹配: 期望 {EXPECTED_SKUS}, "
        f"实际 {weekly_df['物料编码'].n_unique()}"
    )


def test_demand_forecast_stress(
    scripts_base_dir: Path,
    stress_dir: Path,
    output_dir: Path,
) -> None:
    """压力测试：demand_forecast.py — 10,000 SKU × 260 周。"""
    script_path: Path = scripts_base_dir / "inventory-planner" / "scripts" / "demand_forecast.py"

    returncode, elapsed, stdout, stderr = _run_and_time(
        script_path,
        ["--input", str(stress_dir / "extracted_weekly.parquet"),
         "--output", str(output_dir / "forecast_result.json")],
    )

    assert returncode == 0, f"demand_forecast 失败: {stderr}"
    assert elapsed < TIME_THRESHOLD_SECONDS, (
        f"demand_forecast 超时: {elapsed:.1f}s > {TIME_THRESHOLD_SECONDS}s"
    )

    with open(output_dir / "forecast_result.json", "r", encoding="utf-8") as fp:
        data: dict[str, Any] = json.load(fp)
    assert data["demand_forecast"]["total_items"] == EXPECTED_SKUS, (
        f"预测物料数不匹配: 期望 {EXPECTED_SKUS}, "
        f"实际 {data['demand_forecast']['total_items']}"
    )

    print(f"  demand_forecast: {elapsed:.2f}s ✅")


def test_hyperparameter_tuner_stress(
    scripts_base_dir: Path,
    stress_dir: Path,
    output_dir: Path,
) -> None:
    """压力测试：hyperparameter_tuner.py — 10,000 SKU 调参性能测试。"""
    script_path: Path = scripts_base_dir / "inventory-planner" / "scripts" / "hyperparameter_tuner.py"

    returncode, elapsed, stdout, stderr = _run_and_time(
        script_path,
        ["--input", str(stress_dir / "extracted_weekly.parquet"),
         "--output", str(output_dir / "optimal_params.json"),
         "--method", "auto",
         "--workers", "4"],
    )

    assert returncode == 0, f"hyperparameter_tuner 失败: {stderr}"
    assert elapsed < TUNER_TIME_THRESHOLD_SECONDS, (
        f"hyperparameter_tuner 超时: {elapsed:.1f}s > {TUNER_TIME_THRESHOLD_SECONDS}s"
    )

    with open(output_dir / "optimal_params.json", "r", encoding="utf-8") as fp:
        data: dict[str, Any] = json.load(fp)
    assert data["total_items"] == EXPECTED_SKUS, (
        f"调参物料数不匹配: 期望 {EXPECTED_SKUS}, "
        f"实际 {data['total_items']}"
    )

    print(f"  hyperparameter_tuner: {elapsed:.2f}s ✅")


def test_inventory_planning_stress(
    scripts_base_dir: Path,
    stress_dir: Path,
    output_dir: Path,
) -> None:
    """压力测试：inventory_planning.py — 10,000 SKU × 260 周。"""
    script_path: Path = scripts_base_dir / "inventory-planner" / "scripts" / "inventory_planning.py"

    returncode, elapsed, stdout, stderr = _run_and_time(
        script_path,
        ["--data", str(stress_dir / "extracted_weekly.parquet"),
         "--summary", str(stress_dir / "extracted_summary.parquet"),
         "--classification", str(stress_dir / "abc_xyz_result.json"),
         "--forecast", str(output_dir / "forecast_result.json"),
         "--output", str(output_dir / "inventory_plan.json")],
    )

    assert returncode == 0, f"inventory_planning 失败: {stderr}"
    assert elapsed < TIME_THRESHOLD_SECONDS, (
        f"inventory_planning 超时: {elapsed:.1f}s > {TIME_THRESHOLD_SECONDS}s"
    )

    with open(output_dir / "inventory_plan.json", "r", encoding="utf-8") as fp:
        data: dict[str, Any] = json.load(fp)
    assert data["inventory_plan"]["total_items"] == EXPECTED_SKUS, (
        f"库存计划物料数不匹配: 期望 {EXPECTED_SKUS}, "
        f"实际 {data['inventory_plan']['total_items']}"
    )

    print(f"  inventory_planning: {elapsed:.2f}s ✅")


def test_inventory_alert_stress(
    scripts_base_dir: Path,
    stress_dir: Path,
    output_dir: Path,
) -> None:
    """压力测试：inventory_alert.py — 10,000 SKU。"""
    script_path: Path = scripts_base_dir / "inventory-planner" / "scripts" / "inventory_alert.py"

    returncode, elapsed, stdout, stderr = _run_and_time(
        script_path,
        ["--data", str(stress_dir / "extracted_weekly.parquet"),
         "--plan", str(output_dir / "inventory_plan.json"),
         "--summary", str(stress_dir / "extracted_summary.parquet"),
         "--output", str(output_dir / "alert_list.json")],
    )

    assert returncode == 0, f"inventory_alert 失败: {stderr}"
    assert elapsed < TIME_THRESHOLD_SECONDS, (
        f"inventory_alert 超时: {elapsed:.1f}s > {TIME_THRESHOLD_SECONDS}s"
    )

    print(f"  inventory_alert: {elapsed:.2f}s ✅")


def test_xyz_classifier_stress(
    scripts_base_dir: Path,
    stress_dir: Path,
    output_dir: Path,
) -> None:
    """压力测试：xyz_classifier.py — 10,000 SKU × 260 周。"""
    script_path: Path = scripts_base_dir / "category-classifier" / "scripts" / "xyz_classifier.py"

    returncode, elapsed, stdout, stderr = _run_and_time(
        script_path,
        ["--input", str(stress_dir / "extracted_weekly.parquet"),
         "--output", str(output_dir / "xyz_stress_result.json")],
    )

    assert returncode == 0, f"xyz_classifier 失败: {stderr}"
    assert elapsed < TIME_THRESHOLD_SECONDS, (
        f"xyz_classifier 超时: {elapsed:.1f}s > {TIME_THRESHOLD_SECONDS}s"
    )

    with open(output_dir / "xyz_stress_result.json", "r", encoding="utf-8") as fp:
        data: dict[str, Any] = json.load(fp)
    assert data["xyz_classification"]["total_items"] == EXPECTED_SKUS, (
        f"XYZ 分类物料数不匹配: 期望 {EXPECTED_SKUS}, "
        f"实际 {data['xyz_classification']['total_items']}"
    )

    print(f"  xyz_classifier: {elapsed:.2f}s ✅")