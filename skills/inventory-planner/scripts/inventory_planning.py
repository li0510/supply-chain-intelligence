"""
库存计划脚本 (inventory_planning.py)

供应链智能分析平台 — inventory-planner 子 Skill — 第二道防线

功能：基于周度出库数据和 ABC-XYZ 分类结果，计算分类差异化的：
      - 安全库存（Safety Stock，含保质期约束）
      - 再订购点（ROP，Reorder Point）
      - 最高库存（Max Stock，含保质期约束）
      - 经济订货批量（EOQ）
      - 补货策略（含生命周期状态判断）
      - 库存水位指标
      - TCO 总成本估算

新增功能：
    - 间歇性需求适配：对于 TSB/IMAPA 预测的物料，支持选择
      标准差计算方式（std_all vs std_nonzero），以适应间歇性数据特征。
    - 使用中文字段名读取物料主数据。

公式（周度版本）：
    安全库存 = Z × σ_week × √LT_weeks
    再订购点 = 周均需求 × LT_weeks + 安全库存
    最高库存 = ROP + EOQ（不定期补货）
            = 周均需求 × (补货周期 + LT_weeks) + 安全库存（定期补货）
    EOQ = √(2 × 年需求量 × 订货成本 / 持有成本率)
    年需求量 = 周均需求 × 52

用法:
    uv run inventory_planning.py --data <extracted_weekly.parquet路径> \
      --classification <abc_xyz_result.json路径> \
      --forecast <forecast_result.json路径> \
      --summary <extracted_summary.parquet路径> \
      --output <输出JSON路径> \
      [--lead-time-weeks <提前期>] [--ordering-cost <订货成本>] \
      [--holding-rate <持有成本率>] [--sigma-lt <提前期标准差>] \
      [--std-method <std_all|std_nonzero>]

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

SERVICE_LEVEL_Z_MAP: dict[float, float] = {
    0.999: 3.09,
    0.99: 2.33,
    0.97: 1.88,
    0.95: 1.65,
    0.90: 1.28,
    0.85: 1.04,
    0.80: 0.84,
}

CLASSIFICATION_SERVICE_LEVEL: dict[str, float] = {
    "AX": 0.99,
    "AY": 0.97,
    "AZ": 0.95,
    "BX": 0.97,
    "BY": 0.95,
    "BZ": 0.90,
    "CX": 0.95,
    "CY": 0.90,
    "CZ": 0.85,
}

REPLENISHMENT_POLICY_MATRIX: dict[str, dict[str, str | None]] = {
    "AX": {"type": "定期定量", "period_weeks": "1", "method": "JIT/VMI"},
    "AY": {"type": "定期不定量", "period_weeks": "1", "method": "滚动预测共享"},
    "AZ": {"type": "不定期不定量", "period_weeks": None, "method": "按订单采购(MTO)"},
    "BX": {"type": "定期定量", "period_weeks": "1", "method": "EOQ补货"},
    "BY": {"type": "定期不定量", "period_weeks": "2", "method": "安全库存缓冲"},
    "BZ": {"type": "不定期不定量", "period_weeks": None, "method": "定期评审+按单采购"},
    "CX": {"type": "不定期定量", "period_weeks": None, "method": "双堆法/两箱法"},
    "CY": {"type": "不定期不定量", "period_weeks": None, "method": "最小-最大库存法"},
    "CZ": {"type": "不定期不定量", "period_weeks": None, "method": "按单采购或淘汰"},
}

DEFAULT_LEAD_TIME_WEEKS: int = 1
DEFAULT_SERVICE_LEVEL: float = 0.95
DEFAULT_ORDERING_COST: float = 100.0
DEFAULT_HOLDING_RATE: float = 0.2
DEFAULT_STD_METHOD: str = "std_all"

SHELF_LIFE_SAFETY_STOCK_RATIO: float = 0.8
SHELF_LIFE_MAX_STOCK_RATIO: float = 0.6

# 间歇性需求预测方法标识（用于判断是否启用 std_nonzero）
INTERMITTENT_FORECAST_METHODS: set[str] = {"TSB", "IMAPA"}


# ============================================================================
# 库存参数计算
# ============================================================================

def calculate_inventory_params(
    weekly_df: pl.DataFrame,
    summary_df: pl.DataFrame,
    classification_map: dict[str, str],
    forecast_map: dict[str, float],
    forecast_methods: dict[str, str] | None = None,
    lead_time_weeks: int = DEFAULT_LEAD_TIME_WEEKS,
    ordering_cost: float = DEFAULT_ORDERING_COST,
    holding_rate: float = DEFAULT_HOLDING_RATE,
    sigma_lt: float | None = None,
    std_method: str = DEFAULT_STD_METHOD,
) -> dict[str, Any]:
    """
    计算每个物料的库存计划参数。

    v3.0.0 新增：
        - 间歇性需求适配：对于 TSB/IMAPA 预测的物料，
          支持选择标准差计算方式（std_all vs std_nonzero）。

    Parameters
    ----------
    weekly_df : pl.DataFrame
        周度数据（包含 物料编码、周出库量、周结存）。
    summary_df : pl.DataFrame
        汇总数据（包含 物料编码、生命周期字段）。
    classification_map : dict[str, str]
        物料编码 → ABC-XYZ 组合分类。
    forecast_map : dict[str, float]
        物料编码 → 预测周需求量。
    forecast_methods : dict[str, str] | None
        物料编码 → 预测方法（用于判断是否间歇性需求）。
    lead_time_weeks : int
        提前期（周），默认 1 周。
    ordering_cost : float
        每次订货成本，默认 100。
    holding_rate : float
        年持有成本率，默认 0.2（20%）。
    sigma_lt : float | None
        提前期标准差（周），用于扩展安全库存公式。
    std_method : str
        标准差计算方式："std_all"（全周期，含零值）或 "std_nonzero"（仅非零值）。
        默认 "std_all"。对于间歇性需求物料，建议使用 "std_nonzero"。

    Returns
    -------
    dict[str, Any]
        库存计划报告。
    """
    # ── 按物料编码聚合周度数据 ──
    agg_df: pl.DataFrame = weekly_df.group_by("物料编码").agg(
        pl.col("周出库量").mean().alias("周均出库量"),
        pl.col("周出库量").std().alias("周出库标准差_all"),
        pl.col("周出库量").last().alias("当前结存"),
    )

    # ── 计算仅非零值的标准差 ──
    nonzero_std_df: pl.DataFrame = (
        weekly_df.filter(pl.col("周出库量") > 0)
        .group_by("物料编码")
        .agg(pl.col("周出库量").std().alias("周出库标准差_nonzero"))
    )
    agg_df = agg_df.join(nonzero_std_df, on="物料编码", how="left")

    # ── 合并生命周期字段 ──
    lifecycle_cols_in_summary: list[str] = [
        col for col in [
            "生命周期状态", "保质期天数",
            "生产日期", "过期日期",
            "剩余保质期天数",
            "新品上市日期", "老品下市日期",
        ] if col in summary_df.columns
    ]
    if lifecycle_cols_in_summary:
        summary_lifecycle: pl.DataFrame = summary_df.select(
            ["物料编码"] + lifecycle_cols_in_summary
        )
        summary_lifecycle = summary_lifecycle.with_columns(
                pl.col("物料编码").cast(pl.Utf8)
        )        
        agg_df = agg_df.join(summary_lifecycle, on="物料编码", how="left")

    plan_results: list[dict[str, Any]] = []

    for row in agg_df.iter_rows(named=True):
        code: str = row["物料编码"]
        weekly_avg: float = row["周均出库量"] if row["周均出库量"] is not None else 0.0
        weekly_std_all: float = row["周出库标准差_all"] if row["周出库标准差_all"] is not None else 0.0
        weekly_std_nonzero: float = row["周出库标准差_nonzero"] if row["周出库标准差_nonzero"] is not None else 0.0
        current_balance: float = row["当前结存"] if row["当前结存"] is not None else 0.0

        # ── 确定使用的标准差 ──
        forecast_method: str = (
            forecast_methods.get(code, "") if forecast_methods else ""
        )
        is_intermittent: bool = any(
            m in forecast_method for m in INTERMITTENT_FORECAST_METHODS
        )
        if std_method == "std_nonzero" and is_intermittent and weekly_std_nonzero > 0:
            weekly_std: float = weekly_std_nonzero
            std_note: str = "std_nonzero（仅非零值）"
        else:
            weekly_std = weekly_std_all
            std_note = "std_all（全周期）"

        combo: str = classification_map.get(code, "BX")
        service_level: float = CLASSIFICATION_SERVICE_LEVEL.get(combo, DEFAULT_SERVICE_LEVEL)
        z_value: float = SERVICE_LEVEL_Z_MAP.get(service_level, 1.65)

        policy_info: dict[str, str | None] = REPLENISHMENT_POLICY_MATRIX.get(
            combo, {"type": "不定期不定量", "period_weeks": None, "method": "默认策略"}
        )

        # ── 安全库存 ──
        if sigma_lt is not None and sigma_lt > 0 and weekly_avg > 0:
            safety_stock: float = z_value * (
                lead_time_weeks * (weekly_std ** 2)
                + (weekly_avg ** 2) * (sigma_lt ** 2)
            ) ** 0.5
            ss_formula: str = f"Z×√(LT×σ²_d+ D²×σ²_LT), σ_LT={sigma_lt}周"
        else:
            safety_stock = z_value * weekly_std * (lead_time_weeks ** 0.5)
            ss_formula = f"Z×σ×√LT, σ_LT=未提供"

        forecast_demand_val: float = forecast_map.get(code, weekly_avg)

        # ── 保质期约束 ──
        shelf_life_days_val: float | None = row.get("保质期天数")
        if (
            shelf_life_days_val is not None
            and shelf_life_days_val > 0
            and weekly_avg > 0
        ):
            remaining_shelf_life_weeks: float = shelf_life_days_val / 7.0
            max_safety_stock: float = (
                weekly_avg * remaining_shelf_life_weeks * SHELF_LIFE_SAFETY_STOCK_RATIO
            )
            if safety_stock > max_safety_stock:
                ss_formula += f", 保质期上限={max_safety_stock:.2f}"
                safety_stock = max_safety_stock

        # ── ROP ──
        if policy_info["type"] == "不定期不定量" and combo in ("AZ", "BZ", "CZ"):
            reorder_point: float | None = None
            future_rop: list[dict[str, float]] = []
            rop_note: str = "不适用（MTO/按订单采购）"
        else:
            reorder_point = forecast_demand_val * lead_time_weeks + safety_stock
            future_rop = [
                {
                    "周偏移": i + 1,
                    "预测周需求量": round(forecast_demand_val, 2),
                    "预测ROP": round(
                        forecast_demand_val * lead_time_weeks + safety_stock, 2
                    ),
                }
                for i in range(4)
            ]
            rop_note = ""

        # ── EOQ ──
        annual_demand: float = forecast_demand_val * 52
        if annual_demand > 0 and holding_rate > 0:
            eoq: float = (2 * annual_demand * ordering_cost / holding_rate) ** 0.5
        else:
            eoq = 0.0

        # ── 最高库存 ──
        if policy_info["type"] in ("定期定量", "定期不定量"):
            period_weeks: int = (
                int(policy_info["period_weeks"])
                if policy_info["period_weeks"]
                else 1
            )
            max_stock: float = (
                forecast_demand_val * (period_weeks + lead_time_weeks) + safety_stock
            )
        else:
            max_stock = (reorder_point or 0.0) + eoq

        # ── 保质期约束（最高库存上限）──
        if (
            shelf_life_days_val is not None
            and shelf_life_days_val > 0
            and weekly_avg > 0
        ):
            remaining_shelf_life_weeks = shelf_life_days_val / 7.0
            max_allowed_stock: float = (
                weekly_avg * remaining_shelf_life_weeks * SHELF_LIFE_MAX_STOCK_RATIO
            )
            if max_stock > max_allowed_stock:
                max_stock = max_allowed_stock

        # ── 库存水位 ──
        stock_level_pct: float = (
            current_balance / max_stock * 100 if max_stock > 0 else 0.0
        )

        # ── 生命周期状态判断 ──
        lifecycle_status: str = row.get("生命周期状态") or "正常在售"
        if lifecycle_status == "新品上市":
            safety_stock = max(safety_stock, weekly_avg * 2)
            service_level = 0.99
            z_value = SERVICE_LEVEL_Z_MAP[0.99]
            policy_info = {
                "type": "定期不定量",
                "period_weeks": "1",
                "method": "新品上市",
            }
            reorder_point = forecast_demand_val * lead_time_weeks + safety_stock
            rop_note = ""
        elif lifecycle_status == "老品下市":
            safety_stock = 0.0
            reorder_point = None
            rop_note = "停止补货-清仓中"
            max_stock = current_balance
            policy_info = {
                "type": "不定期不定量",
                "period_weeks": None,
                "method": "停止补货-清仓中",
            }
        elif lifecycle_status == "已淘汰":
            safety_stock = 0.0
            reorder_point = None
            rop_note = "已淘汰"
            policy_info = {
                "type": "不定期不定量",
                "period_weeks": None,
                "method": "已淘汰",
            }

        # ── 库存状态 ──
        if reorder_point is not None and current_balance <= safety_stock:
            status: str = "缺货风险"
        elif reorder_point is not None and current_balance <= reorder_point:
            status = "需补货"
        elif current_balance >= max_stock:
            status = "积压"
        else:
            status = "正常"

        # ── TCO 成本估算 ──
        avg_stock: float = (safety_stock + max_stock) / 2
        holding_cost: float = avg_stock * holding_rate
        order_count: float = annual_demand / eoq if eoq > 0 else 0.0
        total_ordering_cost: float = order_count * ordering_cost
        total_cost: float = holding_cost + total_ordering_cost

        plan_results.append({
            "物料编码": code,
            "生命周期状态": lifecycle_status,
            "ABC-XYZ分类": combo,
            "服务水平": service_level,
            "Z值": round(z_value, 2),
            "周均出库量": round(weekly_avg, 2),
            "周出库标准差": round(weekly_std, 2),
            "标准差计算方式": std_note,
            "安全库存": round(safety_stock, 2),
            "安全库存公式": ss_formula,
            "再订购点(ROP)": round(reorder_point, 2) if reorder_point is not None else None,
            "ROP说明": rop_note,
            "动态ROP(未来4周)": future_rop,
            "经济订货批量(EOQ)": round(eoq, 2),
            "最高库存": round(max_stock, 2),
            "当前结存": round(current_balance, 2),
            "库存水位(%)": round(stock_level_pct, 1),
            "库存状态": status,
            "提前期(周)": lead_time_weeks,
            "提前期标准差(σ_LT)": sigma_lt if sigma_lt else "未提供",
            "补货策略类型": policy_info["type"],
            "补货周期(周)": policy_info["period_weeks"],
            "补货方法": policy_info["method"],
            "年持有成本(估算)": round(holding_cost, 2),
            "年订货成本(估算)": round(total_ordering_cost, 2),
            "年总库存成本(估算)": round(total_cost, 2),
        })

    risk_count: int = sum(1 for r in plan_results if r["库存状态"] == "缺货风险")
    reorder_count: int = sum(1 for r in plan_results if r["库存状态"] == "需补货")
    overstock_count: int = sum(1 for r in plan_results if r["库存状态"] == "积压")
    normal_count: int = sum(1 for r in plan_results if r["库存状态"] == "正常")

    return {
        "lead_time_weeks": lead_time_weeks,
        "ordering_cost": ordering_cost,
        "holding_rate": holding_rate,
        "std_method": std_method,
        "total_items": len(plan_results),
        "risk_count": risk_count,
        "reorder_count": reorder_count,
        "overstock_count": overstock_count,
        "normal_count": normal_count,
        "item_details": plan_results,
    }


# ============================================================================
# 分类映射与预测映射加载
# ============================================================================

def load_classification_map(classification_path: Path) -> dict[str, str]:
    """
    从 ABC-XYZ 分类结果 JSON 中提取物料编码→组合分类映射。

    Parameters
    ----------
    classification_path : Path
        abc_xyz_result.json 文件路径。

    Returns
    -------
    dict[str, str]
        物料编码 → ABC-XYZ 组合分类。
    """
    with open(classification_path, "r", encoding="utf-8") as fp:
        data: dict[str, Any] = json.load(fp)

    mapping: dict[str, str] = {}
    matrix_data: dict[str, Any] = data.get("abc_xyz_matrix", {})
    strategy_items: list[dict] = matrix_data.get("strategy_items", [])

    if strategy_items:
        for item in strategy_items:
            code: str = item.get("物料编码", "")
            combo: str = item.get("组合", "BX")
            if code:
                mapping[code] = combo
    else:
        xyz_data: dict[str, Any] = data.get("xyz_classification", {})
        abc_data: dict[str, Any] = data.get("abc_classification", {})

        xyz_map: dict[str, str] = {}
        for detail in xyz_data.get("item_details", []):
            xyz_map[detail["物料编码"]] = detail.get("XYZ分类", "Y")

        abc_map: dict[str, str] = {}
        for detail in abc_data.get("item_details", []):
            abc_map[detail["物料编码"]] = detail.get("ABC分类", "B")

        all_codes: set[str] = set(xyz_map.keys()) | set(abc_map.keys())
        for code in all_codes:
            mapping[code] = f"{abc_map.get(code, 'B')}{xyz_map.get(code, 'Y')}"

    return mapping


def load_forecast_map(forecast_path: Path) -> dict[str, float]:
    """
    从需求预测结果 JSON 中提取物料编码→预测周需求量映射。

    Parameters
    ----------
    forecast_path : Path
        forecast_result.json 文件路径。

    Returns
    -------
    dict[str, float]
        物料编码 → 预测周需求量。
    """
    with open(forecast_path, "r", encoding="utf-8") as fp:
        data: dict[str, Any] = json.load(fp)

    mapping: dict[str, float] = {}
    forecast_data: dict[str, Any] = data.get("demand_forecast", {})
    for detail in forecast_data.get("item_details", []):
        code: str = detail.get("物料编码", "")
        forecast_val: float = detail.get("预测周需求量", 0.0)
        if code:
            mapping[code] = forecast_val

    return mapping


def load_forecast_methods(forecast_path: Path) -> dict[str, str]:
    """
    从需求预测结果 JSON 中提取物料编码→预测方法映射。

    用于判断该物料是否使用了间歇性需求预测方法（TSB/IMAPA）。

    Parameters
    ----------
    forecast_path : Path
        forecast_result.json 文件路径。

    Returns
    -------
    dict[str, str]
        物料编码 → 预测方法。
    """
    with open(forecast_path, "r", encoding="utf-8") as fp:
        data: dict[str, Any] = json.load(fp)

    mapping: dict[str, str] = {}
    forecast_data: dict[str, Any] = data.get("demand_forecast", {})
    for detail in forecast_data.get("item_details", []):
        code: str = detail.get("物料编码", "")
        method: str = detail.get("预测方法", "")
        if code:
            mapping[code] = method

    return mapping


# ============================================================================
# 主入口
# ============================================================================

def main() -> None:
    """命令行入口，计算库存计划参数并输出 JSON 报告。"""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="库存计划（周度+分类差异化+生命周期+保质期约束+间歇性适配） — 供应链智能分析平台"
    )
    parser.add_argument("--data", required=True,
                        help="extracted_weekly.parquet 文件路径")
    parser.add_argument("--summary", required=True,
                        help="extracted_summary.parquet 文件路径（包含生命周期字段）")
    parser.add_argument("--classification", required=True,
                        help="abc_xyz_result.json 文件路径")
    parser.add_argument("--forecast", required=True,
                        help="forecast_result.json 文件路径")
    parser.add_argument("--output", required=True,
                        help="输出 JSON 文件路径")
    parser.add_argument("--lead-time-weeks", type=int,
                        default=DEFAULT_LEAD_TIME_WEEKS,
                        help="提前期（周）")
    parser.add_argument("--ordering-cost", type=float,
                        default=DEFAULT_ORDERING_COST,
                        help="每次订货成本")
    parser.add_argument("--holding-rate", type=float,
                        default=DEFAULT_HOLDING_RATE,
                        help="年持有成本率")
    parser.add_argument("--sigma-lt", type=float, default=None,
                        help="提前期标准差（周），用于扩展安全库存公式")
    parser.add_argument("--std-method", type=str, default=DEFAULT_STD_METHOD,
                        choices=["std_all", "std_nonzero"],
                        help="标准差计算方式：std_all（全周期），std_nonzero（仅非零值，适用于间歇性需求）")
    args: argparse.Namespace = parser.parse_args()

    data_path: Path = Path(args.data)
    summary_path: Path = Path(args.summary)
    classification_path: Path = Path(args.classification)
    forecast_path: Path = Path(args.forecast)
    output_path: Path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    for path, name in [
        (data_path, "周度数据"),
        (summary_path, "汇总数据"),
        (classification_path, "分类"),
        (forecast_path, "预测"),
    ]:
        if not path.exists():
            print(f"错误: {name}文件不存在: {path}")
            return

    weekly_df: pl.DataFrame = pl.read_parquet(data_path)
    summary_df: pl.DataFrame = pl.read_parquet(summary_path)
    print(f"周度数据: {weekly_df.height} 行 x {weekly_df.width} 列")
    print(f"汇总数据: {summary_df.height} 行 x {summary_df.width} 列")

    classification_map: dict[str, str] = load_classification_map(classification_path)
    print(f"分类映射: {len(classification_map)} 个物料")

    forecast_map: dict[str, float] = load_forecast_map(forecast_path)
    print(f"预测映射: {len(forecast_map)} 个物料")

    forecast_methods: dict[str, str] = load_forecast_methods(forecast_path)
    intermittent_count: int = sum(
        1 for m in forecast_methods.values()
        if any(im in m for im in INTERMITTENT_FORECAST_METHODS)
    )
    if intermittent_count > 0:
        print(f"间歇性需求物料: {intermittent_count} 个（预测方法包含 TSB/IMAPA）")

    plan_report: dict[str, Any] = calculate_inventory_params(
        weekly_df, summary_df, classification_map, forecast_map,
        forecast_methods=forecast_methods,
        lead_time_weeks=args.lead_time_weeks,
        ordering_cost=args.ordering_cost,
        holding_rate=args.holding_rate,
        sigma_lt=args.sigma_lt,
        std_method=args.std_method,
    )
    print(f"库存计划: {plan_report['total_items']} 个物料")
    print(f"  标准差计算方式: {plan_report['std_method']}")
    print(f"  缺货风险: {plan_report['risk_count']} 个")
    print(f"  需补货: {plan_report['reorder_count']} 个")
    print(f"  积压: {plan_report['overstock_count']} 个")
    print(f"  正常: {plan_report['normal_count']} 个")

    report: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "data_source": str(data_path),
        "summary_source": str(summary_path),
        "classification_source": str(classification_path),
        "forecast_source": str(forecast_path),
        "inventory_plan": plan_report,
    }

    with open(output_path, "w", encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2)
    print(f"库存计划报告已保存: {output_path}")


if __name__ == "__main__":
    main()