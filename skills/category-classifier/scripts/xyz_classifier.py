"""
XYZ 分类脚本 (xyz_classifier.py)

供应链智能分析平台 — category-classifier 子 Skill

功能：基于周度出库数据的变异系数（CV）对物料进行 XYZ 分类。
     X 类：需求稳定，CV ≤ 0.3
     Y 类：需求有波动，0.3 < CV ≤ 0.8
     Z 类：需求不稳定，CV > 0.8
     生成 ABC-XYZ 组合矩阵与差异化管控策略。
     结合物料生命周期阶段优化分类。

符合 Polars 高性能数据处理原则体系：
    - 原生表达式
    - 向量化计算
    - 避免 Python 循环

用法:
    uv run xyz_classifier.py --input <extracted_weekly.parquet路径> \
      --output <输出JSON路径> [--append]

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
# 配置（周度校准阈值）
# ============================================================================

X_CLASS_THRESHOLD: float = 0.3
Y_CLASS_THRESHOLD: float = 0.8


# 管控策略矩阵
CONTROL_STRATEGY_MATRIX: dict[str, dict[str, str]] = {
    "AX": {"服务水平": "99%", "补货机制": "定期定量(JIT)", "盘点频率": "每日",
           "安全库存策略": "低安全库存", "备注": "与供应商建立VMI"},
    "AY": {"服务水平": "97%", "补货机制": "定期不定量", "盘点频率": "每周",
           "安全库存策略": "中等安全库存", "备注": "滚动预测共享"},
    "AZ": {"服务水平": "95%", "补货机制": "不定期不定量(按订单)", "盘点频率": "双周",
           "安全库存策略": "高安全库存或按单采购", "备注": "与客户确认需求"},
    "BX": {"服务水平": "97%", "补货机制": "定期定量(EOQ)", "盘点频率": "每周",
           "安全库存策略": "低安全库存", "备注": "EOQ补货"},
    "BY": {"服务水平": "95%", "补货机制": "定期不定量", "盘点频率": "双周",
           "安全库存策略": "中等安全库存", "备注": "设置安全库存缓冲"},
    "BZ": {"服务水平": "90%", "补货机制": "不定期不定量", "盘点频率": "月度",
           "安全库存策略": "高安全库存或按单采购", "备注": "定期评审"},
    "CX": {"服务水平": "95%", "补货机制": "不定期定量(双堆法)", "盘点频率": "月度",
           "安全库存策略": "低安全库存", "备注": "双堆法/两箱法"},
    "CY": {"服务水平": "90%", "补货机制": "不定期不定量", "盘点频率": "月度",
           "安全库存策略": "中等安全库存", "备注": "最小-最大库存法"},
    "CZ": {"服务水平": "85%", "补货机制": "不定期不定量", "盘点频率": "季度",
           "安全库存策略": "按单采购或淘汰", "备注": "评估是否保留"},
}


# ============================================================================
# XYZ 分类
# ============================================================================

def classify_xyz(
    weekly_df: pl.DataFrame,
    x_threshold: float = X_CLASS_THRESHOLD,
    y_threshold: float = Y_CLASS_THRESHOLD,
) -> dict[str, Any]:
    """
    基于周度需求波动（变异系数）进行 XYZ 分类。

    Parameters
    ----------
    weekly_df : pl.DataFrame
        周度数据（包含 物料编码、周出库量）。
    x_threshold: float
        X 类变异系数阈值。
    y_threshold: float
        Y 类变异系数阈值。

    Returns
    -------
    dict[str, Any]
        XYZ 分类报告。
    """
    agg_df: pl.DataFrame = weekly_df.group_by("物料编码").agg(
        pl.col("周出库量").std().alias("周出库标准差"),
        pl.col("周出库量").mean().alias("周出库均值"),
        pl.col("周出库量").sum().alias("总出库量"),
        pl.col("周出库量").count().alias("数据点数"),
    )

    agg_df = agg_df.with_columns(
        pl.when(pl.col("周出库均值") > 0)
        .then(pl.col("周出库标准差") / pl.col("周出库均值"))
        .otherwise(pl.lit(float("inf")))
        .alias("变异系数")
    )

    agg_df = agg_df.with_columns(
        pl.when(pl.col("数据点数") < 3)
        .then(pl.lit("数据不足"))
        .when(pl.col("变异系数") <= x_threshold)
        .then(pl.lit("X"))
        .when(pl.col("变异系数") <= y_threshold)
        .then(pl.lit("Y"))
        .otherwise(pl.lit("Z"))
        .alias("XYZ分类")
    )

    class_counts: pl.DataFrame = agg_df.group_by("XYZ分类").len().sort("XYZ分类")
    x_count: int = agg_df.filter(pl.col("XYZ分类") == "X").height
    y_count: int = agg_df.filter(pl.col("XYZ分类") == "Y").height
    z_count: int = agg_df.filter(pl.col("XYZ分类") == "Z").height
    insufficient_count: int = agg_df.filter(pl.col("XYZ分类") == "数据不足").height

    return {
        "x_class_threshold": X_CLASS_THRESHOLD,
        "y_class_threshold": Y_CLASS_THRESHOLD,
        "total_items": agg_df.height,
        "x_count": x_count,
        "y_count": y_count,
        "z_count": z_count,
        "insufficient_data_count": insufficient_count,
        "class_distribution": class_counts.rows(named=True),
        "item_details": agg_df.rows(named=True),
        "note": (
            "XYZ 分类基于周度出库量的变异系数（CV）。"
            "阈值配置：X类 CV≤0.3，Y类 0.3<CV≤0.8，Z类 CV>0.8。"
            "阈值可通过配置文件调整。"
        ),
    }


# ============================================================================
# 生命周期维度增强
# ============================================================================

def enhance_with_lifecycle(
    xyz_result: dict[str, Any],
    cost_report: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    结合物料生命周期阶段优化分类结果。

    如果提供了效率成本报告中的产品流分析，将生命周期状态
    融入 XYZ 分类建议中。例如：CZ + 衰退 → 建议淘汰。

    Parameters
    ----------
    xyz_result : dict[str, Any]
        XYZ 分类结果。
    cost_report : dict[str, Any] | None
        效率成本报告（包含 product_flow_analysis）。

    Returns
    -------
    dict[str, Any]
        增强后的 XYZ 分类结果。
    """
    if cost_report is None:
        return xyz_result

    product_flow: dict[str, Any] = cost_report.get("product_flow_analysis", {})
    lifecycle_map: dict[str, str] = {}
    for item in product_flow.get("item_details", []):
        code: str = item.get("物料编码", "")
        status_life: str = item.get("生命周期状态", "")
        if code and status_life:
            lifecycle_map[code] = status_life

    if not lifecycle_map:
        return xyz_result

    enhanced_details: list[dict[str, Any]] = []
    for item in xyz_result.get("item_details", []):
        code: str = item["物料编码"]
        xyz_class: str = item.get("XYZ分类", "Z")
        lifecycle: str = lifecycle_map.get(code, "")
        suggestion: str = ""

        if xyz_class == "Z" and lifecycle in ("消耗型（出库>入库）", "积累型（入库>出库）"):
            suggestion = "建议评估是否保留或转为按单采购"
        elif lifecycle == "未出库/可能呆滞":
            suggestion = "可能为呆滞物料，建议排查"
        elif lifecycle == "平衡型" and xyz_class == "Z":
            suggestion = "需求波动大但库存进出平衡，建议缩短评审周期"

        enhanced_item: dict[str, Any] = dict(item)
        enhanced_item["生命周期状态"] = lifecycle
        if suggestion:
            enhanced_item["分类优化建议"] = suggestion
        enhanced_details.append(enhanced_item)

    xyz_result["item_details"] = enhanced_details
    xyz_result["lifecycle_enhanced"] = True

    return xyz_result


# ============================================================================
# ABC-XYZ 组合矩阵
# ============================================================================

def build_abc_xyz_matrix(
    abc_result: dict[str, Any] | None,
    xyz_result: dict[str, Any],
) -> dict[str, Any]:
    """
    构建 ABC-XYZ 组合矩阵并生成管控策略。

    Parameters
    ----------
    abc_result : dict[str, Any] | None
        ABC 分类结果（可能为空）。
    xyz_result : dict[str, Any]
        XYZ 分类结果。

    Returns
    -------
    dict[str, Any]
        组合矩阵报告。
    """
    if abc_result is None:
        return {
            "status": "partial",
            "note": "ABC 分类数据缺失，无法生成完整组合矩阵。仅展示 XYZ 分类结果。",
            "matrix": {},
            "strategy_items": [],
        }

    abc_map: dict[str, str] = {}
    for item in abc_result.get("item_details", []):
        abc_map[item["物料编码"]] = item.get("ABC分类", "C")

    matrix: dict[str, int] = {}
    strategy_items: list[dict[str, Any]] = []

    for item in xyz_result.get("item_details", []):
        code: str = item["物料编码"]
        abc_class: str = abc_map.get(code, "C")
        xyz_class: str = item.get("XYZ分类", "Z")
        combo_key: str = f"{abc_class}{xyz_class}"
        matrix[combo_key] = matrix.get(combo_key, 0) + 1

        strategy: dict[str, str] = CONTROL_STRATEGY_MATRIX.get(
            combo_key,
            {"服务水平": "N/A", "补货机制": "未定义", "盘点频率": "N/A",
             "安全库存策略": "请联系管理员配置", "备注": ""}
        )

        strategy_items.append({
            "物料编码": code,
            "ABC分类": abc_class,
            "XYZ分类": xyz_class,
            "组合": combo_key,
            "服务水平": strategy["服务水平"],
            "补货机制": strategy["补货机制"],
            "盘点频率": strategy["盘点频率"],
            "安全库存策略": strategy["安全库存策略"],
            "备注": strategy["备注"],
            "生命周期状态": item.get("生命周期状态", ""),
            "分类优化建议": item.get("分类优化建议", ""),
        })

    return {
        "status": "completed",
        "matrix": matrix,
        "strategy_items": strategy_items,
        "total_combinations": len(matrix),
    }


# ============================================================================
# 主入口
# ============================================================================

def main() -> None:
    """命令行入口，执行 XYZ 分类并输出 JSON 报告。"""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="XYZ 分类（周度校准）与组合矩阵 — 供应链智能分析平台"
    )
    parser.add_argument("--input", required=True,
                        help="extracted_weekly.parquet 文件路径")
    parser.add_argument("--output", required=True,
                        help="输出 JSON 文件路径")
    parser.add_argument("--append", action="store_true",
                        help="追加模式：将结果追加到已有 JSON 文件")
    parser.add_argument("--cost-report", type=str, default=None,
                        help="efficiency_cost_report.json 文件路径（用于生命周期增强，可选）")
    parser.add_argument("--x-threshold", type=float, default=X_CLASS_THRESHOLD,
                        help=f"X 类变异系数上限（默认 {X_CLASS_THRESHOLD}）")
    parser.add_argument("--y-threshold", type=float, default=Y_CLASS_THRESHOLD,
                        help=f"Y 类变异系数上限（默认 {Y_CLASS_THRESHOLD}）")
    args: argparse.Namespace = parser.parse_args()

    input_path: Path = Path(args.input)
    output_path: Path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        print(f"错误: 输入文件不存在: {input_path}")
        return

    weekly_df: pl.DataFrame = pl.read_parquet(input_path)
    print(f"加载周度数据: {weekly_df.height} 行 x {weekly_df.width} 列")
    print(f"可用周数: {weekly_df['ISO_Week'].n_unique()}")

    # ── XYZ 分类 ──
    xyz_report: dict[str, Any] = classify_xyz(
        weekly_df, args.x_threshold, args.y_threshold
    )
    print(f"X 类: {xyz_report['x_count']} 个, "
          f"Y 类: {xyz_report['y_count']} 个, "
          f"Z 类: {xyz_report['z_count']} 个, "
          f"数据不足: {xyz_report['insufficient_data_count']} 个")

    # ── 生命周期增强 ──
    if args.cost_report:
        cost_path: Path = Path(args.cost_report)
        if cost_path.exists():
            with open(cost_path, "r", encoding="utf-8") as fp:
                cost_data: dict[str, Any] = json.load(fp)
            xyz_report = enhance_with_lifecycle(xyz_report, cost_data)
            print("已应用生命周期增强。")

    # ── 读取已有 ABC 结果 ──
    abc_report: dict[str, Any] | None = None
    if args.append and output_path.exists():
        with open(output_path, "r", encoding="utf-8") as fp:
            existing: dict[str, Any] = json.load(fp)
        abc_report = existing.get("abc_classification")

    # ── 组合矩阵 ──
    matrix_report: dict[str, Any] = build_abc_xyz_matrix(abc_report, xyz_report)
    if matrix_report["status"] == "completed":
        print(f"组合矩阵: {matrix_report['total_combinations']} 种组合")
        for combo_key, count in matrix_report.get("matrix", {}).items():
            print(f"  {combo_key}: {count} 个物料")

    # ── 输出 ──
    report: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "data_source": str(input_path),
        "xyz_classification": xyz_report,
        "abc_xyz_matrix": matrix_report,
    }

    if args.append and output_path.exists():
        with open(output_path, "r", encoding="utf-8") as fp:
            existing = json.load(fp)
        existing.update(report)
        report = existing

    with open(output_path, "w", encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2)
    print(f"分类结果已保存: {output_path}")


if __name__ == "__main__":
    main()