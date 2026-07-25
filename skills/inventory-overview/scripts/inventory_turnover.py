"""
库存周转分析脚本 (inventory_turnover.py)

供应链智能分析平台 — inventory-overview 子 Skill

功能：计算库存周转率、周转天数、库存持有天数（DOH）、
     识别呆滞库存、计算呆滞库存金额占比。
     周度版本：基于 13 周数据计算。

高性能设计（企业级千万级数据量适配）：
    - 所有汇总标量指标通过一次 select 并行计算，避免多次全表扫描
    - 使用 DataFrame.filter(Expr).select(Expr).item() 模式
    - 符合 P40（全局一致性）和 P47（最少重复造轮子）

公式（周度版本）：
    周转率 = 总出库量 / 平均周库存
    平均周库存 = Σ(每周结存) / 可用周数（精确版）
    周转天数 = 91 / 周转率（13周 × 7天）
    周转周数 = 13 / 周转率
    DOH = 当前结存 / 周均出库量 × 7

用法:
    uv run inventory_turnover.py --input <extracted_summary.parquet路径> \
      --weekly <extracted_weekly.parquet路径> \
      --output <输出JSON路径>

作者: Supply Chain Intelligence Team
版本: 0.2.5
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl


# ============================================================================
# 配置
# ============================================================================

PERIOD_WEEKS: int = 13
PERIOD_DAYS: int = 91
SLOW_MOVING_THRESHOLD_WEEKS: int = 12


# ============================================================================
# 周转计算
# ============================================================================

def calculate_turnover(
    summary_df: pl.DataFrame,
    weekly_df: pl.DataFrame,
) -> dict[str, Any]:
    """
    计算每个物料的库存周转率和周转天数（周度精确版）。

    平均库存使用每周结存的平均值（Σ(每周结存) / 可用周数），
    而非简化版的 (期初+期末)/2。

    所有汇总标量指标（平均周转率、呆滞物料数、总结存量、
    呆滞结存量）通过一次 select 并行计算，避免多次全表扫描。

    Parameters
    ----------
    summary_df : pl.DataFrame
        汇总数据（包含 物料编码、库存量、出库数量、结存数量）。
    weekly_df : pl.DataFrame
        周度数据（包含 物料编码、周结存、周出库量）。

    Returns
    -------
    dict[str, Any]
        周转分析报告。
    """
    # ── 按物料编码计算每周平均库存 ──
    weekly_avg_stock: pl.DataFrame = weekly_df.group_by("物料编码").agg(
        pl.col("周结存").mean().alias("平均周库存"),
        pl.col("周出库量").sum().alias("总周出库量"),
        pl.col("周出库量").mean().alias("周均出库量"),
    )

    # 确保物料编码类型一致（summary_df 中可能为 Categorical）
    summary_df = summary_df.with_columns(
        pl.col("物料编码").cast(pl.Utf8)
    )

    # ── 合并汇总数据 ──
    merged: pl.DataFrame = summary_df.join(
        weekly_avg_stock, on="物料编码", how="left"
    )

    # ── 周转率 = 总出库量 / 平均周库存 ──
    merged = merged.with_columns(
        pl.when(pl.col("平均周库存") > 0)
        .then(pl.col("总周出库量") / pl.col("平均周库存"))
        .otherwise(pl.lit(0.0))
        .alias("周转率")
    )

    # ── 周转天数 = 91 / 周转率 ──
    merged = merged.with_columns(
        pl.when(pl.col("周转率") > 0)
        .then(pl.lit(PERIOD_DAYS) / pl.col("周转率"))
        .otherwise(pl.lit(float("inf")))
        .alias("周转天数")
    )

    # ── 周转周数 = 13 / 周转率 ──
    merged = merged.with_columns(
        pl.when(pl.col("周转率") > 0)
        .then(pl.lit(PERIOD_WEEKS) / pl.col("周转率"))
        .otherwise(pl.lit(float("inf")))
        .alias("周转周数")
    )

    # ── DOH = 当前结存 / 周均出库量 × 7 ──
    merged = merged.with_columns(
        pl.when(pl.col("周均出库量") > 0)
        .then(pl.col("结存数量") / pl.col("周均出库量") * 7)
        .otherwise(pl.lit(float("inf")))
        .alias("库存持有天数(DOH)")
    )

    # ── 呆滞标记 ──
    merged = merged.with_columns(
        (pl.col("周转周数") > SLOW_MOVING_THRESHOLD_WEEKS).alias("是否呆滞")
    )

    # ── 排序 ──
    merged = merged.sort("周转率", descending=True)

    # ── 汇总统计：一次 select 并行计算所有标量指标 ──
    stats: pl.DataFrame = merged.select([
        # 平均周转率（仅对周转率 > 0 的物料取均值）
        pl.col("周转率")
        .filter(pl.col("周转率") > 0)
        .mean()
        .alias("avg_turnover_rate"),
        # 呆滞物料数量
        pl.col("是否呆滞").sum().alias("slow_moving_count"),
        # 总库存结存量
        pl.col("结存数量").sum().alias("total_balance"),
        # 呆滞物料的总结存量
        pl.when(pl.col("是否呆滞"))
        .then(pl.col("结存数量"))
        .otherwise(0.0)
        .sum()
        .alias("slow_moving_balance"),
    ])

    avg_turnover_rate: float = stats["avg_turnover_rate"].item()
    slow_moving_count: int = stats["slow_moving_count"].item()
    total_balance: float = stats["total_balance"].item()
    slow_moving_balance: float = stats["slow_moving_balance"].item()

    # ── 呆滞库存金额占比 ──
    slow_moving_ratio: float = (
        slow_moving_balance / total_balance * 100 if total_balance > 0 else 0.0
    )

    return {
        "period_weeks": PERIOD_WEEKS,
        "period_days": PERIOD_DAYS,
        "slow_moving_threshold_weeks": SLOW_MOVING_THRESHOLD_WEEKS,
        "average_turnover_rate": round(avg_turnover_rate, 4),
        "slow_moving_count": slow_moving_count,
        "total_items": merged.height,
        "slow_moving_balance": round(slow_moving_balance, 2),
        "slow_moving_ratio_pct": round(slow_moving_ratio, 2),
        "item_details": merged.rows(named=True),
    }


# ============================================================================
# 主入口
# ============================================================================

def main() -> None:
    """命令行入口，计算库存周转率并输出 JSON 报告。"""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="库存周转分析（周度精确版） — 供应链智能分析平台"
    )
    parser.add_argument("--input", required=True,
                        help="extracted_summary.parquet 文件路径")
    parser.add_argument("--weekly", required=True,
                        help="extracted_weekly.parquet 文件路径（用于计算平均周库存）")
    parser.add_argument("--output", required=True,
                        help="输出 JSON 文件路径")
    args: argparse.Namespace = parser.parse_args()

    summary_path: Path = Path(args.input)
    weekly_path: Path = Path(args.weekly)
    output_path: Path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    for path, name in [(summary_path, "汇总数据"), (weekly_path, "周度数据")]:
        if not path.exists():
            print(f"错误: {name}文件不存在: {path}")
            return

    summary_df: pl.DataFrame = pl.read_parquet(summary_path)
    weekly_df: pl.DataFrame = pl.read_parquet(weekly_path)
    print(f"汇总数据: {summary_df.height} 行")
    print(f"周度数据: {weekly_df.height} 行, {weekly_df['ISO_Week'].n_unique()} 周")

    # ── 周转分析 ──
    turnover_report: dict[str, Any] = calculate_turnover(summary_df, weekly_df)
    print(f"平均周转率: {turnover_report['average_turnover_rate']:.4f}")
    print(f"呆滞物料数: {turnover_report['slow_moving_count']} / {turnover_report['total_items']}")
    print(f"呆滞库存金额占比: {turnover_report['slow_moving_ratio_pct']:.2f}%")

    # ── 输出 ──
    report: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "data_source": str(summary_path),
        "weekly_source": str(weekly_path),
        "turnover_analysis": turnover_report,
    }

    with open(output_path, "w", encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2)
    print(f"周转分析报告已保存: {output_path}")


if __name__ == "__main__":
    main()