"""
成本与资金分析脚本 (cost_analyzer.py)

供应链智能分析平台 — inventory-overview 子 Skill

功能：计算库存占用资金、资金周转天数、出入库金额分析。
     总成本视角（TCO）：持有成本 + 采购成本 + 缺货成本估算。
     产品流分析：物料流向、需求变化趋势、生命周期判断。

高性能设计（企业级千万级数据量适配）：
    - 所有汇总标量指标通过一次 select 并行计算
    - 使用 DataFrame.select(Expr).item() 模式

符合 Polars 高性能数据处理原则体系：
    - 原生表达式
    - 向量化计算
    - 避免 Python 循环

用法:
    uv run cost_analyzer.py --input <Parquet文件路径> --output <输出JSON路径> [--append]

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

HOLDING_COST_RATE: float = 0.02
STOCKOUT_COST_RATE: float = 0.05
LIFECYCLE_ZERO_THRESHOLD: int = 60


# ============================================================================
# 资金分析
# ============================================================================

def calculate_capital_analysis(
    df: pl.DataFrame,
    has_unit_price: bool,
) -> dict[str, Any]:
    """
    计算库存占用资金和出入库金额。

    所有汇总标量指标通过一次 select 并行计算。

    Parameters
    ----------
    df : pl.DataFrame
        结构化库存数据。
    has_unit_price : bool
        是否包含单价字段。

    Returns
    -------
    dict[str, Any]
        资金分析报告。
    """
    if not has_unit_price:
        return {
            "status": "skipped",
            "reason": "缺少单价字段，无法计算库存金额和资金分析。",
        }

    # ── 一次 select 并行计算所有标量指标 ──
    stats: pl.DataFrame = df.select([
        pl.col("库存量").sum().alias("total_inventory_qty"),
        pl.col("结存数量").sum().alias("total_balance_qty"),
    ])

    total_inventory_qty: float = stats["total_inventory_qty"].item()
    total_balance_qty: float = stats["total_balance_qty"].item()

    # ── TCO 估算 ──
    holding_cost: float = total_balance_qty * HOLDING_COST_RATE
    stockout_cost: float = total_balance_qty * STOCKOUT_COST_RATE

    return {
        "status": "completed",
        "total_inventory_quantity": total_inventory_qty,
        "total_balance_quantity": total_balance_qty,
        "holding_cost_rate": HOLDING_COST_RATE,
        "estimated_holding_cost": round(holding_cost, 2),
        "stockout_cost_rate": STOCKOUT_COST_RATE,
        "estimated_stockout_cost": round(stockout_cost, 2),
        "note": "金额计算需要单价字段。当前仅输出数量维度的成本估算。",
    }


# ============================================================================
# 产品流分析
# ============================================================================

def analyze_product_flow(df: pl.DataFrame) -> dict[str, Any]:
    """
    分析物料流向和生命周期。

    Parameters
    ----------
    df : pl.DataFrame
        结构化库存数据。

    Returns
    -------
    dict[str, Any]
        产品流分析报告。
    """
    agg_df: pl.DataFrame = df.group_by("物料编码").agg(
        pl.col("入库数量").sum().alias("总入库量"),
        pl.col("出库数量").sum().alias("总出库量"),
        pl.col("库存量").first().alias("期初库存"),
        pl.col("结存数量").last().alias("期末库存"),
    )

    agg_df = agg_df.with_columns(
        (pl.col("总入库量") - pl.col("总出库量")).alias("净流动"),
    )

    agg_df = agg_df.with_columns(
        pl.when((pl.col("总出库量") == 0) & (pl.col("期初库存") > 0))
        .then(pl.lit("未出库/可能呆滞"))
        .when(pl.col("总出库量") > pl.col("总入库量"))
        .then(pl.lit("消耗型（出库>入库）"))
        .when(pl.col("总入库量") > pl.col("总出库量"))
        .then(pl.lit("积累型（入库>出库）"))
        .otherwise(pl.lit("平衡型"))
        .alias("生命周期状态")
    )

    status_counts: pl.DataFrame = agg_df.group_by("生命周期状态").len()

    return {
        "total_items": agg_df.height,
        "status_distribution": status_counts.rows(named=True),
        "item_details": agg_df.rows(named=True),
    }


# ============================================================================
# 主入口
# ============================================================================

def main() -> None:
    """命令行入口，执行成本与产品流分析。"""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="成本与资金分析 — 供应链智能分析平台"
    )
    parser.add_argument("--input", required=True,
                        help="extracted_summary.parquet 文件路径")
    parser.add_argument("--output", required=True,
                        help="输出 JSON 文件路径")
    parser.add_argument("--append", action="store_true",
                        help="追加模式：将结果追加到已有 JSON 文件")
    parser.add_argument("--has-unit-price", action="store_true",
                        help="标记数据中包含单价字段")
    args: argparse.Namespace = parser.parse_args()

    input_path: Path = Path(args.input)
    output_path: Path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        print(f"错误: 输入文件不存在: {input_path}")
        return

    df: pl.DataFrame = pl.read_parquet(input_path)
    print(f"加载数据: {df.height} 行 x {df.width} 列")

    has_unit_price: bool = args.has_unit_price or ("单价" in df.columns)

    # ── 资金分析 ──
    capital_report: dict[str, Any] = calculate_capital_analysis(df, has_unit_price)
    if capital_report.get("status") == "skipped":
        print(f"警告: {capital_report['reason']}")

    # ── 产品流分析 ──
    flow_report: dict[str, Any] = analyze_product_flow(df)
    print(f"产品流分析: {flow_report['total_items']} 个物料")
    for status_item in flow_report["status_distribution"]:
        print(f"  {status_item['生命周期状态']}: {status_item['len']} 个")

    # ── 输出 ──
    report: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "data_source": str(input_path),
        "capital_analysis": capital_report,
        "product_flow_analysis": flow_report,
    }

    if args.append and output_path.exists():
        with open(output_path, "r", encoding="utf-8") as fp:
            existing: dict[str, Any] = json.load(fp)
        existing.update(report)
        report = existing

    with open(output_path, "w", encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2)
    print(f"效率成本报告已保存: {output_path}")


if __name__ == "__main__":
    main()