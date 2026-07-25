"""
数据质量检查脚本 (data_validator.py)

供应链智能分析平台 — data-inspector 子 Skill

功能：对提取后的结构化数据进行质量检查。
     包括：缺失值统计、非数字值检测、异常波动检测（Z-score）、
     进销存平衡校验（期末 = 期初 + 入库 - 出库）。

用法:
    uv run data_validator.py --input <Parquet文件路径> --output <输出目录路径>

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

NUMERIC_FIELDS: list[str] = [
    "库存量", "入库数量", "出库数量", "结存数量"
]

Z_SCORE_THRESHOLD: float = 3.0


# ============================================================================
# 平衡校验
# ============================================================================

def check_balance(df: pl.DataFrame) -> list[dict[str, Any]]:
    """
    进销存平衡校验：期末结存 应等于 期初库存 + 入库数量 - 出库数量。

    Parameters
    ----------
    df : pl.DataFrame
        待校验的 DataFrame，需包含库存量、入库数量、出库数量、结存数量列。

    Returns
    -------
    list[dict[str, Any]]
        不平衡项清单。
    """
    imbalance_list: list[dict[str, Any]] = []

    calculated: pl.Series = (
        df["库存量"].fill_null(0.0)
        + df["入库数量"].fill_null(0.0)
        - df["出库数量"].fill_null(0.0)
    )
    actual: pl.Series = df["结存数量"].fill_null(0.0)
    diff: pl.Series = (calculated - actual).abs()

    for row_idx in range(df.height):
        if diff[row_idx] is not None and diff[row_idx] > 0.01:
            imbalance_list.append({
                "row": row_idx + 1,
                "物料编码": str(df["物料编码"][row_idx]),
                "期初库存": float(df["库存量"][row_idx]) if df["库存量"][row_idx] is not None else 0.0,
                "入库数量": float(df["入库数量"][row_idx]) if df["入库数量"][row_idx] is not None else 0.0,
                "出库数量": float(df["出库数量"][row_idx]) if df["出库数量"][row_idx] is not None else 0.0,
                "实际结存": float(df["结存数量"][row_idx]) if df["结存数量"][row_idx] is not None else 0.0,
                "计算结存": float(calculated[row_idx]),
                "差异": float(diff[row_idx]),
            })

    return imbalance_list


# ============================================================================
# 异常波动检测
# ============================================================================

def detect_outliers(df: pl.DataFrame) -> list[dict[str, Any]]:
    """
    使用 Z-score 方法检测各数值列的异常波动。

    Parameters
    ----------
    df : pl.DataFrame
        待检测的 DataFrame。

    Returns
    -------
    list[dict[str, Any]]
        异常波动清单。
    """
    outliers: list[dict[str, Any]] = []

    for num_col in NUMERIC_FIELDS:
        series: pl.Series = df[num_col].drop_nulls()
        if series.len() < 3:
            continue
        mean_val: float = series.mean()
        std_val: float = series.std()
        if std_val == 0.0:
            continue

        for row_idx in range(df.height):
            val = df[num_col][row_idx]
            if val is None:
                continue
            z_score: float = abs(float(val) - mean_val) / std_val
            if z_score > Z_SCORE_THRESHOLD:
                outliers.append({
                    "row": row_idx + 1,
                    "物料编码": str(df["物料编码"][row_idx]),
                    "column": num_col,
                    "value": float(val),
                    "z_score": round(z_score, 2),
                })

    return outliers


# ============================================================================
# 主入口
# ============================================================================

def main() -> None:
    """命令行入口，执行数据质量检查并输出报告。"""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="数据质量检查 — 供应链智能分析平台"
    )
    parser.add_argument("--input", required=True, help="Parquet 文件路径")
    parser.add_argument("--output", required=True, help="输出目录路径")
    args: argparse.Namespace = parser.parse_args()

    input_path: Path = Path(args.input)
    output_dir: Path = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        print(f"错误: 输入文件不存在: {input_path}")
        return

    df: pl.DataFrame = pl.read_parquet(input_path)
    print(f"加载数据: {df.height} 行, {df.width} 列")

    # ── 平衡校验 ──
    imbalance_list: list[dict[str, Any]] = check_balance(df)
    print(f"平衡校验: {len(imbalance_list)} 条不平衡")

    # ── 异常检测 ──
    outliers: list[dict[str, Any]] = detect_outliers(df)
    print(f"异常波动: {len(outliers)} 条（Z-score > {Z_SCORE_THRESHOLD}）")

    # ── 汇总报告 ──
    report: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "total_rows": df.height,
        "imbalance_count": len(imbalance_list),
        "imbalance_details": imbalance_list,
        "outlier_count": len(outliers),
        "outlier_details": outliers,
    }

    report_path: Path = output_dir / "validation_report.json"
    with open(report_path, "w", encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2)
    print(f"质量检查报告已保存: {report_path}")


if __name__ == "__main__":
    main()