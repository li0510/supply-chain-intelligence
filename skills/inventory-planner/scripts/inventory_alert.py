"""
库存预警脚本 (inventory_alert.py)

供应链智能分析平台 — inventory-planner 子 Skill — 第三道防线

功能：基于库存计划参数生成分类差异化的预警清单。
     缺货预警：当前结存 < 安全库存
     积压预警：当前结存 > 最高库存
     采购提醒：当前结存 < 再订购点
     增强功能：
         - 预计缺货日期（可支撑周数）
         - 建议补货日期
         - 预警趋势（与上周对比 ↑↓→）
         - 在途订单关联（通过 supplier_report 读取）
         - 按供应商聚合
     新增功能：
         - 效期警告：剩余保质期天数 ≤ 30 天 → 立即促销/报废
         - 效期预警：剩余保质期天数 ≤ 保质期天数 × 0.3
         - 老品下市清仓：生命周期状态 = 老品下市 且 当前结存 > 0
         - 所有生命周期字段使用中文命名

高性能优化（企业级千万级数据量适配）：
     - 使用 partition_by 按物料编码预分组，O(n) 复杂度
     - 避免逐物料 filter 全表扫描

符合 Polars 高性能数据处理原则体系：
    - 原生表达式
    - 向量化计算
    - partition_by 替代逐物料 filter

用法:
    uv run inventory_alert.py --data <extracted_weekly.parquet路径> \
      --plan <inventory_plan.json路径> \
      --summary <extracted_summary.parquet路径> \
      --output <输出JSON路径> \
      [--supplier-report <supplier_report.json路径>]

作者: Supply Chain Intelligence Team
版本: 0.2.5
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl


# ============================================================================
# 配置
# ============================================================================

ALERT_COLOR_MAP: dict[str, str] = {
    "A": "红色",
    "B": "橙色",
    "C": "黄色",
}


# ============================================================================
# 数据预处理：按物料编码预分组
# ============================================================================

def pregroup_weekly_by_material(
    weekly_df: pl.DataFrame,
) -> dict[str, pl.DataFrame]:
    """
    将周度数据按物料编码预分组为 dict，避免后续逐物料 filter 全表扫描。

    使用 Polars 的 partition_by 方法，内部基于 Arrow 零拷贝分区，
    一次遍历完成全部物料的数据分离。每个分区是原 DataFrame 的视图，
    不复制数据，内存高效。

    Parameters
    ----------
    weekly_df : pl.DataFrame
        周度数据（包含 物料编码、ISO_Week、周出库量）。

    Returns
    -------
    dict[str, pl.DataFrame]
        物料编码 → 该物料的周度数据（已按 ISO_Week 排序）。
    """
    sorted_df: pl.DataFrame = weekly_df.sort(["物料编码", "ISO_Week"])

    partitioned: dict[tuple, pl.DataFrame] = sorted_df.partition_by(
        "物料编码", as_dict=True
    )

    result: dict[str, pl.DataFrame] = {}
    for key_tuple, material_data in partitioned.items():
        code: str = str(key_tuple[0])
        result[code] = material_data

    return result


# ============================================================================
# 预警生成
# ============================================================================

def generate_alerts(
    inventory_plan: dict[str, Any],
    weekly_df: pl.DataFrame | None = None,
    supplier_report: dict[str, Any] | None = None,
    lead_time_weeks: int = 1,
) -> dict[str, Any]:
    """
    基于库存计划结果生成分类差异化的预警清单。

    高性能版本：使用 partition_by 预分组周度数据，避免逐物料 filter。

    增强功能：
        - 预计缺货日期 = 当前结存 / 周均出库量（可支撑周数）
        - 建议补货日期 = 预计缺货日期 - 提前期
        - 预警趋势：通过比较上周出库量与本周出库量判断 ↑↓→
        - 在途订单关联（如有 supplier_report）
        - 按供应商聚合
        - 效期警告/预警（v2.0.0 新增）
        - 老品下市清仓预警（v2.0.0 新增）

    Parameters
    ----------
    inventory_plan : dict[str, Any]
        库存计划报告（来自 inventory_planning.py）。
    weekly_df : pl.DataFrame | None
        周度数据（用于计算预警趋势）。
    supplier_report : dict[str, Any] | None
        供应商评估报告（用于在途关联和按供应商聚合）。
    lead_time_weeks : int
        提前期（周），默认 1 周。

    Returns
    -------
    dict[str, Any]
        预警清单。
    """
    item_details: list[dict] = inventory_plan.get("item_details", [])

    # ── 预分组周度数据 ──
    material_groups: dict[str, pl.DataFrame] = {}
    if weekly_df is not None and weekly_df.height > 0:
        material_groups = pregroup_weekly_by_material(weekly_df)

    # ── 构建供应商映射 + 在途订单映射 ──
    supplier_map: dict[str, str] = {}
    in_transit_map: dict[str, float] = {}
    if supplier_report:
        for detail in supplier_report.get("delivery_analysis", {}).get("supplier_details", []):
            supplier_name: str = detail.get("供应商", "")
            if supplier_name:
                in_transit_qty: float = detail.get("在途量", 0.0)
                if in_transit_qty > 0:
                    in_transit_map[supplier_name] = in_transit_qty
        for detail in supplier_report.get("delivery_analysis", {}).get("supplier_details", []):
            supplier_name = detail.get("供应商", "")
            if supplier_name:
                supplier_map[detail.get("物料编码", "")] = supplier_name

    shortage_alerts: list[dict[str, Any]] = []
    overstock_alerts: list[dict[str, Any]] = []
    reorder_alerts: list[dict[str, Any]] = []
    expiry_alerts: list[dict[str, Any]] = []

    for item in item_details:
        code: str = item.get("物料编码", "")
        status: str = item.get("库存状态", "正常")
        combo: str = item.get("ABC-XYZ分类", "BX")
        abc_class: str = combo[0] if combo else "B"
        color: str = ALERT_COLOR_MAP.get(abc_class, "黄色")

        current_balance: float = item.get("当前结存", 0.0)
        safety_stock: float = item.get("安全库存", 0.0)
        reorder_point_val: float | None = item.get("再订购点(ROP)")
        max_stock: float = item.get("最高库存", 0.0)
        weekly_avg: float = item.get("周均出库量", 0.0)
        lifecycle_status: str = item.get("生命周期状态", "正常在售")

        # ── 在途量 ──
        supplier_name: str = supplier_map.get(code, "")
        in_transit: float = item.get("在途量", 0.0)
        if in_transit == 0.0 and supplier_name and supplier_name in in_transit_map:
            in_transit = in_transit_map[supplier_name]
        if in_transit == 0.0:
            in_transit = 0.0

        # ── 预计缺货日期 ──
        if weekly_avg > 0:
            net_balance: float = current_balance + in_transit
            weeks_until_stockout: float = net_balance / weekly_avg
            estimated_stockout_date: str = (
                datetime.now() + timedelta(weeks=weeks_until_stockout)
            ).strftime("%Y-%m-%d") if weeks_until_stockout > 0 else "已缺货"
        else:
            weeks_until_stockout = float("inf") if (current_balance + in_transit) > 0 else 0.0
            estimated_stockout_date = "无法估算（周均出库量为0）"

        # ── 建议补货日期 ──
        if weeks_until_stockout > 0 and weekly_avg > 0:
            suggested_order_date: str = (
                datetime.now() + timedelta(
                    weeks=max(0, weeks_until_stockout - lead_time_weeks)
                )
            ).strftime("%Y-%m-%d")
        else:
            suggested_order_date = "立即补货"

        # ── 预警趋势 ──
        material_data: pl.DataFrame = material_groups.get(code, pl.DataFrame())
        trend_arrow: str = _detect_alert_trend_from_group(material_data)

        # ── 效期预警（使用中文字段名）──
        shelf_life_days_val: float | None = item.get("保质期天数")
        remaining_shelf_life_days_val: float | None = item.get("剩余保质期天数")
        if (
            shelf_life_days_val is not None
            and shelf_life_days_val > 0
            and current_balance > 0
        ):
            if remaining_shelf_life_days_val is None:
                production_date_raw: str | None = item.get("生产日期")
                if production_date_raw:
                    try:
                        prod_date: datetime = datetime.fromisoformat(str(production_date_raw))
                        remaining_shelf_life_days_val = (
                            shelf_life_days_val
                            - (datetime.now() - prod_date).days
                        )
                    except ValueError:
                        pass

            if remaining_shelf_life_days_val is not None:
                if remaining_shelf_life_days_val <= 30:
                    expiry_alerts.append({
                        "物料编码": code,
                        "ABC分类": abc_class,
                        "预警类型": "效期警告",
                        "严重程度": "高",
                        "剩余天数": int(remaining_shelf_life_days_val),
                        "保质期总天数": int(shelf_life_days_val),
                        "当前结存": current_balance,
                        "建议动作": "立即促销/折价处理/报废",
                    })
                elif remaining_shelf_life_days_val <= shelf_life_days_val * 0.3:
                    expiry_alerts.append({
                        "物料编码": code,
                        "ABC分类": abc_class,
                        "预警类型": "效期预警",
                        "严重程度": "中",
                        "剩余天数": int(remaining_shelf_life_days_val),
                        "保质期总天数": int(shelf_life_days_val),
                        "当前结存": current_balance,
                        "建议动作": "优先消耗、调整补货计划",
                    })

        # ── 老品下市清仓预警 ──
        if lifecycle_status == "老品下市" and current_balance > 0:
            overstock_alerts.append({
                "物料编码": code,
                "ABC分类": abc_class,
                "组合分类": combo,
                "预警颜色": color,
                "预警类型": "老品下市清仓",
                "当前结存": current_balance,
                "周均出库量": weekly_avg,
                "预计缺货日期": estimated_stockout_date,
                "建议补货日期": "停止补货",
                "预警趋势": trend_arrow,
                "过剩量": current_balance,
                "建议处理量": current_balance,
                "处理建议": "启动清仓计划，停止补货",
            })
            continue

        # ── 已淘汰物料跳过预警 ──
        if lifecycle_status == "已淘汰":
            continue

        # ── 标准预警逻辑 ──
        alert_base: dict[str, Any] = {
            "物料编码": code,
            "ABC分类": abc_class,
            "组合分类": combo,
            "预警颜色": color,
            "生命周期状态": lifecycle_status,
            "当前结存": current_balance,
            "周均出库量": weekly_avg,
            "在途量": in_transit,
            "预计缺货日期": estimated_stockout_date,
            "建议补货日期": suggested_order_date,
            "预警趋势": trend_arrow,
        }

        if status == "缺货风险":
            shortage_alerts.append({
                **alert_base,
                "安全库存": safety_stock,
                "缺口量": round(safety_stock - current_balance, 2),
                "建议采购量": round(
                    max(0, (reorder_point_val or 0) - current_balance - in_transit), 2
                ),
                "紧急程度": (
                    "紧急" if abc_class == "A"
                    else ("重要" if abc_class == "B" else "一般")
                ),
            })
        elif status == "需补货":
            reorder_alerts.append({
                **alert_base,
                "再订购点": reorder_point_val,
                "建议采购量": round(
                    max(0, (reorder_point_val or 0) - current_balance + safety_stock - in_transit), 2
                ),
                "紧急程度": (
                    "本周" if abc_class == "A"
                    else ("本月" if abc_class == "B" else "季度")
                ),
            })
        elif status == "积压":
            overstock_alerts.append({
                **alert_base,
                "最高库存": max_stock,
                "过剩量": round(current_balance - max_stock, 2),
                "建议处理量": round(current_balance - max_stock, 2),
                "处理建议": (
                    "联系供应商退换" if abc_class == "A"
                    else ("促销消耗" if abc_class == "B" else "暂不处理")
                ),
            })

    # ── 按紧急程度排序 ──
    shortage_alerts.sort(
        key=lambda x: (0 if x["紧急程度"] == "紧急" else 1, -x["缺口量"])
    )
    reorder_alerts.sort(
        key=lambda x: (0 if x["紧急程度"] == "本周" else 1, -x["建议采购量"])
    )
    overstock_alerts.sort(key=lambda x: -x.get("过剩量", 0))
    expiry_alerts.sort(
        key=lambda x: (0 if x["严重程度"] == "高" else 1, x["剩余天数"])
    )

    total_alerts: int = (
        len(shortage_alerts)
        + len(reorder_alerts)
        + len(overstock_alerts)
        + len(expiry_alerts)
    )

    # ── 按供应商聚合 ──
    supplier_aggregation: list[dict[str, Any]] = _aggregate_by_supplier(
        shortage_alerts + reorder_alerts, supplier_map
    )

    return {
        "total_alerts": total_alerts,
        "shortage_count": len(shortage_alerts),
        "reorder_count": len(reorder_alerts),
        "overstock_count": len(overstock_alerts),
        "expiry_count": len(expiry_alerts),
        "shortage_alerts": shortage_alerts,
        "reorder_alerts": reorder_alerts,
        "overstock_alerts": overstock_alerts,
        "expiry_alerts": expiry_alerts,
        "top_urgent": shortage_alerts[:5] if shortage_alerts else reorder_alerts[:5],
        "supplier_aggregation": supplier_aggregation,
    }


def _detect_alert_trend_from_group(
    material_data: pl.DataFrame,
) -> str:
    """
    从已分组的物料时间序列数据中检测预警趋势。
    比较最近两周的出库量变化方向。

    ↑ = 出库增加（需求恶化）
    ↓ = 出库减少（需求改善）
    → = 持平或无法判断

    Parameters
    ----------
    material_data : pl.DataFrame
        单个物料的周度数据（已按 ISO_Week 排序）。

    Returns
    -------
    str
        ↑ / ↓ / →。
    """
    if material_data.height < 2:
        return "→"

    if "周出库量" not in material_data.columns:
        return "→"

    last_week_out: float = float(material_data["周出库量"].tail(1).item())
    prev_week_out: float = float(material_data["周出库量"].tail(2).head(1).item())

    if last_week_out > prev_week_out * 1.15:
        return "↑"
    elif last_week_out < prev_week_out * 0.85:
        return "↓"
    else:
        return "→"


def _aggregate_by_supplier(
    alerts: list[dict[str, Any]],
    supplier_map: dict[str, str],
) -> list[dict[str, Any]]:
    """
    按供应商聚合预警项，同一供应商的多物料预警合并为一个补货请求。

    Parameters
    ----------
    alerts : list[dict[str, Any]]
        预警项列表。
    supplier_map : dict[str, str]
        物料编码 → 供应商名称映射。

    Returns
    -------
    list[dict[str, Any]]
        按供应商聚合后的清单。
    """
    if not supplier_map:
        return []

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for alert in alerts:
        code: str = alert.get("物料编码", "")
        supplier: str = supplier_map.get(code, "未知供应商")
        grouped[supplier].append(alert)

    result: list[dict[str, Any]] = []
    for supplier, items in grouped.items():
        total_qty: float = sum(
            item.get("建议采购量", 0) for item in items
        )
        result.append({
            "供应商": supplier,
            "物料数": len(items),
            "总建议采购量": round(total_qty, 2),
            "物料清单": [item["物料编码"] for item in items],
            "最高紧急程度": max(
                (item.get("紧急程度", "一般") for item in items),
                key=lambda x: {
                    "紧急": 3, "重要": 2, "本周": 2, "本月": 1, "一般": 0, "季度": 0
                }.get(x, 0),
            ),
        })

    result.sort(key=lambda x: x["总建议采购量"], reverse=True)
    return result


# ============================================================================
# 主入口
# ============================================================================

def main() -> None:
    """命令行入口，生成预警清单并输出 JSON 报告。"""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="库存预警（增强版+高性能+效期预警） — 供应链智能分析平台"
    )
    parser.add_argument("--data", required=True,
                        help="extracted_weekly.parquet 文件路径")
    parser.add_argument("--plan", required=True,
                        help="inventory_plan.json 文件路径")
    parser.add_argument("--summary", type=str, default=None,
                        help="extracted_summary.parquet 文件路径（可选，用于获取生命周期字段）")
    parser.add_argument("--output", required=True,
                        help="输出 JSON 文件路径")
    parser.add_argument("--supplier-report", type=str, default=None,
                        help="supplier_report.json 文件路径（可选，用于在途关联和供应商聚合）")
    args: argparse.Namespace = parser.parse_args()

    data_path: Path = Path(args.data)
    plan_path: Path = Path(args.plan)
    output_path: Path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    for path, name in [(data_path, "数据"), (plan_path, "库存计划")]:
        if not path.exists():
            print(f"错误: {name}文件不存在: {path}")
            return

    # ── 加载库存计划 ──
    with open(plan_path, "r", encoding="utf-8") as fp:
        plan_data: dict[str, Any] = json.load(fp)
    inventory_plan: dict[str, Any] = plan_data.get("inventory_plan", {})
    print(f"库存计划: {inventory_plan.get('total_items', 0)} 个物料")

    # ── 加载周度数据 ──
    weekly_df: pl.DataFrame = pl.read_parquet(data_path)
    print(f"周度数据: {weekly_df.height} 行 x {weekly_df.width} 列")

    # ── 加载供应商报告 ──
    supplier_report: dict[str, Any] | None = None
    if args.supplier_report:
        supplier_path: Path = Path(args.supplier_report)
        if supplier_path.exists():
            with open(supplier_path, "r", encoding="utf-8") as fp:
                supplier_report = json.load(fp)
            print("已加载供应商评估报告（含在途订单信息）。")

    # ── 生成预警 ──
    alert_report: dict[str, Any] = generate_alerts(
        inventory_plan, weekly_df, supplier_report
    )
    print(f"预警总数: {alert_report['total_alerts']}")
    print(f"  缺货预警: {alert_report['shortage_count']} 项")
    print(f"  采购提醒: {alert_report['reorder_count']} 项")
    print(f"  积压预警: {alert_report['overstock_count']} 项")
    print(f"  效期预警: {alert_report['expiry_count']} 项")
    if alert_report.get("supplier_aggregation"):
        print(f"  供应商聚合: {len(alert_report['supplier_aggregation'])} 组")

    if alert_report["top_urgent"]:
        print("TOP紧急项:")
        for alert in alert_report["top_urgent"][:3]:
            print(f"  {alert['物料编码']}: {alert.get('紧急程度', '')}, "
                  f"建议采购 {alert.get('建议采购量', 0)}, "
                  f"预计缺货 {alert.get('预计缺货日期', 'N/A')}, "
                  f"在途 {alert.get('在途量', 0)}")

    if alert_report["expiry_alerts"]:
        print("效期预警项:")
        for alert in alert_report["expiry_alerts"][:3]:
            print(f"  {alert['物料编码']}: {alert['预警类型']}, "
                  f"剩余 {alert['剩余天数']} 天, "
                  f"建议: {alert['建议动作']}")

    # ── 输出 ──
    report: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "data_source": str(data_path),
        "plan_source": str(plan_path),
        "alert_list": alert_report,
    }

    with open(output_path, "w", encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2)
    print(f"预警清单已保存: {output_path}")


if __name__ == "__main__":
    main()