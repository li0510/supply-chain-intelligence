"""
ABC 分类脚本 (abc_classifier.py)

供应链智能分析平台 — category-classifier 子 Skill

功能：基于出库金额对物料进行 ABC 分类。
     A 类：累计出库金额占比前 70%
     B 类：累计出库金额占比 70%-90%
     C 类：累计出库金额占比 90%-100%

符合 Polars 高性能数据处理原则体系：
    - 原生表达式
    - 向量化计算
    - 避免 Python 循环

用法:
    uv run abc_classifier.py --input <Parquet文件路径> --output <输出JSON路径>

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

A_CLASS_THRESHOLD: float = 0.70
B_CLASS_THRESHOLD: float = 0.90


# ============================================================================
# ABC 分类
# ============================================================================

def classify_abc(df: pl.DataFrame) -> dict[str, Any]:
    """
    基于出库金额进行 ABC 分类。

    若数据中包含单价或出库金额字段，按金额分类；
    否则按出库数量分类。

    Parameters
    ----------
    df : pl.DataFrame
        包含物料编码、出库数量的结构化数据。

    Returns
    -------
    dict[str, Any]
        ABC 分类报告。
    """
    # 检查是否有金额相关字段
    has_amount: bool = "出库金额" in df.columns or "单价" in df.columns

    # 按物料编码聚合出库量
    agg_df: pl.DataFrame = df.group_by("物料编码").agg(
        pl.col("出库数量").sum().alias("总出库量"),
    )

    # 如果有金额字段，按金额排序；否则按数量排序
    if has_amount and "出库金额" in df.columns:
        amount_df: pl.DataFrame = df.group_by("物料编码").agg(
            pl.col("出库金额").sum().alias("总出库金额"),
        )
        agg_df = agg_df.join(amount_df, on="物料编码", how="left")
        agg_df = agg_df.sort("总出库金额", descending=True)
        agg_df = agg_df.with_columns(
            (pl.col("总出库金额") / pl.col("总出库金额").sum()).alias("金额占比"),
            (pl.col("总出库金额").cum_sum() / pl.col("总出库金额").sum()).alias("累计占比"),
        )
        classification_basis: str = "出库金额"
    else:
        agg_df = agg_df.sort("总出库量", descending=True)
        agg_df = agg_df.with_columns(
            (pl.col("总出库量") / pl.col("总出库量").sum()).alias("数量占比"),
            (pl.col("总出库量").cum_sum() / pl.col("总出库量").sum()).alias("累计占比"),
        )
        classification_basis = "出库数量"

    # 分类
    agg_df = agg_df.with_columns(
        pl.when(pl.col("累计占比") <= A_CLASS_THRESHOLD)
        .then(pl.lit("A"))
        .when(pl.col("累计占比") <= B_CLASS_THRESHOLD)
        .then(pl.lit("B"))
        .otherwise(pl.lit("C"))
        .alias("ABC分类")
    )

    # 统计
    class_counts: pl.DataFrame = agg_df.group_by("ABC分类").len().sort("ABC分类")
    a_count: int = agg_df.filter(pl.col("ABC分类") == "A").height
    b_count: int = agg_df.filter(pl.col("ABC分类") == "B").height
    c_count: int = agg_df.filter(pl.col("ABC分类") == "C").height

    return {
        "classification_basis": classification_basis,
        "a_class_threshold": A_CLASS_THRESHOLD,
        "b_class_threshold": B_CLASS_THRESHOLD,
        "total_items": agg_df.height,
        "a_count": a_count,
        "b_count": b_count,
        "c_count": c_count,
        "class_distribution": class_counts.rows(named=True),
        "item_details": agg_df.rows(named=True),
    }


# ============================================================================
# 主入口
# ============================================================================

def main() -> None:
    """命令行入口，执行 ABC 分类并输出 JSON 报告。"""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="ABC 分类 — 供应链智能分析平台"
    )
    parser.add_argument("--input", required=True, help="extracted_data.parquet 文件路径")
    parser.add_argument("--output", required=True, help="输出 JSON 文件路径")
    args: argparse.Namespace = parser.parse_args()

    input_path: Path = Path(args.input)
    output_path: Path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        print(f"错误: 输入文件不存在: {input_path}")
        return

    df: pl.DataFrame = pl.read_parquet(input_path)
    print(f"加载数据: {df.height} 行 x {df.width} 列")

    # ── ABC 分类 ──
    abc_report: dict[str, Any] = classify_abc(df)
    print(f"分类基准: {abc_report['classification_basis']}")
    print(f"A 类: {abc_report['a_count']} 个, "
          f"B 类: {abc_report['b_count']} 个, "
          f"C 类: {abc_report['c_count']} 个")

    # ── 输出 ──
    report: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "data_source": str(input_path),
        "abc_classification": abc_report,
    }

    with open(output_path, "w", encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2)
    print(f"ABC 分类报告已保存: {output_path}")


if __name__ == "__main__":
    main()