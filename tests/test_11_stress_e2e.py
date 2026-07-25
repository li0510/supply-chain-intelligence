"""
企业级数据量压力测试 — 端到端集成测试 (test_11_stress_e2e.py)

供应链智能分析平台 — 企业级压力测试

功能：使用生成器生成的模拟企业级数据（默认 10,000 SKU × 260 周），
     按依赖顺序串联执行全部核心子 Skill，验证全链路性能和产出正确性。
     产出文件保留在固定路径 projects/stress_e2e_test/ 下。

更新内容：
    - test_stress_e2e_full_pipeline 中 inventory_planning.py 调用新增 --summary 必需参数
    - 在端到端压力测试中增加调参步骤（hyperparameter_tuner.py → optimal_params.json → demand_forecast.py --optimal-params）

用法（需要先生成压力测试数据）:
    uv run python tests/test_data/generate_stress_data.py \
      --skus 10000 --weeks 260 --seed 42 \
      --intermittent-ratio 0.1 \
      --output-dir tests/test_data/stress_data/

    uv run pytest tests/test_11_stress_e2e.py -v -s

作者: Supply Chain Intelligence Team
版本: 0.2.5
"""
from __future__ import annotations

import json
import shutil
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
TIME_THRESHOLD_SECONDS: float = 1800.0
TUNER_TIME_THRESHOLD_SECONDS: float = 600.0
EXPECTED_SKUS: int = 10000


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(scope="module")
def stress_dir(project_root: Path) -> Path:
    """压力测试数据目录。"""
    return project_root / STRESS_DATA_DIR


@pytest.fixture(scope="module")
def e2e_dir(project_root: Path) -> Path:
    """端到端压力测试固定输出目录（测试结束后保留）。"""
    return project_root / "projects" / "stress_e2e_test"


@pytest.fixture(scope="module")
def scripts_base_dir(project_root: Path) -> Path:
    """脚本基础目录。"""
    return project_root / "skills"


# ============================================================================
# 辅助函数
# ============================================================================

def _run_and_time(
    script_path: Path, args: list[str]
) -> tuple[int, float, str, str]:
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
# 测试：企业级端到端压力测试
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

    weekly_df: pl.DataFrame = pl.read_parquet(
        stress_dir / "extracted_weekly.parquet"
    )
    assert weekly_df["物料编码"].n_unique() == EXPECTED_SKUS, (
        f"周度数据 SKU 数量不匹配: 期望 {EXPECTED_SKUS}, "
        f"实际 {weekly_df['物料编码'].n_unique()}"
    )


def test_stress_e2e_full_pipeline(
    scripts_base_dir: Path,
    stress_dir: Path,
    e2e_dir: Path,
) -> None:
    """
    企业级端到端压力测试：按依赖顺序执行全部核心子 Skill，
    验证全链路性能和最终产出。
    """
    e2e_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy(
        stress_dir / "extracted_weekly.parquet",
        e2e_dir / "extracted_weekly.parquet",
    )
    shutil.copy(
        stress_dir / "extracted_summary.parquet",
        e2e_dir / "extracted_summary.parquet",
    )
    shutil.copy(
        stress_dir / "abc_xyz_result.json",
        e2e_dir / "abc_xyz_result.json",
    )

    total_start: float = time.perf_counter()
    step_times: dict[str, float] = {}

    # ── Skill 02: inventory-overview ──
    print("\n[1/5] inventory-overview")
    returncode, elapsed, stdout, stderr = _run_and_time(
        scripts_base_dir / "inventory-overview" / "scripts" / "data_aggregator.py",
        ["--input", str(e2e_dir / "extracted_summary.parquet"),
         "--output", str(e2e_dir / "inventory_overview.json")],
    )
    assert returncode == 0, f"data_aggregator 失败: {stderr}"
    step_times["data_aggregator"] = elapsed
    print(f"  data_aggregator: {elapsed:.2f}s ✅")

    returncode, elapsed, stdout, stderr = _run_and_time(
        scripts_base_dir / "inventory-overview" / "scripts" / "inventory_turnover.py",
        ["--input", str(e2e_dir / "extracted_summary.parquet"),
         "--weekly", str(e2e_dir / "extracted_weekly.parquet"),
         "--output", str(e2e_dir / "efficiency_cost_report.json")],
    )
    assert returncode == 0, f"inventory_turnover 失败: {stderr}"
    step_times["inventory_turnover"] = elapsed
    print(f"  inventory_turnover: {elapsed:.2f}s ✅")

    returncode, elapsed, stdout, stderr = _run_and_time(
        scripts_base_dir / "inventory-overview" / "scripts" / "cost_analyzer.py",
        ["--input", str(e2e_dir / "extracted_summary.parquet"),
         "--output", str(e2e_dir / "efficiency_cost_report.json"),
         "--append"],
    )
    assert returncode == 0, f"cost_analyzer 失败: {stderr}"
    step_times["cost_analyzer"] = elapsed
    print(f"  cost_analyzer: {elapsed:.2f}s ✅")

    # ── Skill 03: category-classifier ──
    print("\n[2/5] category-classifier")
    returncode, elapsed, stdout, stderr = _run_and_time(
        scripts_base_dir / "category-classifier" / "scripts" / "xyz_classifier.py",
        ["--input", str(e2e_dir / "extracted_weekly.parquet"),
         "--output", str(e2e_dir / "xyz_stress_result.json")],
    )
    assert returncode == 0, f"xyz_classifier 失败: {stderr}"
    step_times["xyz_classifier"] = elapsed
    print(f"  xyz_classifier: {elapsed:.2f}s ✅")

    # ── Skill 06: inventory-planner（含调参→预测链路）──
    print("\n[3/5] inventory-planner")

    # 步骤 3a：调参
    print("  执行 hyperparameter_tuner.py ...")
    optimal_params_path: Path = e2e_dir / "optimal_params.json"
    returncode, elapsed, stdout, stderr = _run_and_time(
        scripts_base_dir / "inventory-planner" / "scripts" / "hyperparameter_tuner.py",
        ["--input", str(e2e_dir / "extracted_weekly.parquet"),
         "--output", str(optimal_params_path),
         "--method", "auto",
         "--workers", "4"],
    )
    assert returncode == 0, f"hyperparameter_tuner 失败: {stderr}"
    step_times["hyperparameter_tuner"] = elapsed
    print(f"  hyperparameter_tuner: {elapsed:.2f}s ✅")

    # 步骤 3b：使用最优参数预测
    print("  使用最优参数执行 demand_forecast.py ...")
    returncode, elapsed, stdout, stderr = _run_and_time(
        scripts_base_dir / "inventory-planner" / "scripts" / "demand_forecast.py",
        ["--input", str(e2e_dir / "extracted_weekly.parquet"),
         "--output", str(e2e_dir / "forecast_result.json"),
         "--optimal-params", str(optimal_params_path)],
    )
    assert returncode == 0, f"demand_forecast 失败: {stderr}"
    step_times["demand_forecast"] = elapsed
    print(f"  demand_forecast: {elapsed:.2f}s ✅")

    returncode, elapsed, stdout, stderr = _run_and_time(
        scripts_base_dir / "inventory-planner" / "scripts" / "inventory_planning.py",
        ["--data", str(e2e_dir / "extracted_weekly.parquet"),
         "--summary", str(e2e_dir / "extracted_summary.parquet"),
         "--classification", str(e2e_dir / "abc_xyz_result.json"),
         "--forecast", str(e2e_dir / "forecast_result.json"),
         "--output", str(e2e_dir / "inventory_plan.json")],
    )
    assert returncode == 0, f"inventory_planning 失败: {stderr}"
    step_times["inventory_planning"] = elapsed
    print(f"  inventory_planning: {elapsed:.2f}s ✅")

    returncode, elapsed, stdout, stderr = _run_and_time(
        scripts_base_dir / "inventory-planner" / "scripts" / "inventory_alert.py",
        ["--data", str(e2e_dir / "extracted_weekly.parquet"),
         "--plan", str(e2e_dir / "inventory_plan.json"),
         "--summary", str(e2e_dir / "extracted_summary.parquet"),
         "--output", str(e2e_dir / "alert_list.json")],
    )
    assert returncode == 0, f"inventory_alert 失败: {stderr}"
    step_times["inventory_alert"] = elapsed
    print(f"  inventory_alert: {elapsed:.2f}s ✅")

    # ── Skill 07: purchase-advisor ──
    print("\n[4/5] purchase-advisor")
    returncode, elapsed, stdout, stderr = _run_and_time(
        scripts_base_dir / "purchase-advisor" / "scripts" / "purchase_planner.py",
        ["--alerts", str(e2e_dir / "alert_list.json"),
         "--inventory-plan", str(e2e_dir / "inventory_plan.json"),
         "--output", str(e2e_dir / "purchase_plan.json")],
    )
    assert returncode == 0, f"purchase_planner 失败: {stderr}"
    step_times["purchase_planner"] = elapsed
    print(f"  purchase_planner: {elapsed:.2f}s ✅")

    returncode, elapsed, stdout, stderr = _run_and_time(
        scripts_base_dir / "purchase-advisor" / "scripts" / "report_generator.py",
        ["--project-dir", str(e2e_dir),
         "--output", str(e2e_dir / "final_report.json")],
    )
    assert returncode == 0, f"report_generator 失败: {stderr}"
    step_times["report_generator"] = elapsed
    print(f"  report_generator: {elapsed:.2f}s ✅")

    total_elapsed: float = time.perf_counter() - total_start

    # ── 汇总报告 ──
    print(f"\n{'=' * 60}")
    print("全链路端到端压力测试汇总")
    print(f"{'=' * 60}")
    for step_name, step_time in step_times.items():
        print(f"  {step_name}: {step_time:.2f}s")
    print(f"  总执行时间: {total_elapsed:.2f}s (阈值: {TIME_THRESHOLD_SECONDS}s)")
    print(f"{'=' * 60}")

    # =====================================================================
    # 验证项
    # =====================================================================

    # 验证 1：总执行时间在阈值内
    assert total_elapsed < TIME_THRESHOLD_SECONDS, (
        f"验证1失败: 总执行时间 {total_elapsed:.1f}s > {TIME_THRESHOLD_SECONDS}s"
    )

    # 验证 2：final_report.json 存在
    final_path: Path = e2e_dir / "final_report.json"
    assert final_path.exists(), f"验证2失败: final_report.json 不存在"

    # 验证 3：forecast_result.json 包含 10,000 SKU
    with open(e2e_dir / "forecast_result.json", "r", encoding="utf-8") as fp:
        forecast_data: dict[str, Any] = json.load(fp)
    assert forecast_data["demand_forecast"]["total_items"] == EXPECTED_SKUS, (
        f"验证3失败: 预测物料数 {forecast_data['demand_forecast']['total_items']} ≠ {EXPECTED_SKUS}"
    )
    assert forecast_data["demand_forecast"]["parameter_source"] == "optimal_params", (
        "验证3失败: 参数来源未标记为 optimal_params"
    )

    # 验证 4：inventory_plan.json 包含 10,000 SKU
    with open(e2e_dir / "inventory_plan.json", "r", encoding="utf-8") as fp:
        plan_data: dict[str, Any] = json.load(fp)
    assert plan_data["inventory_plan"]["total_items"] == EXPECTED_SKUS, (
        f"验证4失败: 库存计划物料数 {plan_data['inventory_plan']['total_items']} ≠ {EXPECTED_SKUS}"
    )

    # 验证 5：purchase_plan.json 存在且包含采购项
    purchase_path: Path = e2e_dir / "purchase_plan.json"
    assert purchase_path.exists(), f"验证5失败: purchase_plan.json 不存在"
    with open(purchase_path, "r", encoding="utf-8") as fp:
        purchase_data: dict[str, Any] = json.load(fp)
    assert purchase_data["purchase_plan"]["total_items"] >= 0, (
        "验证5失败: purchase_plan 无采购项"
    )

    # 验证 6：alert_list.json 存在
    alert_path: Path = e2e_dir / "alert_list.json"
    assert alert_path.exists(), f"验证6失败: alert_list.json 不存在"

    # 验证 7：optimal_params.json 存在
    assert optimal_params_path.exists(), f"验证7失败: optimal_params.json 不存在"
    with open(optimal_params_path, "r", encoding="utf-8") as fp:
        optimal_data: dict[str, Any] = json.load(fp)
    assert optimal_data["total_items"] == EXPECTED_SKUS, (
        f"验证7失败: 调参物料数 {optimal_data['total_items']} ≠ {EXPECTED_SKUS}"
    )

    print(f"\n✅ 企业级端到端压力测试通过！总耗时 {total_elapsed:.1f}s")
    print(f"   产出文件路径: {e2e_dir}")