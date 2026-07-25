"""
采购计划生成脚本 (purchase_planner.py)

供应链智能分析平台 — purchase-advisor 子 Skill

功能：基于预警清单生成采购优先级排序、建议采购量计算、
     供应商分配建议。
     增强功能：
         - EOQ 校验（建议采购量 < EOQ 时提示合并采购）
         - MOQ 校验（最小起订量）
         - 多供应商分配比例（战略物料 70:30）
         - 采购预算估算

公式（周度版本）：
    采购量 = 需求量 + 安全库存 - 当前结存 - 在途量

用法:
    uv run purchase_planner.py --alerts <alert_list.json路径> \
      [--supplier-report <supplier_report.json路径>] \
      [--supply-demand <supply_demand_gap.json路径>] \
      [--inventory-plan <inventory_plan.json路径>] \
      [--moq <最小起订量>] \
      --output <输出JSON路径>

作者: Supply Chain Intelligence Team
版本: 0.2.5
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


# ============================================================================
# 配置
# ============================================================================

DEFAULT_LEAD_TIME_WEEKS: int = 1
DEFAULT_MOQ: float = 0.0
URGENT_WEEKS: int = 0
WEEKLY_WEEKS: int = 1
MONTHLY_WEEKS: int = 4


# ============================================================================
# 采购量计算
# ============================================================================

def calculate_purchase_qty(
    alert_item: dict[str, Any],
    supply_demand_map: dict[str, float] | None = None,
    eoq: float | None = None,
    moq: float = DEFAULT_MOQ,
) -> tuple[float, list[str]]:
    """
    计算建议采购量，并进行 EOQ/MOQ 校验。

    Parameters
    ----------
    alert_item : dict[str, Any]
        预警项数据。
    supply_demand_map : dict[str, float] | None
        物料编码 → 需求量的映射。
    eoq : float | None
        经济订货批量。
    moq : float
        最小起订量。

    Returns
    -------
    tuple[float, list[str]]
        (建议采购量, 校验提示列表)。
    """
    code: str = alert_item.get("物料编码", "")
    current_balance: float = alert_item.get("当前结存", 0.0)
    safety_stock: float = alert_item.get("安全库存", 0.0)
    in_transit: float = alert_item.get("在途量", 0.0)

    if supply_demand_map and code in supply_demand_map:
        demand_qty: float = supply_demand_map[code]
    else:
        demand_qty = alert_item.get("建议采购量", 0.0)

    purchase_qty: float = demand_qty + safety_stock - current_balance - in_transit
    purchase_qty = max(0.0, purchase_qty)

    warnings: list[str] = []

    # EOQ 校验
    if eoq is not None and eoq > 0 and 0 < purchase_qty < eoq * 0.5:
        warnings.append(
            f"建议采购量({purchase_qty:.2f})远小于EOQ({eoq:.2f})，"
            "建议合并采购以达到经济订货批量。"
        )

    # MOQ 校验
    if moq > 0 and purchase_qty < moq:
        warnings.append(
            f"建议采购量({purchase_qty:.2f})低于最小起订量({moq:.2f})，"
            f"已自动调整至 MOQ。"
        )
        purchase_qty = moq

    return round(purchase_qty, 2), warnings


# ============================================================================
# 建议下单日期
# ============================================================================

def suggest_order_date(urgency: str) -> str:
    """基于紧急程度建议下单日期（周度版本）。"""
    today: datetime = datetime.now()
    urgency_map: dict[str, int] = {
        "紧急": URGENT_WEEKS,
        "本周": WEEKLY_WEEKS,
        "本月": MONTHLY_WEEKS,
        "季度": max(1, 12 - (today.timetuple().tm_yday // 30) % 3),
    }
    weeks_offset: int = urgency_map.get(urgency, DEFAULT_LEAD_TIME_WEEKS)
    order_date: datetime = today + timedelta(weeks=max(0, weeks_offset))
    return order_date.strftime("%Y-%m-%d")


# ============================================================================
# 供应商分配（多供应商策略）
# ============================================================================

def assign_suppliers(
    material_code: str,
    purchase_qty: float,
    abc_class: str,
    supplier_report: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """
    为采购需求分配供应商，战略物料（A类）按 70:30 分配主/备供应商。

    Parameters
    ----------
    material_code : str
        物料编码。
    purchase_qty : float
        采购量。
    abc_class : str
        ABC 分类。
    supplier_report : dict[str, Any] | None
        供应商评估报告。

    Returns
    -------
    list[dict[str, Any]]
        供应商分配列表。
    """
    if supplier_report is None:
        return [{"物料编码": material_code, "采购量": purchase_qty, "推荐供应商": "未指定"}]

    delivery_details: list[dict] = (
        supplier_report.get("delivery_analysis", {}).get("supplier_details", [])
    )
    if not delivery_details:
        return [{"物料编码": material_code, "采购量": purchase_qty, "推荐供应商": "未指定"}]

    sorted_suppliers: list[dict] = sorted(
        delivery_details,
        key=lambda x: x.get("准时交货率", 0),
        reverse=True,
    )

    if abc_class == "A" and len(sorted_suppliers) >= 2:
        primary: dict = sorted_suppliers[0]
        secondary: dict = sorted_suppliers[1]
        return [
            {
                "物料编码": material_code,
                "采购量": round(purchase_qty * 0.7, 2),
                "推荐供应商": primary.get("供应商", "未指定"),
                "分配比例": "70%",
                "供应商准时率": primary.get("准时交货率", "N/A"),
                "角色": "主供应商",
            },
            {
                "物料编码": material_code,
                "采购量": round(purchase_qty * 0.3, 2),
                "推荐供应商": secondary.get("供应商", "未指定"),
                "分配比例": "30%",
                "供应商准时率": secondary.get("准时交货率", "N/A"),
                "角色": "备选供应商",
            },
        ]
    else:
        best: dict = sorted_suppliers[0] if sorted_suppliers else {}
        return [
            {
                "物料编码": material_code,
                "采购量": purchase_qty,
                "推荐供应商": best.get("供应商", "未指定"),
                "分配比例": "100%",
                "供应商准时率": best.get("准时交货率", "N/A"),
                "角色": "单一供应商",
            },
        ]


# ============================================================================
# 采购计划生成
# ============================================================================

def generate_purchase_plan(
    alert_list: dict[str, Any],
    supplier_report: dict[str, Any] | None = None,
    supply_demand_data: dict[str, Any] | None = None,
    inventory_plan: dict[str, Any] | None = None,
    moq: float = DEFAULT_MOQ,
) -> dict[str, Any]:
    """
    生成完整的采购行动计划。

    Parameters
    ----------
    alert_list : dict[str, Any]
        预警清单数据。
    supplier_report : dict[str, Any] | None
        供应商评估报告。
    supply_demand_data : dict[str, Any] | None
        供需匹配数据。
    inventory_plan : dict[str, Any] | None
        库存计划数据（用于获取 EOQ）。
    moq : float
        最小起订量。

    Returns
    -------
    dict[str, Any]
        采购行动计划。
    """
    alerts_data: dict[str, Any] = alert_list.get("alert_list", alert_list)

    # 构建供需映射
    supply_demand_map: dict[str, float] = {}
    if supply_demand_data:
        matching: dict[str, Any] = supply_demand_data.get("supply_demand_matching", {})
        for item in matching.get("all_items", []):
            code: str = item.get("物料编码", "")
            demand: float = item.get("需求量", 0.0)
            if code:
                supply_demand_map[code] = demand

    # 构建 EOQ 映射
    eoq_map: dict[str, float] = {}
    if inventory_plan:
        for item in inventory_plan.get("item_details", []):
            code: str = item.get("物料编码", "")
            eoq_val: float = item.get("经济订货批量(EOQ)", 0.0)
            if code and eoq_val:
                eoq_map[code] = eoq_val

    purchase_items: list[dict[str, Any]] = []
    all_warnings: list[str] = []

    # ── 缺货预警 → 采购项 ──
    for alert in alerts_data.get("shortage_alerts", []):
        code: str = alert.get("物料编码", "")
        eoq: float | None = eoq_map.get(code)
        qty, warnings = calculate_purchase_qty(alert, supply_demand_map, eoq, moq)
        all_warnings.extend(warnings)
        supplier_info: list[dict[str, Any]] = assign_suppliers(
            code, qty, alert.get("ABC分类", "B"), supplier_report
        )
        order_date: str = suggest_order_date(alert.get("紧急程度", "紧急"))

        purchase_items.append({
            "物料编码": code,
            "ABC分类": alert.get("ABC分类", "B"),
            "预警类型": "缺货",
            "紧急程度": alert.get("紧急程度", "紧急"),
            "当前结存": alert.get("当前结存", 0.0),
            "安全库存": alert.get("安全库存", 0.0),
            "缺口量": alert.get("缺口量", 0.0),
            "建议采购量": qty,
            "建议下单日期": order_date,
            "供应商分配": supplier_info,
            "校验提示": warnings,
            "优先级分数": _priority_score(alert.get("ABC分类", "B"), "缺货"),
        })

    # ── 采购提醒 → 采购项 ──
    for alert in alerts_data.get("reorder_alerts", []):
        code = alert.get("物料编码", "")
        eoq = eoq_map.get(code)
        qty, warnings = calculate_purchase_qty(alert, supply_demand_map, eoq, moq)
        all_warnings.extend(warnings)
        supplier_info = assign_suppliers(
            code, qty, alert.get("ABC分类", "B"), supplier_report
        )
        order_date = suggest_order_date(alert.get("紧急程度", "本月"))

        purchase_items.append({
            "物料编码": code,
            "ABC分类": alert.get("ABC分类", "B"),
            "预警类型": "补货",
            "紧急程度": alert.get("紧急程度", "本月"),
            "当前结存": alert.get("当前结存", 0.0),
            "再订购点": alert.get("再订购点", 0.0),
            "建议采购量": qty,
            "建议下单日期": order_date,
            "供应商分配": supplier_info,
            "校验提示": warnings,
            "优先级分数": _priority_score(alert.get("ABC分类", "B"), "补货"),
        })

    # ── 按优先级排序 ──
    purchase_items.sort(key=lambda x: x["优先级分数"], reverse=True)

    # ── 统计 ──
    urgent_count: int = sum(1 for p in purchase_items if p["紧急程度"] == "紧急")
    weekly_count: int = sum(1 for p in purchase_items if p["紧急程度"] == "本周")
    monthly_count: int = sum(1 for p in purchase_items if p["紧急程度"] == "本月")
    total_qty: float = sum(p["建议采购量"] for p in purchase_items)

    # ── 采购预算估算 ──
    budget_estimation: dict[str, Any] = {
        "total_purchase_qty": round(total_qty, 2),
        "note": "缺少单价数据，无法估算采购金额。如需金额估算，请提供物料单价。",
    }

    return {
        "total_items": len(purchase_items),
        "urgent_count": urgent_count,
        "weekly_count": weekly_count,
        "monthly_count": monthly_count,
        "total_purchase_qty": round(total_qty, 2),
        "purchase_items": purchase_items,
        "top_urgent": [p for p in purchase_items if p["紧急程度"] == "紧急"][:10],
        "budget_estimation": budget_estimation,
        "eoq_moq_warnings": all_warnings[:10],
    }


def _priority_score(abc_class: str, alert_type: str) -> int:
    """计算采购优先级分数。"""
    class_score: dict[str, int] = {"A": 100, "B": 50, "C": 10}
    type_score: dict[str, int] = {"缺货": 200, "补货": 100}
    return class_score.get(abc_class, 0) + type_score.get(alert_type, 0)


# ============================================================================
# 主入口
# ============================================================================

def main() -> None:
    """命令行入口，生成采购计划并输出 JSON 报告。"""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="采购计划生成（增强版） — 供应链智能分析平台"
    )
    parser.add_argument("--alerts", required=True,
                        help="alert_list.json 文件路径")
    parser.add_argument("--supplier-report", type=str, default=None,
                        help="supplier_report.json 文件路径（可选）")
    parser.add_argument("--supply-demand", type=str, default=None,
                        help="supply_demand_gap.json 文件路径（可选）")
    parser.add_argument("--inventory-plan", type=str, default=None,
                        help="inventory_plan.json 文件路径（可选，用于获取 EOQ）")
    parser.add_argument("--moq", type=float, default=DEFAULT_MOQ,
                        help="最小起订量")
    parser.add_argument("--output", required=True,
                        help="输出 JSON 文件路径")
    args: argparse.Namespace = parser.parse_args()

    alerts_path: Path = Path(args.alerts)
    output_path: Path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not alerts_path.exists():
        print(f"错误: 预警清单文件不存在: {alerts_path}")
        return

    # ── 加载预警清单 ──
    with open(alerts_path, "r", encoding="utf-8") as fp:
        alert_list: dict[str, Any] = json.load(fp)
    alerts_data: dict[str, Any] = alert_list.get("alert_list", alert_list)
    print(f"预警清单: {alerts_data.get('total_alerts', 0)} 项")

    # ── 加载可选数据 ──
    supplier_report: dict[str, Any] | None = _load_json(args.supplier_report, "供应商报告")
    supply_demand_data: dict[str, Any] | None = _load_json(args.supply_demand, "供需数据")
    inventory_plan: dict[str, Any] | None = _load_json(args.inventory_plan, "库存计划")

    # ── 生成采购计划 ──
    purchase_plan: dict[str, Any] = generate_purchase_plan(
        alert_list, supplier_report, supply_demand_data, inventory_plan, args.moq
    )
    print(f"采购计划: {purchase_plan['total_items']} 项")
    print(f"  紧急: {purchase_plan['urgent_count']} 项")
    print(f"  本周: {purchase_plan['weekly_count']} 项")
    print(f"  本月: {purchase_plan['monthly_count']} 项")
    print(f"  总采购量: {purchase_plan['total_purchase_qty']:.2f}")

    if purchase_plan["top_urgent"]:
        print("TOP 紧急采购项:")
        for item in purchase_plan["top_urgent"][:3]:
            suppliers: str = ", ".join(
                s.get("推荐供应商", "未指定")
                for s in item.get("供应商分配", [])
            )
            print(f"  {item['物料编码']}: {item['建议采购量']:.2f} "
                  f"({item['紧急程度']}) → {suppliers}")

    # ── 输出 ──
    report: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "alerts_source": str(alerts_path),
        "supplier_source": args.supplier_report if args.supplier_report else "未提供",
        "supply_demand_source": args.supply_demand if args.supply_demand else "未提供",
        "purchase_plan": purchase_plan,
    }

    with open(output_path, "w", encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2)
    print(f"采购计划已保存: {output_path}")


def _load_json(path_str: str | None, name: str) -> dict[str, Any] | None:
    """安全加载 JSON 文件。"""
    if not path_str:
        return None
    path: Path = Path(path_str)
    if not path.exists():
        print(f"警告: {name}文件不存在: {path}")
        return None
    with open(path, "r", encoding="utf-8") as fp:
        data: dict[str, Any] = json.load(fp)
    print(f"已加载{name}。")
    return data


if __name__ == "__main__":
    main()