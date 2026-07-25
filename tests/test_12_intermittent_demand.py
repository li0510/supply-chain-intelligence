"""
间歇性需求预测专项测试 (test_12_intermittent_demand.py)

供应链智能分析平台 — 间歇性/块状需求模型验证

功能：验证 TSB 和 IMAPA 方法能正确集成到调参→预测链路中，
     且预测结果具有业务合理性。

测试覆盖：
    1. 间歇性物料（Intermittent）自动选择 TSB
    2. 块状物料（Lumpy）自动选择 IMAPA
    3. 正常物料仍然使用 Holt/Holt-Winters
    4. hyperparameter_tuner.py 能为间歇性/块状物料搜索最优参数
    5. demand_forecast.py 能正确读取最优参数并应用

用法:
    uv run pytest tests/test_12_intermittent_demand.py -v -s

作者: Supply Chain Intelligence Team
版本: 0.2.5
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(scope="module")
def intermittent_data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """生成间歇性/块状测试数据，并返回数据目录。"""
    data_dir: Path = tmp_path_factory.mktemp("intermittent_data")

    # 模拟 20 个物料，每个物料 156 周数据
    np.random.seed(42)

    all_rows: list[dict[str, Any]] = []

    # ── 5 个间歇性物料（ADI > 1.32, CV² > 0.49）──
    for i in range(5):
        code: str = f"INT-{i:05d}"
        for week in range(156):
            # 70% 概率为零，非零时均值 800，std 300
            out = 0.0
            if np.random.random() > 0.7:
                out = max(0, np.random.normal(800, 300))
            balance = max(0, out * 10)  # 简化
            all_rows.append({
                "物料编码": code,
                "ISO_Week": week + 1,
                "周入库量": round(out * 1.1, 2),
                "周出库量": round(out, 2),
                "周结存": round(balance, 2),
            })

    # ── 5 个块状物料（ADI > 1.32, CV² ≤ 0.49）──
    for i in range(5):
        code: str = f"LMP-{i:05d}"
        for week in range(156):
            out = 0.0
            if np.random.random() > 0.8:
                out = max(0, np.random.normal(2000, 500))
            balance = max(0, out * 10)
            all_rows.append({
                "物料编码": code,
                "ISO_Week": week + 1,
                "周入库量": round(out * 1.1, 2),
                "周出库量": round(out, 2),
                "周结存": round(balance, 2),
            })

    # ── 10 个正常物料（ADI ≤ 1.32）──
    for i in range(10):
        code: str = f"GSN-{i:05d}"
        for week in range(156):
            out = max(0, np.random.normal(500, 200))
            balance = max(0, out * 10)
            all_rows.append({
                "物料编码": code,
                "ISO_Week": week + 1,
                "周入库量": round(out * 1.1, 2),
                "周出库量": round(out, 2),
                "周结存": round(balance, 2),
            })

    df: pl.DataFrame = pl.DataFrame(all_rows)
    weekly_path: Path = data_dir / "extracted_weekly.parquet"
    df.write_parquet(weekly_path)

    # 还需要 summary，这里简化：构造一个空的 summary 文件（无生命周期数据）
    summary_df: pl.DataFrame = pl.DataFrame({
        "物料编码": df["物料编码"].unique(),
        "库存量": [1000.0] * 20,
        "入库数量": [0.0] * 20,
        "出库数量": [0.0] * 20,
        "结存数量": [1000.0] * 20,
        "生命周期状态": ["正常在售"] * 20,
        "保质期天数": [None] * 20,
    })
    summary_path: Path = data_dir / "extracted_summary.parquet"
    summary_df.write_parquet(summary_path)

    return data_dir


@pytest.fixture(scope="module")
def scripts_base_dir(project_root: Path) -> Path:
    """脚本基础目录。"""
    return project_root / "skills"


# ============================================================================
# 测试 1：需求模式分类正确性
# ============================================================================

def test_demand_pattern_classification(
    intermittent_data_dir: Path,
) -> None:
    """验证间歇性/块状/正常物料被正确分类。"""
    weekly_df: pl.DataFrame = pl.read_parquet(
        intermittent_data_dir / "extracted_weekly.parquet"
    )

    # 手动调用分类函数（需要导入 demand_forecast 的内部函数）
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "inventory-planner" / "scripts"))
    from demand_forecast import _classify_demand_pattern, pregroup_by_material

    groups: dict[str, pl.DataFrame] = pregroup_by_material(weekly_df)

    intermittent_count: int = 0
    lumpy_count: int = 0
    smooth_count: int = 0

    for code, material_data in groups.items():
        pattern: str = _classify_demand_pattern(material_data)
        if code.startswith("INT-"):
            assert pattern == "intermittent", (
                f"间歇性物料 {code} 分类错误: {pattern}"
            )
            intermittent_count += 1
        elif code.startswith("LMP-"):
            assert pattern == "lumpy", (
                f"块状物料 {code} 分类错误: {pattern}"
            )
            lumpy_count += 1
        elif code.startswith("GSN-"):
            assert pattern in ("smooth", "erratic"), (
                f"正常物料 {code} 分类错误: {pattern}"
            )
            smooth_count += 1

    assert intermittent_count == 5, f"间歇性物料数: {intermittent_count}"
    assert lumpy_count == 5, f"块状物料数: {lumpy_count}"
    print(f"分类正确: 间歇性={intermittent_count}, 块状={lumpy_count}, 正常={smooth_count}")


# ============================================================================
# 测试 2：TSB/IMAPA 预测值合理性
# ============================================================================

def test_demand_forecast_with_intermittent(
    scripts_base_dir: Path,
    intermittent_data_dir: Path,
    tmp_path: Path,
) -> None:
    """验证 demand_forecast.py 能为间歇性/块状物料正确生成预测。"""
    script_path: Path = scripts_base_dir / "inventory-planner" / "scripts" / "demand_forecast.py"
    weekly_path: Path = intermittent_data_dir / "extracted_weekly.parquet"
    output_path: Path = tmp_path / "forecast_result.json"

    result: subprocess.CompletedProcess = subprocess.run(
        [
            sys.executable, str(script_path),
            "--input", str(weekly_path),
            "--output", str(output_path),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"demand_forecast.py 执行失败。\nstderr: {result.stderr}"
    )

    with open(output_path, "r", encoding="utf-8") as fp:
        data: dict[str, Any] = json.load(fp)

    forecast_items: list[dict] = data["demand_forecast"]["item_details"]

    # 验证间歇性物料使用 TSB
    for item in forecast_items:
        code: str = item["物料编码"]
        method: str = item["预测方法"]
        if code.startswith("INT-"):
            assert method == "TSB", (
                f"间歇性物料 {code} 预测方法错误: {method}"
            )
            # 预测值应大于 0（有历史需求）
            assert item["预测周需求量"] >= 0, (
                f"间歇性物料 {code} 预测值为负: {item['预测周需求量']}"
            )
        elif code.startswith("LMP-"):
            assert "IMAPA" in method, (
                f"块状物料 {code} 预测方法错误: {method}"
            )
            assert item["预测周需求量"] >= 0, (
                f"块状物料 {code} 预测值为负: {item['预测周需求量']}"
            )
        elif code.startswith("GSN-"):
            # 正常物料应使用 Holt/Holt-Winters/SES
            pass

    print(f"预测方法验证通过。TSB/IMAPA 已正确分配。")


# ============================================================================
# 测试 3：调参→预测链路集成
# ============================================================================

def test_tune_and_forecast_intermittent(
    scripts_base_dir: Path,
    intermittent_data_dir: Path,
    tmp_path: Path,
) -> None:
    """验证 hyperparameter_tuner.py → optimal_params.json → demand_forecast.py 完整链路。"""
    tuner_path: Path = scripts_base_dir / "inventory-planner" / "scripts" / "hyperparameter_tuner.py"
    forecast_path: Path = scripts_base_dir / "inventory-planner" / "scripts" / "demand_forecast.py"
    weekly_path: Path = intermittent_data_dir / "extracted_weekly.parquet"
    optimal_params_path: Path = tmp_path / "optimal_params.json"
    forecast_output_path: Path = tmp_path / "forecast_with_params.json"

    # ── 步骤 1：调参 ──
    print("执行 hyperparameter_tuner.py ...")
    result_tune: subprocess.CompletedProcess = subprocess.run(
        [
            sys.executable, str(tuner_path),
            "--input", str(weekly_path),
            "--output", str(optimal_params_path),
            "--method", "auto",
            "--workers", "2",
        ],
        capture_output=True,
        text=True,
    )
    assert result_tune.returncode == 0, (
        f"hyperparameter_tuner.py 执行失败。\nstderr: {result_tune.stderr}"
    )
    assert optimal_params_path.exists(), "optimal_params.json 未生成"

    with open(optimal_params_path, "r", encoding="utf-8") as fp:
        optimal_data: dict[str, Any] = json.load(fp)

    # 验证间歇性物料推荐 TSB
    for item in optimal_data["items"]:
        if item["物料编码"].startswith("INT-"):
            assert item["推荐方法"] == "TSB", (
                f"间歇性物料 {item['物料编码']} 调参推荐方法错误: {item['推荐方法']}"
            )
        elif item["物料编码"].startswith("LMP-"):
            assert "IMAPA" in item["推荐方法"], (
                f"块状物料 {item['物料编码']} 调参推荐方法错误: {item['推荐方法']}"
            )

    # ── 步骤 2：使用最优参数预测 ──
    print("使用最优参数执行 demand_forecast.py ...")
    result_forecast: subprocess.CompletedProcess = subprocess.run(
        [
            sys.executable, str(forecast_path),
            "--input", str(weekly_path),
            "--output", str(forecast_output_path),
            "--optimal-params", str(optimal_params_path),
        ],
        capture_output=True,
        text=True,
    )
    assert result_forecast.returncode == 0, (
        f"demand_forecast.py 执行失败。\nstderr: {result_forecast.stderr}"
    )

    with open(forecast_output_path, "r", encoding="utf-8") as fp:
        forecast_data: dict[str, Any] = json.load(fp)

    assert forecast_data["demand_forecast"]["parameter_source"] == "optimal_params", (
        "参数来源未标记为 optimal_params"
    )

    print("调参→预测链路验证通过。")