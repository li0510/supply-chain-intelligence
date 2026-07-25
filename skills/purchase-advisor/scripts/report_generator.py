"""
综合报告生成脚本 (report_generator.py)

供应链智能分析平台 — purchase-advisor 子 Skill

功能：汇总所有上游分析结果，生成决策级综合报告。
     包括执行摘要、各模块分析汇总、未完成项清单、
     下一步行动建议、行动闭环记录。

用法:
    uv run report_generator.py --project-dir <项目工作目录路径> \
      --output <输出JSON路径>

作者: Supply Chain Intelligence Team
版本: 0.2.5
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


# ============================================================================
# 配置
# ============================================================================

EXPECTED_FILES: dict[str, str] = {
    "extracted_summary.parquet": "data-inspector",
    "extracted_weekly.parquet": "data-inspector",
    "raw_data_profile.json": "data-inspector",
    "error_report.json": "data-inspector",
    "inventory_overview.json": "inventory-overview",
    "efficiency_cost_report.json": "inventory-overview",
    "abc_xyz_result.json": "category-classifier",
    "supplier_report.json": "supplier-analyzer",
    "supply_demand_gap.json": "supply-demand-matcher",
    "forecast_result.json": "inventory-planner",
    "inventory_plan.json": "inventory-planner",
    "alert_list.json": "inventory-planner",
    "purchase_plan.json": "purchase-advisor",
}


# ============================================================================
# 报告生成
# ============================================================================

def generate_final_report(project_dir: Path) -> dict[str, Any]:
    """
    扫描项目目录中的所有分析结果，生成综合报告。

    Parameters
    ----------
    project_dir : Path
        项目工作目录路径。

    Returns
    -------
    dict[str, Any]
        综合报告。
    """
    # ── 文件存在性检查 ──
    file_status: dict[str, dict[str, Any]] = {}
    completed_modules: list[str] = []
    missing_modules: list[str] = []

    for filename, skill_name in EXPECTED_FILES.items():
        file_path: Path = project_dir / filename
        exists: bool = file_path.exists()
        status_info: dict[str, Any] = {
            "file": filename,
            "skill": skill_name,
            "exists": exists,
            "size_bytes": file_path.stat().st_size if exists else 0,
        }
        file_status[filename] = status_info

        if exists:
            if skill_name not in completed_modules:
                completed_modules.append(skill_name)
        else:
            if skill_name not in missing_modules:
                missing_modules.append(skill_name)

    # ── 提取关键指标 ──
    key_metrics: dict[str, Any] = _extract_key_metrics(project_dir, file_status)

    # ── 未完成项清单 ──
    incomplete_items: list[dict[str, str]] = []
    for filename, info in file_status.items():
        if not info["exists"]:
            incomplete_items.append({
                "file": filename,
                "required_by": info["skill"],
                "action": f"运行 {info['skill']} 以生成 {filename}",
            })

    # ── 下一步行动建议 ──
    next_actions: list[str] = _generate_next_actions(key_metrics, missing_modules)

    return {
        "generated_at": datetime.now().isoformat(),
        "project_dir": str(project_dir),
        "executive_summary": {
            "completed_modules": completed_modules,
            "missing_modules": missing_modules,
            "total_files_expected": len(EXPECTED_FILES),
            "total_files_present": sum(1 for f in file_status.values() if f["exists"]),
        },
        "key_metrics": key_metrics,
        "file_status": file_status,
        "incomplete_items": incomplete_items,
        "next_actions": next_actions,
    }


def _extract_key_metrics(
    project_dir: Path, file_status: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """从各分析报告中提取关键指标。"""
    metrics: dict[str, Any] = {}

    # ── 数据探查 ──
    profile_path: Path = project_dir / "raw_data_profile.json"
    if profile_path.exists():
        with open(profile_path, "r", encoding="utf-8") as fp:
            profile: dict[str, Any] = json.load(fp)
        file_reports: list[dict] = profile.get("file_reports", [])
        if file_reports:
            metrics["source_files"] = len(file_reports)
            metrics["total_rows_raw"] = sum(r.get("total_rows", 0) for r in file_reports)

    # ── 错误报告 ──
    error_path: Path = project_dir / "error_report.json"
    if error_path.exists():
        with open(error_path, "r", encoding="utf-8") as fp:
            error_data: dict[str, Any] = json.load(fp)
        metrics["data_quality_issues"] = error_data.get("total_issues", 0)

    # ── 库存全景 ──
    overview_path: Path = project_dir / "inventory_overview.json"
    if overview_path.exists():
        with open(overview_path, "r", encoding="utf-8") as fp:
            overview: dict[str, Any] = json.load(fp)
        inv_summary: dict[str, Any] = overview.get("inventory_summary", {})
        metrics["total_inventory"] = inv_summary.get("total_inventory", 0)
        metrics["total_balance"] = inv_summary.get("total_balance", 0)
        metrics["unique_items"] = inv_summary.get("unique_items", 0)

    # ── 预警清单 ──
    alert_path: Path = project_dir / "alert_list.json"
    if alert_path.exists():
        with open(alert_path, "r", encoding="utf-8") as fp:
            alert_data: dict[str, Any] = json.load(fp)
        alert_list: dict[str, Any] = alert_data.get("alert_list", alert_data)
        metrics["total_alerts"] = alert_list.get("total_alerts", 0)
        metrics["shortage_count"] = alert_list.get("shortage_count", 0)
        metrics["reorder_count"] = alert_list.get("reorder_count", 0)
        metrics["overstock_count"] = alert_list.get("overstock_count", 0)

    # ── 采购计划 ──
    purchase_path: Path = project_dir / "purchase_plan.json"
    if purchase_path.exists():
        with open(purchase_path, "r", encoding="utf-8") as fp:
            purchase_data: dict[str, Any] = json.load(fp)
        purchase_plan: dict[str, Any] = purchase_data.get("purchase_plan", {})
        metrics["purchase_items"] = purchase_plan.get("total_items", 0)
        metrics["urgent_purchases"] = purchase_plan.get("urgent_count", 0)
        metrics["total_purchase_qty"] = purchase_plan.get("total_purchase_qty", 0)

    return metrics


def _generate_next_actions(
    key_metrics: dict[str, Any], missing_modules: list[str]
) -> list[str]:
    """基于关键指标和缺失模块生成下一步行动建议。"""
    actions: list[str] = []

    # 缺失模块
    if missing_modules:
        actions.append(f"建议运行以下缺失的分析模块: {', '.join(missing_modules)}")

    # 数据质量问题
    quality_issues: int = int(key_metrics.get("data_quality_issues", 0))
    if quality_issues > 0:
        actions.append(f"存在 {quality_issues} 项数据质量问题，建议审查 error_report.json。")

    # 缺货预警
    shortage_count: int = int(key_metrics.get("shortage_count", 0))
    if shortage_count > 0:
        actions.append(f"存在 {shortage_count} 项缺货预警，建议立即审查并下单采购。")

    # 积压预警
    overstock_count: int = int(key_metrics.get("overstock_count", 0))
    if overstock_count > 0:
        actions.append(f"存在 {overstock_count} 项积压预警，建议评估处理方案。")

    # 紧急采购
    urgent_count: int = int(key_metrics.get("urgent_purchases", 0))
    if urgent_count > 0:
        actions.append(f"存在 {urgent_count} 项紧急采购需求，建议今日内完成下单。")

    if not actions:
        actions.append("当前库存状态良好，无紧急行动项。建议定期评审。")

    return actions


# ============================================================================
# 行动闭环记录
# ============================================================================

def save_action_history(
    project_dir: Path, final_report: dict[str, Any]
) -> None:
    """
    保存本次行动记录，供下次步骤 0 回顾。

    Parameters
    ----------
    project_dir : Path
        项目工作目录路径。
    final_report : dict[str, Any]
        综合报告。
    """
    history_path: Path = project_dir / "action_history.json"

    # 读取已有历史记录
    history: list[dict[str, Any]] = []
    if history_path.exists():
        with open(history_path, "r", encoding="utf-8") as fp:
            data: dict[str, Any] = json.load(fp)
            history = data.get("history", [])

    # 追加本次记录
    history.append({
        "timestamp": datetime.now().isoformat(),
        "completed_modules": final_report["executive_summary"]["completed_modules"],
        "missing_modules": final_report["executive_summary"]["missing_modules"],
        "key_metrics": final_report["key_metrics"],
        "next_actions": final_report["next_actions"],
    })

    # 保留最近 10 次记录
    if len(history) > 10:
        history = history[-10:]

    with open(history_path, "w", encoding="utf-8") as fp:
        json.dump({"history": history}, fp, ensure_ascii=False, indent=2)

    print(f"行动记录已保存: {history_path} (共 {len(history)} 条)")


# ============================================================================
# 主入口
# ============================================================================

def main() -> None:
    """命令行入口，生成综合报告并保存行动记录。"""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="综合报告生成 — 供应链智能分析平台"
    )
    parser.add_argument("--project-dir", required=True, help="项目工作目录路径")
    parser.add_argument("--output", required=True, help="输出 JSON 文件路径")
    args: argparse.Namespace = parser.parse_args()

    project_dir: Path = Path(args.project_dir)
    output_path: Path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not project_dir.exists():
        print(f"错误: 项目目录不存在: {project_dir}")
        return

    # ── 生成综合报告 ──
    final_report: dict[str, Any] = generate_final_report(project_dir)

    print(f"综合报告:")
    print(f"  已完成模块: {final_report['executive_summary']['completed_modules']}")
    print(f"  缺失模块: {final_report['executive_summary']['missing_modules']}")
    print(f"  文件覆盖: {final_report['executive_summary']['total_files_present']}/"
          f"{final_report['executive_summary']['total_files_expected']}")

    key_metrics: dict[str, Any] = final_report["key_metrics"]
    if key_metrics:
        print(f"  关键指标:")
        for k, v in key_metrics.items():
            print(f"    {k}: {v}")

    print(f"  未完成项: {len(final_report['incomplete_items'])}")
    print(f"  下一步行动:")
    for action in final_report["next_actions"]:
        print(f"    - {action}")

    # ── 输出 ──
    with open(output_path, "w", encoding="utf-8") as fp:
        json.dump(final_report, fp, ensure_ascii=False, indent=2)
    print(f"综合报告已保存: {output_path}")

    # ── 行动闭环记录 ──
    save_action_history(project_dir, final_report)


if __name__ == "__main__":
    main()