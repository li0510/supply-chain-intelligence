"""
供需匹配分析脚本 (supply_demand_matcher.py)

供应链智能分析平台 — supply-demand-matcher 子 Skill

功能：整合供给端与需求端数据，计算供需缺口，进行供应商产能匹配。
     供给端：现有库存 + 在途量（如有）
     需求端：生产计划/销售订单/预测需求（用户提供或历史估计）

符合 Polars 高性能数据处理原则体系：
    - 原生表达式
    - 向量化计算
    - 避免 Python 循环

用法:
    uv run supply_demand_matcher.py --supply <Parquet路径> --demand <需求文件路径> \
      [--supplier-report <JSON路径>] --output <输出JSON路径>

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

GAP_LEVEL_THRESHOLDS: dict[str, float] = {
    "充足": 0.0,     # 供给 >= 需求
    "偏紧": 0.10,    # 缺口 < 10%
    "短缺": 0.30,    # 缺口 >= 10%
}


# ============================================================================
# 需求数据加载
# ============================================================================

def load_demand_data(demand_source: str) -> pl.DataFrame:
    """
    从文件或 JSON 字符串加载需求数据。

    支持格式：
        - CSV（GBK 编码，需包含物料编码、需求量列）
        - Excel（需包含物料编码、需求量列）
        - JSON 字符串（格式: [{"物料编码":"xxx","需求量":xxx},...]）

    Parameters
    ----------
    demand_source : str
        需求数据文件路径或 JSON 字符串。

    Returns
    -------
    pl.DataFrame
        包含物料编码和需求量的 DataFrame。
    """
    demand_path: Path = Path(demand_source)

    # 尝试作为文件路径读取
    if demand_path.exists():
        suffix: str = demand_path.suffix.lower()
        if suffix == ".csv":
            demand_df: pl.DataFrame = pl.read_csv(
                demand_path, encoding="gbk", truncate_ragged_lines=True
            )
        elif suffix in (".xlsx", ".xls"):
            demand_df = pl.read_excel(demand_path)
        elif suffix == ".json":
            with open(demand_path, "r", encoding="utf-8") as fp:
                data: list[dict] = json.load(fp)
            demand_df = pl.DataFrame(data)
        else:
            raise ValueError(f"不支持的需求文件格式: {suffix}")
    else:
        # 尝试作为 JSON 字符串解析
        try:
            data = json.loads(demand_source)
            demand_df = pl.DataFrame(data)
        except json.JSONDecodeError:
            raise ValueError(f"需求数据无法解析: {demand_source}")

    # 标准化列名
    return _normalize_demand_columns(demand_df)


def _normalize_demand_columns(df: pl.DataFrame) -> pl.DataFrame:
    """标准化需求数据的列名。"""
    column_aliases: dict[str, list[str]] = {
        "物料编码": ["物料编码", "物料号", "编码", "item_code", "material_code"],
        "需求量": ["需求量", "需求数量", "demand_qty", "demand", "需求"],
    }

    rename_map: dict[str, str] = {}
    for target, aliases in column_aliases.items():
        for alias in aliases:
            if alias in df.columns:
                rename_map[alias] = target
                break

    if rename_map:
        df = df.rename(rename_map)

    # 确保必需列存在
    if "物料编码" not in df.columns:
        raise ValueError("需求数据中缺少物料编码列。请提供包含物料编码和需求量的数据。")
    if "需求量" not in df.columns:
        # 尝试找到数值列作为需求量
        numeric_cols: list[str] = [
            c for c in df.columns
            if c != "物料编码" and df[c].dtype in [pl.Float64, pl.Float32, pl.Int64, pl.Int32]
        ]
        if numeric_cols:
            df = df.rename({numeric_cols[0]: "需求量"})
            print(f"警告: 未找到需求量列，已自动使用 '{numeric_cols[0]}' 作为需求量列。")
        else:
            raise ValueError("需求数据中缺少需求量列。")

    # 类型转换
    df = df.with_columns(
        pl.col("物料编码").cast(pl.Utf8),
        pl.col("需求量").cast(pl.Float64),
    )

    return df.select(["物料编码", "需求量"])


# ============================================================================
# 供需匹配
# ============================================================================

def match_supply_demand(
    supply_df: pl.DataFrame,
    demand_df: pl.DataFrame,
) -> dict[str, Any]:
    """
    执行供需匹配计算。

    Parameters
    ----------
    supply_df : pl.DataFrame
        供给端数据（来自 extracted_data.parquet）。
    demand_df : pl.DataFrame
        需求端数据。

    Returns
    -------
    dict[str, Any]
        供需匹配报告。
    """
    # 供给端：汇总各物料的现有库存和结存
    supply_agg: pl.DataFrame = supply_df.group_by("物料编码").agg(
        pl.col("库存量").sum().alias("期初库存"),
        pl.col("结存数量").sum().alias("现有库存"),
    )

    # 检查是否有在途量字段
    if "在途量" in supply_df.columns:
        in_transit: pl.DataFrame = supply_df.group_by("物料编码").agg(
            pl.col("在途量").sum().alias("在途量")
        )
        supply_agg = supply_agg.join(in_transit, on="物料编码", how="left")
        supply_agg = supply_agg.with_columns(
            pl.col("在途量").fill_null(0.0)
        )
    else:
        supply_agg = supply_agg.with_columns(
            pl.lit(0.0).alias("在途量")
        )

    # 总供给 = 现有库存 + 在途量
    # 确保物料编码类型一致（supply_agg 中可能为 Categorical）
    supply_agg = supply_agg.with_columns(
        (pl.col("现有库存") + pl.col("在途量")).alias("总供给量"), 
        pl.col("物料编码").cast(pl.Utf8)
    )

    # 合并供需
    matched: pl.DataFrame = demand_df.join(
        supply_agg, on="物料编码", how="full", suffix="_供给"
    )

    # 填充缺失值
    matched = matched.with_columns(
        pl.col("需求量").fill_null(0.0),
        pl.col("总供给量").fill_null(0.0),
        pl.col("现有库存").fill_null(0.0),
        pl.col("在途量").fill_null(0.0),
    )

    # 计算缺口
    matched = matched.with_columns(
        (pl.col("需求量") - pl.col("总供给量")).alias("缺口量"),
    )

    # 缺口占比
    matched = matched.with_columns(
        pl.when(pl.col("需求量") > 0)
        .then(pl.col("缺口量") / pl.col("需求量"))
        .otherwise(pl.lit(0.0))
        .alias("缺口占比")
    )

    # 供需状态
    matched = matched.with_columns(
        pl.when(pl.col("缺口量") <= 0)
        .then(pl.lit("充足"))
        .when(pl.col("缺口占比") < GAP_LEVEL_THRESHOLDS["偏紧"])
        .then(pl.lit("偏紧"))
        .otherwise(pl.lit("短缺"))
        .alias("供需状态")
    )

    # 统计
    total_demand: float = float(matched["需求量"].sum())
    total_supply: float = float(matched["总供给量"].sum())
    total_gap: float = float(matched["缺口量"].sum())

    shortage_items: pl.DataFrame = matched.filter(pl.col("供需状态") == "短缺")
    surplus_items: pl.DataFrame = matched.filter(pl.col("缺口量") < 0)

    return {
        "total_demand": round(total_demand, 2),
        "total_supply": round(total_supply, 2),
        "total_gap": round(total_gap, 2),
        "gap_ratio": round(total_gap / total_demand, 4) if total_demand > 0 else 0.0,
        "total_items": matched.height,
        "shortage_count": shortage_items.height,
        "surplus_count": surplus_items.height,
        "shortage_items": shortage_items.sort("缺口量", descending=True).rows(named=True),
        "surplus_items": surplus_items.sort("缺口量").rows(named=True),
        "all_items": matched.sort("缺口量", descending=True).rows(named=True),
    }


# ============================================================================
# 供应商产能匹配
# ============================================================================

def match_supplier_capacity(
    gap_report: dict[str, Any],
    supplier_report: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """
    将短缺物料分配给可用供应商。

    Parameters
    ----------
    gap_report : dict[str, Any]
        供需匹配报告。
    supplier_report : dict[str, Any] | None
        供应商评估报告。

    Returns
    -------
    dict[str, Any] | None
        供应商分配建议，若无供应商数据则返回 None。
    """
    if supplier_report is None:
        return None

    # 从供应商报告中提取供应商信息
    delivery_details: list[dict] = supplier_report.get("delivery_analysis", {}).get(
        "supplier_details", []
    )
    if not delivery_details:
        return None

    # 获取短缺物料
    shortage_items: list[dict] = gap_report.get("shortage_items", [])
    if not shortage_items:
        return {"status": "no_shortage", "message": "无短缺物料，无需分配供应商。"}

    # 按准时交货率降序排列供应商（最优在前）
    sorted_suppliers: list[dict] = sorted(
        delivery_details,
        key=lambda x: x.get("准时交货率", 0),
        reverse=True,
    )

    allocations: list[dict[str, Any]] = []
    for item in shortage_items:
        gap_qty: float = item.get("缺口量", 0)
        if gap_qty <= 0:
            continue

        # 简单分配策略：分配给最优供应商
        best_supplier: dict = sorted_suppliers[0] if sorted_suppliers else {}
        allocations.append({
            "物料编码": item.get("物料编码"),
            "缺口量": gap_qty,
            "推荐供应商": best_supplier.get("供应商", "未知"),
            "供应商准时率": best_supplier.get("准时交货率", "N/A"),
            "交期风险等级": best_supplier.get("交期风险等级", "N/A"),
        })

    return {
        "status": "completed",
        "allocation_count": len(allocations),
        "allocations": allocations,
    }


# ============================================================================
# 主入口
# ============================================================================

def main() -> None:
    """命令行入口，执行供需匹配分析并输出 JSON 报告。"""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="供需匹配分析 — 供应链智能分析平台"
    )
    parser.add_argument("--supply", required=True,
                        help="供给端数据路径 (extracted_data.parquet)")
    parser.add_argument("--demand", required=True,
                        help="需求端数据路径或 JSON 字符串")
    parser.add_argument("--supplier-report", type=str, default=None,
                        help="供应商评估报告路径 (可选)")
    parser.add_argument("--output", required=True,
                        help="输出 JSON 文件路径")
    args: argparse.Namespace = parser.parse_args()

    supply_path: Path = Path(args.supply)
    output_path: Path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not supply_path.exists():
        print(f"错误: 供给端文件不存在: {supply_path}")
        return

    # ── 加载供给数据 ──
    supply_df: pl.DataFrame = pl.read_parquet(supply_path)
    print(f"供给数据: {supply_df.height} 行 x {supply_df.width} 列")

    # ── 加载需求数据 ──
    demand_df: pl.DataFrame = load_demand_data(args.demand)
    print(f"需求数据: {demand_df.height} 行, 总需求量={demand_df['需求量'].sum():.2f}")

    # ── 供需匹配 ──
    gap_report: dict[str, Any] = match_supply_demand(supply_df, demand_df)
    print(f"供需匹配: 总需求={gap_report['total_demand']:.2f}, "
          f"总供给={gap_report['total_supply']:.2f}, "
          f"缺口={gap_report['total_gap']:.2f}")
    print(f"短缺物料: {gap_report['shortage_count']} 个, "
          f"过剩物料: {gap_report['surplus_count']} 个")

    # ── 供应商产能匹配 ──
    supplier_report: dict[str, Any] | None = None
    if args.supplier_report:
        supplier_path: Path = Path(args.supplier_report)
        if supplier_path.exists():
            with open(supplier_path, "r", encoding="utf-8") as fp:
                supplier_report = json.load(fp)
            print("已加载供应商评估报告。")

    allocation_report: dict[str, Any] | None = match_supplier_capacity(
        gap_report, supplier_report
    )
    if allocation_report:
        print(f"供应商分配: {allocation_report.get('allocation_count', 0)} 条建议")
    else:
        print("供应商分配: 无可用供应商数据，跳过。")

    # ── 输出 ──
    report: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "supply_source": str(supply_path),
        "demand_source": args.demand,
        "supply_demand_matching": gap_report,
        "supplier_allocation": allocation_report,
    }

    with open(output_path, "w", encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2)
    print(f"供需匹配报告已保存: {output_path}")


if __name__ == "__main__":
    main()