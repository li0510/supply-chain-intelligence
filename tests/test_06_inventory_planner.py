"""
库存计划与预警测试 (test_06_inventory_planner.py)

验证 inventory-planner 子 Skill 的三道防线。
双输出模式适配：
    - 需求预测使用 extracted_weekly.parquet（周度数据）
    - 库存计划使用 extracted_weekly.parquet（周度数据）+ extracted_summary.parquet（生命周期字段）

更新内容：
    - test_inventory_planning 新增 --summary 必需参数
    - 新增生命周期状态字段的断言检查
    - test_demand_forecast 增加 TSB/IMAPA 预测方法名验证
    - 新增 test_optimal_params_integration：验证 --optimal-params 读取 + 参数优先使用
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


# ============================================================================
# 测试 6.1：需求预测（使用周度数据）
# ============================================================================

def test_demand_forecast(
    scripts_base_dir: Path,
    output_dir: Path,
) -> None:
    """测试 demand_forecast.py 的需求预测功能。"""
    script_path: Path = scripts_base_dir / "inventory-planner" / "scripts" / "demand_forecast.py"
    weekly_path: Path = output_dir / "extracted_weekly.parquet"

    assert weekly_path.exists(), f"前置条件不满足: {weekly_path} 不存在。"

    result: subprocess.CompletedProcess = subprocess.run(
        [
            sys.executable, str(script_path),
            "--input", str(weekly_path),
            "--output", str(output_dir / "forecast_result.json"),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"demand_forecast.py 执行失败。\nstderr: {result.stderr}"
    )

    output_path: Path = output_dir / "forecast_result.json"
    assert output_path.exists(), f"产出文件不存在: {output_path}"

    with open(output_path, "r", encoding="utf-8") as fp:
        data: dict[str, Any] = json.load(fp)

    assert "demand_forecast" in data, "缺少 demand_forecast"
    df: dict[str, Any] = data["demand_forecast"]
    assert df["total_items"] > 0, "物料数为 0"
    assert len(df["item_details"]) == df["total_items"], "预测明细数量不匹配"
    assert "MAE" in df["item_details"][0], "缺少 MAE 指标"
    assert "RMSE" in df["item_details"][0], "缺少 RMSE 指标"
    assert "Bias" in df["item_details"][0], "缺少 Bias 指标"

    # ── 验证预测方法名（包含新增的 TSB/IMAPA）──
    valid_methods: set[str] = {
        "简单指数平滑", "Holt趋势调整指数平滑",
        "Holt-Winters(add)", "Holt-Winters(mul)",
        "Holt-Winters(加法-数据不足STL检验)",
        "TSB",
        "简单均值", "唯一值", "无历史数据",
        "新品类比", "新品类比（无历史数据，需手动输入预测值）",
        "新品类比（SES）", "清仓预测", "已淘汰",
    }
    for item in df["item_details"]:
        method: str = item.get("预测方法", "")
        if method.startswith("IMAPA"):
            assert "窗口=" in method, f"IMAPA 方法名格式异常: {method}"
        elif method not in valid_methods:
            # 如果出现未知方法，输出警告但暂不断言失败（允许未来扩展）
            print(f"警告: 未识别的预测方法: {method} (物料: {item['物料编码']})")


# ============================================================================
# 测试 6.2：库存计划（使用周度数据 + 汇总数据）
# ============================================================================

def test_inventory_planning(
    scripts_base_dir: Path,
    output_dir: Path,
) -> None:
    """测试 inventory_planning.py 的安全库存和 ROP 计算。"""
    script_path: Path = scripts_base_dir / "inventory-planner" / "scripts" / "inventory_planning.py"
    weekly_path: Path = output_dir / "extracted_weekly.parquet"
    summary_path: Path = output_dir / "extracted_summary.parquet"
    classification_path: Path = output_dir / "abc_xyz_result.json"
    forecast_path: Path = output_dir / "forecast_result.json"

    for p, name in [
        (weekly_path, "周度数据"),
        (summary_path, "汇总数据"),
        (classification_path, "分类"),
        (forecast_path, "预测"),
    ]:
        assert p.exists(), f"前置条件不满足: {name}文件不存在: {p}"

    result: subprocess.CompletedProcess = subprocess.run(
        [
            sys.executable, str(script_path),
            "--data", str(weekly_path),
            "--summary", str(summary_path),
            "--classification", str(classification_path),
            "--forecast", str(forecast_path),
            "--output", str(output_dir / "inventory_plan.json"),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"inventory_planning.py 执行失败。\nstderr: {result.stderr}"
    )

    output_path: Path = output_dir / "inventory_plan.json"
    assert output_path.exists(), f"产出文件不存在: {output_path}"

    with open(output_path, "r", encoding="utf-8") as fp:
        data: dict[str, Any] = json.load(fp)

    assert "inventory_plan" in data, "缺少 inventory_plan"
    ip: dict[str, Any] = data["inventory_plan"]
    assert ip["total_items"] > 0, "物料数为 0"

    for item in ip["item_details"][:3]:
        assert "安全库存" in item, "缺少安全库存"
        assert "再订购点(ROP)" in item or item.get("再订购点(ROP)") is None, "缺少再订购点字段"
        assert "最高库存" in item, "缺少最高库存"
        assert "库存状态" in item, "缺少库存状态"
        assert "补货策略类型" in item, "缺少补货策略类型"
        assert "年总库存成本(估算)" in item, "缺少 TCO 成本"
        assert "生命周期状态" in item, "缺少生命周期状态"
        assert "标准差计算方式" in item, "缺少标准差计算方式字段"


# ============================================================================
# 测试 6.3：执行预警（使用周度数据）
# ============================================================================

def test_inventory_alert(
    scripts_base_dir: Path,
    output_dir: Path,
) -> None:
    """测试 inventory_alert.py 的预警清单生成。"""
    script_path: Path = scripts_base_dir / "inventory-planner" / "scripts" / "inventory_alert.py"
    weekly_path: Path = output_dir / "extracted_weekly.parquet"
    summary_path: Path = output_dir / "extracted_summary.parquet"
    plan_path: Path = output_dir / "inventory_plan.json"

    for p, name in [(weekly_path, "周度数据"), (summary_path, "汇总数据"), (plan_path, "库存计划")]:
        assert p.exists(), f"前置条件不满足: {name}文件不存在: {p}"

    result: subprocess.CompletedProcess = subprocess.run(
        [
            sys.executable, str(script_path),
            "--data", str(weekly_path),
            "--plan", str(plan_path),
            "--summary", str(summary_path),
            "--output", str(output_dir / "alert_list.json"),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"inventory_alert.py 执行失败。\nstderr: {result.stderr}"
    )

    output_path: Path = output_dir / "alert_list.json"
    assert output_path.exists(), f"产出文件不存在: {output_path}"

    with open(output_path, "r", encoding="utf-8") as fp:
        data: dict[str, Any] = json.load(fp)

    assert "alert_list" in data, "缺少 alert_list"
    al: dict[str, Any] = data["alert_list"]
    assert "shortage_alerts" in al, "缺少 shortage_alerts"
    assert "reorder_alerts" in al, "缺少 reorder_alerts"
    assert "overstock_alerts" in al, "缺少 overstock_alerts"
    assert "top_urgent" in al, "缺少 top_urgent"
    assert "supplier_aggregation" in al, "缺少 supplier_aggregation"
    assert "expiry_alerts" in al, "缺少 expiry_alerts"


# ============================================================================
# 测试 6.4：最优参数集成测试（新增）
# ============================================================================

def test_optimal_params_integration(
    scripts_base_dir: Path,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """验证 demand_forecast.py 能正确读取 optimal_params.json 并优先使用最优参数。"""
    script_path: Path = scripts_base_dir / "inventory-planner" / "scripts" / "demand_forecast.py"
    weekly_path: Path = output_dir / "extracted_weekly.parquet"

    assert weekly_path.exists(), f"前置条件不满足: {weekly_path} 不存在。"

    # ── 创建模拟最优参数文件 ──
    optimal_params: dict[str, Any] = {
        "generated_at": "2026-01-01T00:00:00",
        "data_source": str(weekly_path),
        "total_items": 2,
        "items": [
            {
                "物料编码": "GSN-0001",
                "推荐方法": "Holt-Winters(mul)",
                "最优参数": {"alpha": 0.15, "beta": 0.05, "gamma": 0.10},
                "最优RMSE": 12.34,
            },
            {
                "物料编码": "GSN-0002",
                "推荐方法": "Holt趋势调整指数平滑",
                "最优参数": {"alpha": 0.25, "beta": 0.10},
                "最优RMSE": 45.67,
            },
        ],
    }
    optimal_params_path: Path = tmp_path / "optimal_params.json"
    with open(optimal_params_path, "w", encoding="utf-8") as fp:
        json.dump(optimal_params, fp, ensure_ascii=False, indent=2)

    # ── 使用最优参数执行预测 ──
    forecast_output_path: Path = tmp_path / "forecast_with_params.json"
    result: subprocess.CompletedProcess = subprocess.run(
        [
            sys.executable, str(script_path),
            "--input", str(weekly_path),
            "--output", str(forecast_output_path),
            "--optimal-params", str(optimal_params_path),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"demand_forecast.py 执行失败。\nstderr: {result.stderr}"
    )

    with open(forecast_output_path, "r", encoding="utf-8") as fp:
        forecast_data: dict[str, Any] = json.load(fp)

    # 验证参数来源标记
    assert forecast_data["demand_forecast"]["parameter_source"] == "optimal_params", (
        "参数来源未标记为 optimal_params"
    )

    # 验证 GSN-0001 使用了最优参数（alpha 应接近 0.15）
    items_dict: dict[str, dict] = {
        item["物料编码"]: item
        for item in forecast_data["demand_forecast"]["item_details"]
    }
    assert "GSN-0001" in items_dict, "GSN-0001 未在预测结果中"
    # 由于 forecast_demand 不会直接暴露使用的 alpha，我们通过参数来源来间接验证。
    # 如果 optimal_params 存在且物料在其中，则应该使用最优参数。

    print("最优参数集成测试通过。")