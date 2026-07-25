"""
高性能并行调参脚本 (hyperparameter_tuner.py)

供应链智能分析平台 — inventory-planner 子 Skill — 离线优化工具

功能：为每个物料自动搜索最优的预测模型参数，最小化 RMSE。
     支持 SES、Holt's Linear、Holt-Winters、TSB、IMAPA 方法。
     使用三级并行策略（SKU 级 + 参数级 + 向量化）加速搜索。

搜索空间：
    SES：          α ∈ [0.05, 0.95]，步长 0.05，共 19 个组合
    Holt's Linear： α, β ∈ [0.05, 0.95]，步长 0.05，共 361 个组合
    Holt-Winters：  α, β, γ ∈ [0.05, 0.95]，步长 0.05，共 6,859 个组合
    TSB：           α, β ∈ [0.05, 0.40]，步长 0.05，共 64 个组合
    IMAPA：         窗口选择 [1, 2, 4]（及可选 8, 16），非连续参数搜索

并行策略：
    - SKU 级并行：ProcessPoolExecutor，每个 Worker 处理一个 SKU
    - 参数级并行：ThreadPoolExecutor，对单个 SKU 的 Holt/Holt-Winters/TSB 并行评估参数
    - 向量化加速：NumPy 向量化计算全部时间步的 RMSE

模型选择逻辑（与 demand_forecast.py 保持一致）：
    - 已淘汰/老品下市 → 跳过调参
    - ADI > 1.32, CV² > 0.49 → TSB（间歇性需求）
    - ADI > 1.32, CV² ≤ 0.49 → IMAPA（块状需求）
    - n ≥ 156 → Holt-Winters（STL 自动选择）
    - 104 ≤ n < 156 → Holt-Winters（加法）
    - 8 ≤ n < 104 → Holt
    - 3 ≤ n < 8 → SES

结果缓存：
    - 调参结果保存为 optimal_params.json
    - 下次运行时，若数据未变，自动跳过已有结果

与 demand_forecast.py 的耦合：
    - 本脚本 import demand_forecast.py 的核心函数，直接调用，不重复实现预测逻辑。
    - 产出的 optimal_params.json 通过 demand_forecast.py 的 --optimal-params
      参数读取，在预测时优先使用最优参数。

用法:
    uv run hyperparameter_tuner.py --input <extracted_weekly.parquet路径> \
      --output <optimal_params.json路径> \
      [--method auto] [--workers 4] [--force]

作者: Supply Chain Intelligence Team
版本: 0.2.5
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

# 直接 import demand_forecast 的核心函数，复用已有预测逻辑
from demand_forecast import (
    pregroup_by_material,
    _holt_forecast_from_group,
    _holt_winters_forecast_from_group,
    _calculate_metrics_from_group,
    _get_forecast_by_lifecycle,
    _detect_seasonal_mode,
    _classify_demand_pattern,
    _tsb_forecast_from_group,
    _imapa_forecast_from_group,
    DEFAULT_ALPHA,
    DEFAULT_BETA,
    DEFAULT_GAMMA,
    DEFAULT_SEASONAL_PERIODS,
    DEFAULT_TSB_ALPHA,
    DEFAULT_TSB_BETA,
    DEFAULT_IMAPA_MAX_WINDOW,
    MIN_DATA_POINTS_FOR_HOLT,
    MIN_DATA_POINTS_FOR_HW,
    MIN_DATA_POINTS_FOR_STL,
)


# ============================================================================
# 配置
# ============================================================================

PARAM_GRID_STANDARD: list[float] = [round(x * 0.05, 2) for x in range(1, 20)]
PARAM_GRID_TSB: list[float] = [round(x * 0.05, 2) for x in range(1, 9)]

DEFAULT_WORKERS: int = 4
DEFAULT_METHOD: str = "auto"


# ============================================================================
# 单个 SKU 的调参逻辑（在 Worker 进程中执行）
# ============================================================================

def _tune_single_sku(
    code: str,
    material_data: pl.DataFrame,
    n_points: int,
    mean_val: float,
    last_val: float,
    lifecycle_status: str,
    method_override: str | None,
) -> dict[str, Any]:
    """
    为单个 SKU 搜索最优参数。

    根据需求模式和生命周期状态选择模型类型，
    然后在参数空间中搜索最小 RMSE 的参数组合。

    Parameters
    ----------
    code : str
        物料编码。
    material_data : pl.DataFrame
        该物料的周度数据（已按 ISO_Week 排序）。
    n_points : int
        数据点数。
    mean_val : float
        周出库均值。
    last_val : float
        最近周出库量。
    lifecycle_status : str
        物料生命周期状态。
    method_override : str | None
        用户指定的方法（"ses"/"holt"/"holt-winters"/"tsb"/"imapa"），None 表示自动选择。

    Returns
    -------
    dict[str, Any]
        该物料的最优参数和 RMSE。
    """
    # ── 生命周期状态判断 ──
    lifecycle_forecast, lifecycle_method, skip_normal = _get_forecast_by_lifecycle(
        lifecycle_status, material_data, n_points, mean_val, last_val
    )
    if skip_normal:
        return {
            "物料编码": code,
            "生命周期状态": lifecycle_status,
            "推荐方法": lifecycle_method,
            "最优参数": {},
            "最优RMSE": 0.0,
            "数据点数": n_points,
            "说明": "生命周期状态跳过调参",
        }

    # ── 需求模式判断 ──
    demand_pattern: str = _classify_demand_pattern(material_data)

    # ── 确定模型类型 ──
    if method_override == "ses":
        model_type: str = "ses"
    elif method_override == "holt":
        model_type = "holt"
    elif method_override == "holt-winters":
        model_type = "holt-winters"
    elif method_override == "tsb":
        model_type = "tsb"
    elif method_override == "imapa":
        model_type = "imapa"
    elif demand_pattern == "intermittent":
        model_type = "tsb"
    elif demand_pattern == "lumpy":
        model_type = "imapa"
    elif n_points >= MIN_DATA_POINTS_FOR_STL:
        model_type = "holt-winters"
    elif n_points >= MIN_DATA_POINTS_FOR_HW:
        model_type = "holt-winters"
    elif n_points >= MIN_DATA_POINTS_FOR_HOLT:
        model_type = "holt"
    elif n_points >= 3:
        model_type = "ses"
    else:
        return {
            "物料编码": code,
            "生命周期状态": lifecycle_status,
            "需求模式": demand_pattern,
            "推荐方法": "数据不足",
            "最优参数": {},
            "最优RMSE": 0.0,
            "数据点数": n_points,
            "说明": "数据点数不足3，无法调参",
        }

    # ── 参数搜索 ──
    if model_type == "ses":
        return _tune_ses(code, lifecycle_status, demand_pattern, material_data, n_points)
    elif model_type == "holt":
        return _tune_holt(code, lifecycle_status, demand_pattern, material_data, n_points)
    elif model_type == "holt-winters":
        return _tune_hw(code, lifecycle_status, demand_pattern, material_data, n_points)
    elif model_type == "tsb":
        return _tune_tsb(code, lifecycle_status, demand_pattern, material_data, n_points)
    else:
        return _tune_imapa(code, lifecycle_status, demand_pattern, material_data, n_points)


def _tune_ses(
    code: str,
    lifecycle_status: str,
    demand_pattern: str,
    material_data: pl.DataFrame,
    n_points: int,
) -> dict[str, Any]:
    """SES 调参：遍历 19 个 α，找最小 RMSE。"""
    best_rmse: float = float("inf")
    best_params: dict[str, float] = {}

    for alpha in PARAM_GRID_STANDARD:
        forecast_val, _ = _holt_forecast_from_group(material_data, alpha, 0.0)
        _, rmse, _ = _calculate_metrics_from_group(material_data, forecast_val)
        if rmse < best_rmse:
            best_rmse = rmse
            best_params = {"alpha": alpha}

    return {
        "物料编码": code,
        "生命周期状态": lifecycle_status,
        "需求模式": demand_pattern,
        "推荐方法": "简单指数平滑(SES)",
        "最优参数": best_params,
        "最优RMSE": round(best_rmse, 4),
        "数据点数": n_points,
    }


def _tune_holt(
    code: str,
    lifecycle_status: str,
    demand_pattern: str,
    material_data: pl.DataFrame,
    n_points: int,
) -> dict[str, Any]:
    """Holt 调参：遍历 361 个 (α, β) 组合，使用 ThreadPoolExecutor 并行。"""
    best_rmse: float = float("inf")
    best_params: dict[str, float] = {}

    def _eval_holt(params: tuple[float, float]) -> tuple[float, float, float]:
        alpha, beta = params
        forecast_val, _ = _holt_forecast_from_group(material_data, alpha, beta)
        _, rmse, _ = _calculate_metrics_from_group(material_data, forecast_val)
        return (alpha, beta, rmse)

    param_combos: list[tuple[float, float]] = [
        (alpha, beta) for alpha in PARAM_GRID_STANDARD for beta in PARAM_GRID_STANDARD
    ]

    with ThreadPoolExecutor(max_workers=min(8, len(param_combos))) as executor:
        futures = {executor.submit(_eval_holt, combo): combo for combo in param_combos}
        for future in as_completed(futures):
            alpha, beta, rmse = future.result()
            if rmse < best_rmse:
                best_rmse = rmse
                best_params = {"alpha": alpha, "beta": beta}

    return {
        "物料编码": code,
        "生命周期状态": lifecycle_status,
        "需求模式": demand_pattern,
        "推荐方法": "Holt趋势调整指数平滑",
        "最优参数": best_params,
        "最优RMSE": round(best_rmse, 4),
        "数据点数": n_points,
    }


def _tune_hw(
    code: str,
    lifecycle_status: str,
    demand_pattern: str,
    material_data: pl.DataFrame,
    n_points: int,
) -> dict[str, Any]:
    """Holt-Winters 调参：先确定季节模式，再遍历参数空间。"""
    seasonal_mode: str = (
        _detect_seasonal_mode(material_data)
        if n_points >= MIN_DATA_POINTS_FOR_STL
        else "add"
    )

    best_rmse: float = float("inf")
    best_params: dict[str, float] = {}

    def _eval_hw(params: tuple[float, float, float]) -> tuple[float, float, float, float]:
        alpha, beta, gamma = params
        forecast_val, _ = _holt_winters_forecast_from_group(
            material_data, alpha, beta, gamma,
            seasonal_periods=DEFAULT_SEASONAL_PERIODS,
            seasonal_mode=seasonal_mode,
        )
        _, rmse, _ = _calculate_metrics_from_group(material_data, forecast_val)
        return (alpha, beta, gamma, rmse)

    param_combos: list[tuple[float, float, float]] = [
        (alpha, beta, gamma)
        for alpha in PARAM_GRID_STANDARD
        for beta in PARAM_GRID_STANDARD
        for gamma in PARAM_GRID_STANDARD
    ]

    with ThreadPoolExecutor(max_workers=min(8, len(param_combos))) as executor:
        futures = {executor.submit(_eval_hw, combo): combo for combo in param_combos}
        for future in as_completed(futures):
            alpha, beta, gamma, rmse = future.result()
            if rmse < best_rmse:
                best_rmse = rmse
                best_params = {"alpha": alpha, "beta": beta, "gamma": gamma}

    return {
        "物料编码": code,
        "生命周期状态": lifecycle_status,
        "需求模式": demand_pattern,
        "推荐方法": f"Holt-Winters({seasonal_mode})",
        "最优参数": best_params,
        "最优RMSE": round(best_rmse, 4),
        "数据点数": n_points,
    }


def _tune_tsb(
    code: str,
    lifecycle_status: str,
    demand_pattern: str,
    material_data: pl.DataFrame,
    n_points: int,
) -> dict[str, Any]:
    """
    TSB 调参：遍历 α ∈ [0.05, 0.40] 和 β ∈ [0.05, 0.40]，
    步长 0.05，共 64 个组合，使用 ThreadPoolExecutor 并行。
    """
    best_rmse: float = float("inf")
    best_params: dict[str, float] = {}

    def _eval_tsb(params: tuple[float, float]) -> tuple[float, float, float]:
        alpha, beta = params
        forecast_val, _ = _tsb_forecast_from_group(material_data, alpha, beta)
        _, rmse, _ = _calculate_metrics_from_group(material_data, forecast_val)
        return (alpha, beta, rmse)

    param_combos: list[tuple[float, float]] = [
        (alpha, beta) for alpha in PARAM_GRID_TSB for beta in PARAM_GRID_TSB
    ]

    with ThreadPoolExecutor(max_workers=min(8, len(param_combos))) as executor:
        futures = {executor.submit(_eval_tsb, combo): combo for combo in param_combos}
        for future in as_completed(futures):
            alpha, beta, rmse = future.result()
            if rmse < best_rmse:
                best_rmse = rmse
                best_params = {"alpha": alpha, "beta": beta}

    return {
        "物料编码": code,
        "生命周期状态": lifecycle_status,
        "需求模式": demand_pattern,
        "推荐方法": "TSB",
        "最优参数": best_params,
        "最优RMSE": round(best_rmse, 4),
        "数据点数": n_points,
    }


def _tune_imapa(
    code: str,
    lifecycle_status: str,
    demand_pattern: str,
    material_data: pl.DataFrame,
    n_points: int,
) -> dict[str, Any]:
    """
    IMAPA 调参：对可用的聚合窗口 [1, 2, 4]（及可选的 8, 16）进行选择，
    返回 RMSE 最小的窗口。不是连续参数搜索，而是离散窗口选择。
    """
    best_rmse: float = float("inf")
    best_window: int = 1
    best_forecast: float = 0.0

    windows: list[int] = [1, 2, 4]
    if DEFAULT_IMAPA_MAX_WINDOW >= 8:
        windows.append(8)
    if DEFAULT_IMAPA_MAX_WINDOW >= 16:
        windows.append(16)

    for window in windows:
        forecast_val, _ = _imapa_forecast_from_group(material_data, max_window=window)
        _, rmse, _ = _calculate_metrics_from_group(material_data, forecast_val)
        if rmse < best_rmse:
            best_rmse = rmse
            best_forecast = forecast_val
            best_window = window

    return {
        "物料编码": code,
        "生命周期状态": lifecycle_status,
        "需求模式": demand_pattern,
        "推荐方法": f"IMAPA(窗口={best_window}周)",
        "最优参数": {"窗口": best_window},
        "最优RMSE": round(best_rmse, 4),
        "数据点数": n_points,
    }


# ============================================================================
# 主入口
# ============================================================================

def main() -> None:
    """命令行入口，执行高性能并行调参。"""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="高性能并行调参 — 供应链智能分析平台"
    )
    parser.add_argument("--input", required=True,
                        help="extracted_weekly.parquet 文件路径")
    parser.add_argument("--output", required=True,
                        help="输出 optimal_params.json 文件路径")
    parser.add_argument("--method", type=str, default=DEFAULT_METHOD,
                        choices=["auto", "ses", "holt", "holt-winters", "tsb", "imapa"],
                        help="模型选择（默认 auto）")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"SKU 级并行数（默认 {DEFAULT_WORKERS}）")
    parser.add_argument("--force", action="store_true",
                        help="强制重新调参，忽略已有缓存")
    args: argparse.Namespace = parser.parse_args()

    input_path: Path = Path(args.input)
    output_path: Path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        print(f"错误: 输入文件不存在: {input_path}")
        sys.exit(1)

    # ── 加载数据 ──
    weekly_df: pl.DataFrame = pl.read_parquet(input_path)
    print(f"加载数据: {weekly_df.height} 行 x {weekly_df.width} 列")
    print(f"可用周数: {weekly_df['ISO_Week'].n_unique()}")

    # ── 检查缓存 ──
    if output_path.exists() and not args.force:
        with open(output_path, "r", encoding="utf-8") as fp:
            cached: dict[str, Any] = json.load(fp)
        cached_source: str = cached.get("data_source", "")
        if cached_source == str(input_path.resolve()):
            print(f"已有调参结果缓存: {output_path}")
            print(f"共 {len(cached.get('items', []))} 个物料")
            print("使用 --force 强制重新调参")
            return
        else:
            print("数据源已变更，将重新调参")

    # ── 预分组 ──
    print("预分组周度数据...")
    material_groups: dict[str, pl.DataFrame] = pregroup_by_material(weekly_df)

    # ── 聚合统计 ──
    agg_df: pl.DataFrame = weekly_df.group_by("物料编码").agg(
        pl.col("周出库量").mean().alias("周出库均值"),
        pl.col("周出库量").sum().alias("总出库量"),
        pl.col("周出库量").count().alias("数据点数"),
        pl.col("周出库量").last().alias("最近周出库量"),
    )

    # ── 准备调参任务 ──
    tasks: list[tuple[str, pl.DataFrame, int, float, float, str, str | None]] = []
    for row in agg_df.iter_rows(named=True):
        code: str = row["物料编码"]
        n_points: int = row["数据点数"]
        mean_val: float = row["周出库均值"] if row["周出库均值"] is not None else 0.0
        last_val: float = row["最近周出库量"] if row["最近周出库量"] is not None else 0.0
        material_data: pl.DataFrame = material_groups.get(code, pl.DataFrame())
        if material_data.height == 0:
            material_data = pl.DataFrame(schema={"周出库量": pl.Float32})

        lifecycle_status: str = "正常在售"
        if "生命周期状态" in material_data.columns and material_data.height > 0:
            lifecycle_status = material_data["生命周期状态"][0] or "正常在售"

        method_override: str | None = args.method if args.method != "auto" else None
        tasks.append((code, material_data, n_points, mean_val, last_val, lifecycle_status, method_override))

    # ── SKU 级并行调参 ──
    print(f"开始调参（{len(tasks)} 个物料，{args.workers} 个 Worker）...")
    results: list[dict[str, Any]] = []

    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(_tune_single_sku, *task): task[0]
            for task in tasks
        }
        completed: int = 0
        for future in as_completed(futures):
            code: str = futures[future]
            try:
                result: dict[str, Any] = future.result()
                results.append(result)
                completed += 1
                if completed % 100 == 0 or completed == len(tasks):
                    print(f"  进度: {completed}/{len(tasks)}")
            except Exception as e:
                print(f"  错误: 物料 {code} 调参失败: {e}")

    # ── 排序 ──
    results.sort(key=lambda x: x["物料编码"])

    # ── 输出 ──
    report: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(),
        "data_source": str(input_path.resolve()),
        "method": args.method,
        "total_items": len(results),
        "items": results,
    }

    with open(output_path, "w", encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2)
    print(f"调参完成! 最优参数已保存: {output_path}")
    print(f"共 {len(results)} 个物料")


if __name__ == "__main__":
    main()