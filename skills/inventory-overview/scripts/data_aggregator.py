"""
数据聚合脚本 (data_aggregator.py)

供应链智能分析平台 — inventory-overview 子 Skill

功能：对提取后的结构化库存数据进行存量总览和流量总览分析。
     包括总库存量统计、按物料分类汇总、TOP-N/LAST-N 排名、
     入库/出库总量、净增/净减趋势。

高性能设计（企业级千万级数据量适配）：
    - 所有汇总标量指标通过一次 select 并行计算，避免多次全表扫描
    - 使用 DataFrame.select(Expr).item() 模式

符合 Polars 高性能数据处理原则体系：
    - 原生表达式
    - group_by + agg 向量化聚合

用法:
    uv run data_aggregator.py --input <Parquet文件路径> --output <输出JSON路径>

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

TOP_N: int = 10

NUMERIC_FIELDS: list[str] = [
    "库存量", "入库数量", "出库数量", "结存数量"
]


# ============================================================================
# 存量总览
# ============================================================================

def inventory_summary(df: pl.DataFrame) -> dict[str, Any]:
    """
    计算库存存量总览。

    所有汇总标量指标通过一次 select 并行计算，避免多次全表扫描。

    Parameters
    ----------
    df : pl.DataFrame
        包含物料编码和数值列的结构化数据。

    Returns
    -------
    dict[str, Any]
        存量总览报告。
    """
    # ── 一次 select 并行计算所有标量指标 ──
    stats: pl.DataFrame = df.select([
        pl.col("库存量").sum().alias("total_inventory"),
        pl.col("结存数量").sum().alias("total_balance"),
        pl.col("物料编码").n_unique().alias("unique_items"),
    ])

    total_inventory: float = stats["total_inventory"].item()
    total_balance: float = stats["total_balance"].item()
    unique_items: int = stats["unique_items"].item()

    # ── 按物料编码汇总 ──
    by_item: pl.DataFrame = df.group_by("物料编码").agg(
        pl.col("库存量").sum().alias("总库存量"),
        pl.col("结存数量").sum().alias("总结存量"),
    ).sort("总库存量", descending=True)

    # ── TOP-N 和 LAST-N ──
    top_n: list[dict[str, Any]] = by_item.head(TOP_N).rows(named=True)
    last_n: list[dict[str, Any]] = by_item.tail(TOP_N).rows(named=True)

    return {
        "total_inventory": total_inventory,
        "total_balance": total_balance,
        "unique_items": unique_items,
        "top_n_by_inventory": top_n,
        "last_n_by_inventory": last_n,
    }


# ============================================================================
# 流量总览
# ============================================================================

def flow_summary(df: pl.DataFrame) -> dict[str, Any]:
    """
    计算入库出库流量总览。

    所有汇总标量指标通过一次 select 并行计算。

    Parameters
    ----------
    df : pl.DataFrame
        包含数值列的结构化数据。

    Returns
    -------
    dict[str, Any]
        流量总览报告。
    """
    # ── 一次 select 并行计算所有标量指标 ──
    stats: pl.DataFrame = df.select([
        pl.col("入库数量").sum().alias("total_in"),
        pl.col("出库数量").sum().alias("total_out"),
    ])

    total_in: float = stats["total_in"].item()
    total_out: float = stats["total_out"].item()
    net_change: float = total_in - total_out

    # ── 按物料编码汇总出入库 ──
    by_item: pl.DataFrame = df.group_by("物料编码").agg(
        pl.col("入库数量").sum().alias("总入库量"),
        pl.col("出库数量").sum().alias("总出库量"),
    ).sort("总入库量", descending=True)

    top_in: list[dict[str, Any]] = by_item.head(TOP_N).rows(named=True)
    top_out: list[dict[str, Any]] = (
        by_item.sort("总出库量", descending=True).head(TOP_N).rows(named=True)
    )

    return {
        "total_in": total_in,
        "total_out": total_out,
        "net_change": net_change,
        "flow_direction": (
            "净增" if net_change > 0
            else ("净减" if net_change < 0 else "平衡")
        ),
        "top_n_by_in": top_in,
        "top_n_by_out": top_out,
    }


# ============================================================================
# 主入口
# ============================================================================

def main() -> None:
    """命令行入口，执行库存全景分析并输出 JSON 报告。"""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="库存全景分析 — 供应链智能分析平台"
    )
    parser.add_argument("--input", required=True,
                        help="extracted_summary.parquet 文件路径")
    parser.add_argument("--output", required=True,
                        help="输出 JSON 文件路径")
    args: argparse.Namespace = parser.parse_args()

    input_path: Path = Path(args.input)
    output_path: Path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        print(f"错误: 输入文件不存在: {input_path}")
        return

    df: pl.DataFrame = pl.read_parquet(input_path)
    print(f"加载数据: {df.height} 行 x {df.width} 列")

    # ── 存量总览 ──
    inv_summary: dict[str, Any] = inventory_summary(df)
    print(f"存量总览: 总库存={inv_summary['total_inventory']:.2f}, "
          f"总结存={inv_summary['total_balance']:.2f}, "
          f"物料数={inv_summary['unique_items']}")

    # ── 流量总览 ──
    flo_summary: dict[str, Any] = flow_summary(df)
    print(f"流量总览: 总入库={flo_summary['total_in']:.2f}, "
          f"总出库={flo_summary['total_out']:.2f}, "
          f"{flo_summary['flow_direction']}={flo_summary['net_change']:.2f}")

    # ── 汇总报告 ──
    report: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "data_source": str(input_path),
        "total_rows": df.height,
        "inventory_summary": inv_summary,
        "flow_summary": flo_summary,
    }

    with open(output_path, "w", encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2)
    print(f"库存全景报告已保存: {output_path}")


if __name__ == "__main__":
    main()