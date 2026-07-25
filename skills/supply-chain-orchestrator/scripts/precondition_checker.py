"""
前置条件检查器 (precondition_checker.py)

供应链智能分析平台 — 通用模块

功能：检查子 Skill 执行前所需的上游产出文件是否就绪。
     返回状态码和缺失清单，供 Skill 引导用户处理。

用法:
    uv run precondition_checker.py --skill <Skill名称> --project-dir <项目目录>

作者: Supply Chain Intelligence Team
版本: 0.2.5
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


# ============================================================================
# 各 Skill 依赖配置
# ============================================================================

DEPENDENCY_CONFIG: dict[str, dict[str, Any]] = {
    "data-inspector": {
        "required": [],
        "optional": ["action_history.json"],
        "description": "数据验表与探查",
    },
    "inventory-overview": {
        "required": ["extracted_summary.parquet"],
        "optional": ["extracted_weekly.parquet", "raw_data_profile.json"],
        "description": "库存全景分析",
    },
    "category-classifier": {
        "required": ["extracted_summary.parquet"],
        "optional": ["efficiency_cost_report.json", "category_strategy.json"],
        "description": "分类与策略",
    },
    "supplier-analyzer": {
        "required": ["extracted_summary.parquet"],
        "optional": [],
        "description": "供应商分析",
        "field_requirements": ["供应商名称", "计划交期", "实际交期"],
    },
    "supply-demand-matcher": {
        "required": ["extracted_summary.parquet"],
        "optional": ["supplier_report.json"],
        "description": "供需匹配",
        "user_input_required": "需求端数据（生产计划/销售订单/预测）",
    },
    "inventory-planner": {
        "required": ["extracted_weekly.parquet", "abc_xyz_result.json"],
        "optional": ["supply_demand_gap.json"],
        "description": "库存计划与预警",
    },
    "purchase-advisor": {
        "required": ["alert_list.json"],
        "optional": ["supplier_report.json", "supply_demand_gap.json", "abc_xyz_result.json"],
        "description": "采购决策建议",
    },
    "supply-chain-orchestrator": {
        "required": [],
        "optional": [],
        "description": "供应链分析编排器",
    },
}


# ============================================================================
# 主函数
# ============================================================================

def check_preconditions(skill_name: str, project_dir: Path) -> dict[str, Any]:
    """
    检查指定 Skill 的前置条件。

    Parameters
    ----------
    skill_name : str
        Skill 标识名称。
    project_dir : Path
        项目工作目录路径。

    Returns
    -------
    dict[str, Any]
        {
            "status": "ready" | "missing_required" | "missing_optional",
            "required_missing": [...],
            "optional_missing": [...],
            "suggestion": "请先运行 xxx 完成..."
        }
    """
    if skill_name not in DEPENDENCY_CONFIG:
        return {
            "status": "error",
            "required_missing": [],
            "optional_missing": [],
            "suggestion": f"未知的 Skill 名称: {skill_name}",
        }

    config: dict[str, Any] = DEPENDENCY_CONFIG[skill_name]
    required_missing: list[str] = []
    optional_missing: list[str] = []

    for req_file in config["required"]:
        if not (project_dir / req_file).exists():
            required_missing.append(req_file)

    for opt_file in config.get("optional", []):
        if not (project_dir / opt_file).exists():
            optional_missing.append(opt_file)

    if required_missing:
        return {
            "status": "missing_required",
            "required_missing": required_missing,
            "optional_missing": optional_missing,
            "suggestion": _build_suggestion(skill_name, required_missing),
        }
    elif optional_missing:
        return {
            "status": "missing_optional",
            "required_missing": [],
            "optional_missing": optional_missing,
            "suggestion": _build_optional_warning(skill_name, optional_missing),
        }
    else:
        return {
            "status": "ready",
            "required_missing": [],
            "optional_missing": [],
            "suggestion": "",
        }


def _build_suggestion(skill_name: str, missing: list[str]) -> str:
    """根据缺失文件生成建议信息。"""
    suggestions: dict[str, str] = {
        "extracted_data.parquet": "请先运行 data-inspector 完成数据验表与提取。",
        "abc_xyz_result.json": "请先运行 category-classifier 完成 ABC-XYZ 分类。",
        "alert_list.json": "请先运行 inventory-planner 完成库存计划与预警。",
    }
    parts: list[str] = []
    for m in missing:
        if m in suggestions:
            parts.append(f"  - {m}: {suggestions[m]}")
        else:
            parts.append(f"  - {m}: 请从上游 Skill 获取或手动提供。")
    return "缺少必需文件:\n" + "\n".join(parts)


def _build_optional_warning(skill_name: str, missing: list[str]) -> str:
    """根据缺失的可选文件生成提醒信息。"""
    parts: list[str] = [f"以下可选文件缺失，部分功能将受限:"]
    for m in missing:
        parts.append(f"  - {m}")
    return "\n".join(parts)


def main() -> None:
    """命令行入口，执行前置条件检查。"""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="前置条件检查器 — 供应链智能分析平台"
    )
    parser.add_argument("--skill", required=True, help="Skill 名称")
    parser.add_argument("--project-dir", required=True, help="项目工作目录路径")
    args: argparse.Namespace = parser.parse_args()

    project_dir: Path = Path(args.project_dir)
    if not project_dir.exists():
        print(f"错误: 项目目录不存在: {project_dir}")
        sys.exit(1)

    result: dict[str, Any] = check_preconditions(args.skill, project_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result["status"] == "missing_required":
        sys.exit(1)


if __name__ == "__main__":
    main()