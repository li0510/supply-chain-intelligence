"""
供应商评估脚本 (supplier_evaluator.py)

供应链智能分析平台 — supplier-analyzer 子 Skill

功能：对供应商进行多维度评估。
     包括交货准时率分析、质量合格率分析、综合风险评分、
     供应商排名与分级、多供应商物料对比分析。

符合 Polars 高性能数据处理原则体系：
    - 原生表达式
    - 向量化计算
    - 避免 Python 循环

用法:
    uv run supplier_evaluator.py --input <Parquet文件路径> --output <输出JSON路径>

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

ON_TIME_THRESHOLD: float = 0.90
QUALITY_THRESHOLD: float = 0.95
DEPENDENCY_HIGH_THRESHOLD: float = 0.30

RISK_LEVEL_MAP: dict[str, str] = {
    "low": "低风险",
    "medium": "中风险",
    "high": "高风险",
}


# ============================================================================
# 字段检测
# ============================================================================

def detect_supplier_fields(df: pl.DataFrame) -> dict[str, bool]:
    """
    检测 DataFrame 中是否包含供应商相关字段。

    Parameters
    ----------
    df : pl.DataFrame
        结构化数据。

    Returns
    -------
    dict[str, bool]
        各字段是否存在。
    """
    columns: list[str] = df.columns
    supplier_fields: dict[str, list[str]] = {
        "supplier_name": ["供应商名称", "供应商", "supplier", "vendor"],
        "planned_date": ["计划交期", "计划日期", "planned_date", "plan_date"],
        "actual_date": ["实际交期", "实际日期", "actual_date", "delivery_date"],
        "qualified_qty": ["合格数量", "合格数", "qualified_qty"],
        "total_delivery_qty": ["交货总数量", "交货数量", "总数量", "total_qty", "delivery_qty"],
    }

    result: dict[str, bool] = {}
    for key, aliases in supplier_fields.items():
        result[key] = any(alias in columns for alias in aliases)

    return result


# ============================================================================
# 交期分析
# ============================================================================

def analyze_delivery_performance(
    df: pl.DataFrame,
    field_map: dict[str, bool],
    supplier_col: str | None,
) -> dict[str, Any] | None:
    """
    分析供应商交货准时率。

    Parameters
    ----------
    df : pl.DataFrame
        结构化数据。
    field_map : dict[str, bool]
        字段存在性映射。
    supplier_col : str | None
        供应商名称列的实际列名。

    Returns
    -------
    dict[str, Any] | None
        交期分析报告，字段不足时返回 None。
    """
    if not (field_map.get("planned_date") and field_map.get("actual_date") and supplier_col):
        return None

    planned_col: str = _find_column(df, ["计划交期", "计划日期", "planned_date", "plan_date"])
    actual_col: str = _find_column(df, ["实际交期", "实际日期", "actual_date", "delivery_date"])

    if planned_col is None or actual_col is None:
        return None

    # 计算交期偏差（天）
    delivery_df: pl.DataFrame = df.select([
        pl.col(supplier_col).alias("供应商"),
        pl.col("物料编码"),
        pl.col(planned_col).alias("计划交期"),
        pl.col(actual_col).alias("实际交期"),
    ])

    delivery_df = delivery_df.with_columns(
        (pl.col("实际交期").cast(pl.Date, strict=False)
         - pl.col("计划交期").cast(pl.Date, strict=False))
        .dt.total_days()
        .alias("交期偏差天数")
    )

    # 按供应商汇总
    supplier_delivery: pl.DataFrame = delivery_df.group_by("供应商").agg(
        pl.col("交期偏差天数").mean().alias("平均交期偏差"),
        pl.col("交期偏差天数").std().alias("交期偏差标准差"),
        pl.len().alias("交货次数"),
        ((pl.col("交期偏差天数") <= 0).sum() / pl.len()).alias("准时交货率"),
    ).sort("准时交货率")

    # 风险评估
    supplier_delivery = supplier_delivery.with_columns(
        pl.when(pl.col("准时交货率") >= ON_TIME_THRESHOLD)
        .then(pl.lit("low"))
        .when(pl.col("准时交货率") >= 0.70)
        .then(pl.lit("medium"))
        .otherwise(pl.lit("high"))
        .alias("交期风险等级")
    )

    high_risk_count: int = supplier_delivery.filter(pl.col("交期风险等级") == "high").height

    return {
        "on_time_threshold": ON_TIME_THRESHOLD,
        "total_suppliers": supplier_delivery.height,
        "high_risk_count": high_risk_count,
        "supplier_details": supplier_delivery.rows(named=True),
    }


# ============================================================================
# 质量分析
# ============================================================================

def analyze_quality_performance(
    df: pl.DataFrame,
    field_map: dict[str, bool],
    supplier_col: str | None,
) -> dict[str, Any] | None:
    """
    分析供应商质量合格率。

    Parameters
    ----------
    df : pl.DataFrame
        结构化数据。
    field_map : dict[str, bool]
        字段存在性映射。
    supplier_col : str | None
        供应商名称列的实际列名。

    Returns
    -------
    dict[str, Any] | None
        质量分析报告，字段不足时返回 None。
    """
    if not (field_map.get("qualified_qty") and field_map.get("total_delivery_qty") and supplier_col):
        return None

    qualified_col: str | None = _find_column(df, ["合格数量", "合格数", "qualified_qty"])
    total_col: str | None = _find_column(df, ["交货总数量", "交货数量", "总数量", "total_qty", "delivery_qty"])

    if qualified_col is None or total_col is None:
        return None

    # 计算合格率
    quality_df: pl.DataFrame = df.select([
        pl.col(supplier_col).alias("供应商"),
        pl.col("物料编码"),
        pl.col(qualified_col).cast(pl.Float64).alias("合格数量"),
        pl.col(total_col).cast(pl.Float64).alias("交货总数量"),
    ])

    # 按供应商汇总
    supplier_quality: pl.DataFrame = quality_df.group_by("供应商").agg(
        pl.col("合格数量").sum(),
        pl.col("交货总数量").sum(),
        pl.len().alias("交货批次"),
    )

    supplier_quality = supplier_quality.with_columns(
        pl.when(pl.col("交货总数量") > 0)
        .then(pl.col("合格数量") / pl.col("交货总数量"))
        .otherwise(pl.lit(0.0))
        .alias("质量合格率")
    ).sort("质量合格率")

    # 风险评估
    supplier_quality = supplier_quality.with_columns(
        pl.when(pl.col("质量合格率") >= QUALITY_THRESHOLD)
        .then(pl.lit("low"))
        .when(pl.col("质量合格率") >= 0.85)
        .then(pl.lit("medium"))
        .otherwise(pl.lit("high"))
        .alias("质量风险等级")
    )

    high_risk_count: int = supplier_quality.filter(pl.col("质量风险等级") == "high").height

    return {
        "quality_threshold": QUALITY_THRESHOLD,
        "total_suppliers": supplier_quality.height,
        "high_risk_count": high_risk_count,
        "supplier_details": supplier_quality.rows(named=True),
    }


# ============================================================================
# 依赖度分析
# ============================================================================

def analyze_dependency(
    df: pl.DataFrame,
    supplier_col: str | None,
) -> dict[str, Any] | None:
    """
    分析对供应商的依赖度。

    Parameters
    ----------
    df : pl.DataFrame
        结构化数据。
    supplier_col : str | None
        供应商名称列的实际列名。

    Returns
    -------
    dict[str, Any] | None
        依赖度分析报告，字段不足时返回 None。
    """
    if supplier_col is None:
        return None

    # 计算每个供应商负责的物料占比
    total_items: int = df["物料编码"].n_unique()
    if total_items == 0:
        return None

    dependency_df: pl.DataFrame = df.group_by(supplier_col).agg(
        pl.col("物料编码").n_unique().alias("负责物料数"),
        pl.col("出库数量").sum().alias("总出库量"),
    )

    dependency_df = dependency_df.with_columns(
        (pl.col("负责物料数") / pl.lit(total_items)).alias("物料占比"),
    ).sort("物料占比", descending=True)

    # 风险评估
    dependency_df = dependency_df.with_columns(
        pl.when(pl.col("物料占比") >= DEPENDENCY_HIGH_THRESHOLD)
        .then(pl.lit("high"))
        .when(pl.col("物料占比") >= 0.10)
        .then(pl.lit("medium"))
        .otherwise(pl.lit("low"))
        .alias("依赖度风险等级")
    )

    high_risk_count: int = dependency_df.filter(pl.col("依赖度风险等级") == "high").height

    return {
        "dependency_threshold": DEPENDENCY_HIGH_THRESHOLD,
        "total_suppliers": dependency_df.height,
        "total_items": total_items,
        "high_risk_count": high_risk_count,
        "supplier_details": dependency_df.rows(named=True),
    }


# ============================================================================
# 综合风险评估
# ============================================================================

def assess_comprehensive_risk(
    delivery_report: dict[str, Any] | None,
    quality_report: dict[str, Any] | None,
    dependency_report: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    综合三个维度的风险评分，生成最终评估。

    Parameters
    ----------
    delivery_report : dict[str, Any] | None
        交期分析报告。
    quality_report : dict[str, Any] | None
        质量分析报告。
    dependency_report : dict[str, Any] | None
        依赖度分析报告。

    Returns
    -------
    dict[str, Any]
        综合风险评估报告。
    """
    available_dimensions: list[str] = []
    if delivery_report is not None:
        available_dimensions.append("delivery")
    if quality_report is not None:
        available_dimensions.append("quality")
    if dependency_report is not None:
        available_dimensions.append("dependency")

    if not available_dimensions:
        return {
            "status": "skipped",
            "reason": "无可用分析维度，综合风险评估跳过。",
        }

    # 汇总风险等级
    risk_counts: dict[str, int] = {"low": 0, "medium": 0, "high": 0}

    for report in [delivery_report, quality_report, dependency_report]:
        if report is None:
            continue
        for detail in report.get("supplier_details", []):
            for level_key in ["交期风险等级", "质量风险等级", "依赖度风险等级"]:
                if level_key in detail and detail[level_key] in risk_counts:
                    risk_counts[detail[level_key]] += 1

    return {
        "status": "completed",
        "available_dimensions": available_dimensions,
        "risk_summary": risk_counts,
        "total_high_risk": risk_counts["high"],
        "total_medium_risk": risk_counts["medium"],
        "total_low_risk": risk_counts["low"],
        "recommendation": _generate_recommendation(risk_counts),
    }


def _generate_recommendation(risk_counts: dict[str, int]) -> str:
    """基于风险分布生成改进建议。"""
    high: int = risk_counts.get("high", 0)
    medium: int = risk_counts.get("medium", 0)

    if high > 0:
        return (f"存在 {high} 项高风险指标。建议立即审查高风险供应商，"
                "制定改进计划或寻找替代供应商。")
    elif medium > 0:
        return (f"存在 {medium} 项中风险指标。建议加强监控，"
                "与供应商沟通改进方案。")
    else:
        return "当前供应商整体表现良好，建议定期评审维持现状。"


# ============================================================================
# 辅助函数
# ============================================================================

def _find_column(df: pl.DataFrame, candidates: list[str]) -> str | None:
    """在 DataFrame 中查找第一个匹配的列名。"""
    for candidate in candidates:
        if candidate in df.columns:
            return candidate
    return None


# ============================================================================
# 主入口
# ============================================================================

def main() -> None:
    """命令行入口，执行供应商多维度评估并输出 JSON 报告。"""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="供应商分析 — 供应链智能分析平台"
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

    # ── 字段检测 ──
    field_map: dict[str, bool] = detect_supplier_fields(df)
    print(f"字段检测: {field_map}")

    supplier_col: str | None = _find_column(df, ["供应商名称", "供应商", "supplier", "vendor"])

    if supplier_col is None:
        print("警告: 未检测到供应商名称字段，无法进行供应商维度分析。")
        report: dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "data_source": str(input_path),
            "status": "skipped",
            "reason": "原始数据中未检测到供应商相关字段。供应商分析无法执行。",
            "required_fields": ["供应商名称（至少一项）",
                              "计划交期/实际交期（交期分析）",
                              "合格数量/交货总数量（质量分析）"],
        }
        with open(output_path, "w", encoding="utf-8") as fp:
            json.dump(report, fp, ensure_ascii=False, indent=2)
        print(f"报告已保存: {output_path}")
        return

    # ── 交期分析 ──
    delivery_report: dict[str, Any] | None = analyze_delivery_performance(
        df, field_map, supplier_col
    )
    if delivery_report:
        print(f"交期分析: {delivery_report['total_suppliers']} 个供应商, "
              f"高风险 {delivery_report['high_risk_count']} 个")
    else:
        print("交期分析: 字段不足，跳过")

    # ── 质量分析 ──
    quality_report: dict[str, Any] | None = analyze_quality_performance(
        df, field_map, supplier_col
    )
    if quality_report:
        print(f"质量分析: {quality_report['total_suppliers']} 个供应商, "
              f"高风险 {quality_report['high_risk_count']} 个")
    else:
        print("质量分析: 字段不足，跳过")

    # ── 依赖度分析 ──
    dependency_report: dict[str, Any] | None = analyze_dependency(df, supplier_col)
    if dependency_report:
        print(f"依赖度分析: {dependency_report['total_suppliers']} 个供应商, "
              f"高风险 {dependency_report['high_risk_count']} 个")
    else:
        print("依赖度分析: 字段不足，跳过")

    # ── 综合风险评估 ──
    comprehensive_report: dict[str, Any] = assess_comprehensive_risk(
        delivery_report, quality_report, dependency_report
    )
    print(f"综合评估: {comprehensive_report.get('recommendation', '')}")

    # ── 输出 ──
    report = {
        "timestamp": datetime.now().isoformat(),
        "data_source": str(input_path),
        "status": "completed",
        "delivery_analysis": delivery_report,
        "quality_analysis": quality_report,
        "dependency_analysis": dependency_report,
        "comprehensive_risk_assessment": comprehensive_report,
    }

    with open(output_path, "w", encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2)
    print(f"供应商评估报告已保存: {output_path}")


if __name__ == "__main__":
    main()