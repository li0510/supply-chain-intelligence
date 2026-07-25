"""
三道防线编排脚本 (three_lines_defense.py)

供应链智能分析平台 — inventory-planner 子 Skill

功能：串联第一道防线（需求预测）、第二道防线（库存计划）、
     第三道防线（执行预警），提供一站式库存管控。
     增强功能：
         - 信息流整合：预测结果标准化输出供供应商共享
         - 采购预算联动估算供财务参考
         - PDCA 检查点：本期 vs 上期 KPI 对比

用法:
    uv run three_lines_defense.py --data <extracted_weekly.parquet路径> \
      --classification <abc_xyz_result.json路径> \
      --output-dir <输出目录路径> \
      [--lead-time-weeks <提前期>] [--ordering-cost <订货成本>] \
      [--holding-rate <持有成本率>] [--sigma-lt <提前期标准差>]

作者: Supply Chain Intelligence Team
版本: 0.2.5
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


# ============================================================================
# 主入口
# ============================================================================

def main() -> None:
    """命令行入口，按顺序执行三道防线分析。"""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="三道防线编排（增强版） — 供应链智能分析平台"
    )
    parser.add_argument("--data", required=True,
                        help="extracted_weekly.parquet 文件路径")
    parser.add_argument("--classification", required=True,
                        help="abc_xyz_result.json 文件路径")
    parser.add_argument("--output-dir", required=True,
                        help="输出目录路径")
    parser.add_argument("--lead-time-weeks", type=int, default=1,
                        help="提前期（周）")
    parser.add_argument("--ordering-cost", type=float, default=100.0,
                        help="每次订货成本")
    parser.add_argument("--holding-rate", type=float, default=0.2,
                        help="年持有成本率")
    parser.add_argument("--sigma-lt", type=float, default=None,
                        help="提前期标准差（周）")
    args: argparse.Namespace = parser.parse_args()

    data_path: Path = Path(args.data)
    classification_path: Path = Path(args.classification)
    output_dir: Path = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    scripts_dir: Path = Path(__file__).parent

    results: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "data_source": str(data_path),
        "classification_source": str(classification_path),
        "parameters": {
            "lead_time_weeks": args.lead_time_weeks,
            "ordering_cost": args.ordering_cost,
            "holding_rate": args.holding_rate,
            "sigma_lt": args.sigma_lt,
        },
    }

    # ── 第一道防线：需求预测 ──
    print("=" * 60)
    print("[第一道防线] 需求预测（13周滚动窗口）")
    print("=" * 60)
    forecast_output: Path = output_dir / "forecast_result.json"

    forecast_cmd: list[str] = [
        sys.executable, str(scripts_dir / "demand_forecast.py"),
        "--input", str(data_path),
        "--output", str(forecast_output),
    ]

    forecast_result: subprocess.CompletedProcess = subprocess.run(
        forecast_cmd, capture_output=True, text=True
    )
    print(forecast_result.stdout)
    if forecast_result.returncode != 0:
        print(f"错误: 需求预测失败\n{forecast_result.stderr}")
        sys.exit(1)

    if forecast_output.exists():
        with open(forecast_output, "r", encoding="utf-8") as fp:
            forecast_data: dict[str, Any] = json.load(fp)
        results["demand_forecast"] = forecast_data

        # ── 信息流整合：生成供应商共享格式的预测摘要 ──
        supplier_forecast_path: Path = output_dir / "supplier_forecast_summary.json"
        _generate_supplier_forecast(forecast_data, supplier_forecast_path)
        print(f"供应商预测摘要已保存: {supplier_forecast_path}")

    # ── 第二道防线：库存计划 ──
    print("=" * 60)
    print("[第二道防线] 库存计划（分类差异化）")
    print("=" * 60)
    plan_output: Path = output_dir / "inventory_plan.json"

    plan_cmd: list[str] = [
        sys.executable, str(scripts_dir / "inventory_planning.py"),
        "--data", str(data_path),
        "--classification", str(classification_path),
        "--forecast", str(forecast_output),
        "--output", str(plan_output),
        "--lead-time-weeks", str(args.lead_time_weeks),
        "--ordering-cost", str(args.ordering_cost),
        "--holding-rate", str(args.holding_rate),
    ]

    if args.sigma_lt is not None:
        plan_cmd.extend(["--sigma-lt", str(args.sigma_lt)])

    plan_result: subprocess.CompletedProcess = subprocess.run(
        plan_cmd, capture_output=True, text=True
    )
    print(plan_result.stdout)
    if plan_result.returncode != 0:
        print(f"错误: 库存计划失败\n{plan_result.stderr}")
        sys.exit(1)

    if plan_output.exists():
        with open(plan_output, "r", encoding="utf-8") as fp:
            results["inventory_plan"] = json.load(fp)

        # ── 采购预算联动估算 ──
        budget_path: Path = output_dir / "budget_estimation.json"
        _generate_budget_estimation(results["inventory_plan"], budget_path)
        print(f"采购预算估算已保存: {budget_path}")

    # ── 第三道防线：执行预警 ──
    print("=" * 60)
    print("[第三道防线] 执行预警")
    print("=" * 60)
    alert_output: Path = output_dir / "alert_list.json"

    alert_cmd: list[str] = [
        sys.executable, str(scripts_dir / "inventory_alert.py"),
        "--data", str(data_path),
        "--plan", str(plan_output),
        "--output", str(alert_output),
    ]

    alert_result: subprocess.CompletedProcess = subprocess.run(
        alert_cmd, capture_output=True, text=True
    )
    print(alert_result.stdout)
    if alert_result.returncode != 0:
        print(f"错误: 执行预警失败\n{alert_result.stderr}")
        sys.exit(1)

    if alert_output.exists():
        with open(alert_output, "r", encoding="utf-8") as fp:
            results["alert_list"] = json.load(fp)

    # ── PDCA 检查点 ──
    pdca_path: Path = output_dir / "pdca_checkpoint.json"
    _generate_pdca_checkpoint(results, pdca_path)
    print(f"PDCA 检查点已保存: {pdca_path}")

    # ── 汇总 ──
    summary_output: Path = output_dir / "three_lines_summary.json"
    with open(summary_output, "w", encoding="utf-8") as fp:
        json.dump(results, fp, ensure_ascii=False, indent=2)

    print("=" * 60)
    print("三道防线分析完成!")
    print(f"  预测结果: {forecast_output}")
    print(f"  库存计划: {plan_output}")
    print(f"  预警清单: {alert_output}")
    print(f"  供应商预测摘要: {output_dir / 'supplier_forecast_summary.json'}")
    print(f"  采购预算估算: {output_dir / 'budget_estimation.json'}")
    print(f"  PDCA 检查点: {pdca_path}")
    print(f"  汇总报告: {summary_output}")
    print("=" * 60)


def _generate_supplier_forecast(
    forecast_data: dict[str, Any],
    output_path: Path,
) -> None:
    """生成供供应商共享的预测摘要。"""
    demand_forecast: dict[str, Any] = forecast_data.get("demand_forecast", {})
    item_details: list[dict] = demand_forecast.get("item_details", [])

    summary: list[dict[str, Any]] = []
    for item in item_details[:20]:
        summary.append({
            "物料编码": item.get("物料编码"),
            "预测周需求量": item.get("预测周需求量"),
            "趋势": item.get("趋势"),
            "预测区间": f"{item.get('预测下界')} ~ {item.get('预测上界')}",
        })

    report: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(),
        "forecast_window_weeks": demand_forecast.get("forecast_window_weeks"),
        "total_items": demand_forecast.get("total_items"),
        "top_items": summary,
        "note": "本预测基于统计模型生成，仅供参考。实际需求可能因业务因素发生变化。",
    }

    with open(output_path, "w", encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2)


def _generate_budget_estimation(
    inventory_plan_data: dict[str, Any],
    output_path: Path,
) -> None:
    """生成采购预算联动估算。"""
    inventory_plan: dict[str, Any] = inventory_plan_data.get("inventory_plan", {})
    item_details: list[dict] = inventory_plan.get("item_details", [])

    total_annual_holding: float = sum(
        item.get("年持有成本(估算)", 0.0) for item in item_details
    )
    total_annual_ordering: float = sum(
        item.get("年订货成本(估算)", 0.0) for item in item_details
    )

    report: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(),
        "total_items": len(item_details),
        "estimated_annual_holding_cost": round(total_annual_holding, 2),
        "estimated_annual_ordering_cost": round(total_annual_ordering, 2),
        "estimated_annual_total_cost": round(total_annual_holding + total_annual_ordering, 2),
        "note": "以上为基于当前库存计划参数的估算值，不含物料单价。实际成本可能因市场价格波动而变化。",
    }

    with open(output_path, "w", encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2)


def _generate_pdca_checkpoint(
    results: dict[str, Any],
    output_path: Path,
) -> None:
    """
    生成 PDCA 检查点，记录当前执行结果的关键 KPI，
    供后续执行时进行本期 vs 上期对比。

    Parameters
    ----------
    results : dict[str, Any]
        三道防线全部执行结果。
    output_path : Path
        输出文件路径。
    """
    # 提取关键 KPI
    kpis: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
    }

    # 从需求预测中提取
    demand_forecast: dict[str, Any] = results.get("demand_forecast", {}).get("demand_forecast", {})
    kpis["forecasted_items"] = demand_forecast.get("total_items", 0)
    kpis["avg_mae"] = round(
        sum(
            item.get("MAE", 0) for item in demand_forecast.get("item_details", [])
        ) / max(demand_forecast.get("total_items", 1), 1),
        2,
    )

    # 从库存计划中提取
    inventory_plan: dict[str, Any] = results.get("inventory_plan", {}).get("inventory_plan", {})
    kpis["risk_count"] = inventory_plan.get("risk_count", 0)
    kpis["reorder_count"] = inventory_plan.get("reorder_count", 0)
    kpis["overstock_count"] = inventory_plan.get("overstock_count", 0)

    # 从预警清单中提取
    alert_list: dict[str, Any] = results.get("alert_list", {}).get("alert_list", {})
    kpis["total_alerts"] = alert_list.get("total_alerts", 0)

    # 与上期对比
    history_path: Path = output_path.parent / "pdca_history.json"
    previous_kpis: dict[str, Any] = {}
    if history_path.exists():
        with open(history_path, "r", encoding="utf-8") as fp:
            history_data: dict[str, Any] = json.load(fp)
            previous_items: list[dict] = history_data.get("history", [])
            if previous_items:
                previous_kpis = previous_items[-1].get("kpis", {})

    comparison: dict[str, Any] = {}
    if previous_kpis:
        for key in ["risk_count", "reorder_count", "overstock_count", "total_alerts"]:
            current: float = kpis.get(key, 0)
            previous: float = previous_kpis.get(key, 0)
            if previous > 0:
                change_pct: float = (current - previous) / previous * 100
                comparison[key] = {
                    "current": current,
                    "previous": previous,
                    "change_pct": round(change_pct, 1),
                    "trend": "改善" if change_pct < 0 else ("恶化" if change_pct > 0 else "持平"),
                }
            else:
                comparison[key] = {"current": current, "previous": 0, "change_pct": 0, "trend": "首次记录"}

    checkpoint: dict[str, Any] = {
        "kpis": kpis,
        "comparison_with_previous": comparison,
        "summary": (
            f"风险项{comparison.get('risk_count', {}).get('trend', 'N/A')}，"
            f"预警总数{comparison.get('total_alerts', {}).get('trend', 'N/A')}"
        ) if comparison else "首次执行，无历史对比数据。",
    }

    # 保存当前 KPI 到历史记录
    history: list[dict[str, Any]] = []
    if history_path.exists():
        with open(history_path, "r", encoding="utf-8") as fp:
            history_data = json.load(fp)
            history = history_data.get("history", [])
    history.append({"timestamp": datetime.now().isoformat(), "kpis": kpis})
    if len(history) > 10:
        history = history[-10:]
    with open(history_path, "w", encoding="utf-8") as fp:
        json.dump({"history": history}, fp, ensure_ascii=False, indent=2)

    with open(output_path, "w", encoding="utf-8") as fp:
        json.dump(checkpoint, fp, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()