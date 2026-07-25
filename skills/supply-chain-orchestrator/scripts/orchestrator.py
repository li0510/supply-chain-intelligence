"""
供应链分析编排器 (orchestrator.py)

供应链智能分析平台 — 顶层入口 Skill

功能：引导用户选择分析模块、按依赖顺序编排子 Skill、
     追踪执行进度、汇总最终报告。
     不处理具体数据，仅负责调度与协调。

更新内容：
    - execute_module 从占位符替换为真实的 subprocess 调用
    - main 函数的交互模式已实现
    - MODULE_CONFIG 中的文件路径更新为双输出模式

用法:
    uv run orchestrator.py --project-dir <项目工作目录路径> \
      [--modules <模块编号列表>] [--all] [--dry-run]

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
# 模块配置
# ============================================================================

MODULE_CONFIG: dict[str, dict[str, Any]] = {
    "data-inspector": {
        "name": "数据验表与探查",
        "index": 1,
        "required_files": [],
        "optional_files": [],
        "output_files": [
            "extracted_summary.parquet",
            "extracted_weekly.parquet",
            "raw_data_profile.json",
            "error_report.json",
        ],
        "script": "data_extractor",
        "user_input_required": "原始数据文件路径",
        "cli_args": [
            "--input", "{raw_data_dir}",
            "--output", "{project_dir}",
            "--column-mapping", '{"物料编码":"物料编码","库存量":"库存","入库数量":"入库","出库数量":"出库","结存数量":"结存"}',
            "--header-row", "1",
            "--data-start-row", "2",
        ],
    },
    "inventory-overview": {
        "name": "库存全景分析",
        "index": 2,
        "required_files": ["extracted_summary.parquet"],
        "optional_files": ["extracted_weekly.parquet", "raw_data_profile.json"],
        "output_files": [
            "inventory_overview.json",
            "efficiency_cost_report.json",
        ],
        "script": "data_aggregator",
        "user_input_required": None,
        "cli_args": [
            "--input", "{project_dir}/extracted_summary.parquet",
            "--output", "{project_dir}/inventory_overview.json",
        ],
        "additional_scripts": [
            {
                "script": "inventory_turnover",
                "cli_args": [
                    "--input", "{project_dir}/extracted_summary.parquet",
                    "--weekly", "{project_dir}/extracted_weekly.parquet",
                    "--output", "{project_dir}/efficiency_cost_report.json",
                ],
            },
            {
                "script": "cost_analyzer",
                "cli_args": [
                    "--input", "{project_dir}/extracted_summary.parquet",
                    "--output", "{project_dir}/efficiency_cost_report.json",
                    "--append",
                ],
            },
        ],
    },
    "category-classifier": {
        "name": "分类与策略",
        "index": 3,
        "required_files": ["extracted_summary.parquet", "extracted_weekly.parquet"],
        "optional_files": ["efficiency_cost_report.json", "category_strategy.json"],
        "output_files": ["abc_xyz_result.json"],
        "script": "abc_classifier",
        "user_input_required": None,
        "cli_args": [
            "--input", "{project_dir}/extracted_summary.parquet",
            "--output", "{project_dir}/abc_xyz_result.json",
        ],
        "additional_scripts": [
            {
                "script": "xyz_classifier",
                "cli_args": [
                    "--input", "{project_dir}/extracted_weekly.parquet",
                    "--output", "{project_dir}/abc_xyz_result.json",
                    "--append",
                ],
            },
        ],
    },
    "supplier-analyzer": {
        "name": "供应商分析",
        "index": 4,
        "required_files": ["extracted_summary.parquet"],
        "optional_files": [],
        "output_files": ["supplier_report.json"],
        "script": "supplier_evaluator",
        "user_input_required": None,
        "cli_args": [
            "--input", "{project_dir}/extracted_summary.parquet",
            "--output", "{project_dir}/supplier_report.json",
        ],
    },
    "supply-demand-matcher": {
        "name": "供需匹配",
        "index": 5,
        "required_files": ["extracted_summary.parquet"],
        "optional_files": ["supplier_report.json"],
        "output_files": ["supply_demand_gap.json"],
        "script": "supply_demand_matcher",
        "user_input_required": "需求端数据文件路径",
        "cli_args": [
            "--supply", "{project_dir}/extracted_summary.parquet",
            "--demand", "{demand_file}",
            "--output", "{project_dir}/supply_demand_gap.json",
        ],
    },
    "inventory-planner": {
        "name": "库存计划与预警",
        "index": 6,
        "required_files": [
            "extracted_weekly.parquet",
            "extracted_summary.parquet",
            "abc_xyz_result.json",
        ],
        "optional_files": ["supply_demand_gap.json"],
        "output_files": [
            "forecast_result.json",
            "inventory_plan.json",
            "alert_list.json",
            "three_lines_summary.json",
        ],
        "script": "demand_forecast",
        "user_input_required": None,
        "cli_args": [
            "--input", "{project_dir}/extracted_weekly.parquet",
            "--output", "{project_dir}/forecast_result.json",
        ],
        "additional_scripts": [
            {
                "script": "inventory_planning",
                "cli_args": [
                    "--data", "{project_dir}/extracted_weekly.parquet",
                    "--summary", "{project_dir}/extracted_summary.parquet",
                    "--classification", "{project_dir}/abc_xyz_result.json",
                    "--forecast", "{project_dir}/forecast_result.json",
                    "--output", "{project_dir}/inventory_plan.json",
                ],
            },
            {
                "script": "inventory_alert",
                "cli_args": [
                    "--data", "{project_dir}/extracted_weekly.parquet",
                    "--plan", "{project_dir}/inventory_plan.json",
                    "--summary", "{project_dir}/extracted_summary.parquet",
                    "--output", "{project_dir}/alert_list.json",
                ],
            },
        ],
    },
    "purchase-advisor": {
        "name": "采购决策建议",
        "index": 7,
        "required_files": ["alert_list.json"],
        "optional_files": [
            "supplier_report.json",
            "supply_demand_gap.json",
            "abc_xyz_result.json",
            "inventory_plan.json",
        ],
        "output_files": [
            "purchase_plan.json",
            "final_report.json",
            "action_history.json",
        ],
        "script": "purchase_planner",
        "user_input_required": None,
        "cli_args": [
            "--alerts", "{project_dir}/alert_list.json",
            "--inventory-plan", "{project_dir}/inventory_plan.json",
            "--output", "{project_dir}/purchase_plan.json",
        ],
        "additional_scripts": [
            {
                "script": "report_generator",
                "cli_args": [
                    "--project-dir", "{project_dir}",
                    "--output", "{project_dir}/final_report.json",
                ],
            },
        ],
    },
}


# ============================================================================
# 状态扫描
# ============================================================================

def scan_module_status(project_dir: Path) -> dict[str, dict[str, Any]]:
    """
    扫描项目目录，判断每个模块的可用状态。

    Parameters
    ----------
    project_dir : Path
        项目工作目录路径。

    Returns
    -------
    dict[str, dict[str, Any]]
        每个模块的状态信息。
    """
    status: dict[str, dict[str, Any]] = {}

    for module_key, config in MODULE_CONFIG.items():
        required_files: list[str] = config["required_files"]
        optional_files: list[str] = config.get("optional_files", [])
        output_files: list[str] = config["output_files"]

        # 检查产出文件是否已存在（判断是否已完成）
        all_outputs_exist: bool = all(
            (project_dir / f).exists() for f in output_files
        )

        # 检查必需文件是否都存在
        all_required_exist: bool = all(
            (project_dir / f).exists() for f in required_files
        )

        # 检查可选文件
        missing_optional: list[str] = [
            f for f in optional_files if not (project_dir / f).exists()
        ]

        if all_outputs_exist:
            module_status: str = "completed"
            action: str = "已完成"
        elif all_required_exist:
            module_status = "ready"
            action = "可执行"
        elif required_files:
            module_status = "blocked"
            action = "不可执行"
        else:
            module_status = "ready"
            action = "可执行"

        status[module_key] = {
            "name": config["name"],
            "index": config["index"],
            "status": module_status,
            "action": action,
            "missing_required": [
                f for f in required_files if not (project_dir / f).exists()
            ],
            "missing_optional": missing_optional,
            "user_input_required": config["user_input_required"],
        }

    return status


# ============================================================================
# 依赖解析
# ============================================================================

def resolve_execution_order(
    selected_modules: list[str],
) -> list[str]:
    """
    按依赖关系和模块编号排序执行顺序。

    Parameters
    ----------
    selected_modules : list[str]
        用户选择的模块键列表。

    Returns
    -------
    list[str]
        按执行顺序排列的模块键列表。
    """
    sorted_modules: list[str] = sorted(
        selected_modules,
        key=lambda m: MODULE_CONFIG[m]["index"],
    )

    return sorted_modules


# ============================================================================
# 执行单个模块
# ============================================================================

def execute_module(
    module_key: str,
    project_dir: Path,
    scripts_base_dir: Path,
    raw_data_dir: Path | None = None,
    demand_file: Path | None = None,
) -> bool:
    """
    执行单个分析模块的脚本。

    使用 subprocess.run 实际调用子 Skill 的 Python 脚本，
    传递正确的命令行参数，捕获输出并检查返回码。

    Parameters
    ----------
    module_key : str
        模块键名。
    project_dir : Path
        项目工作目录路径。
    scripts_base_dir : Path
        脚本基础目录路径。
    raw_data_dir : Path | None
        原始数据文件目录路径（data-inspector 模块需要）。
    demand_file : Path | None
        需求端数据文件路径（supply-demand-matcher 模块需要）。

    Returns
    -------
    bool
        执行是否成功（所有脚本 returncode == 0）。
    """
    config: dict[str, Any] = MODULE_CONFIG[module_key]
    module_name: str = config["name"]
    script_name: str = config["script"]

    print(f"\n{'=' * 60}")
    print(f"[执行] {module_name} ({module_key})")
    print(f"{'=' * 60}")

    script_dir: Path = scripts_base_dir / module_key / "scripts"

    if not script_dir.exists():
        print(f"错误: 脚本目录不存在: {script_dir}")
        return False

    all_success: bool = True

    # ── 执行主脚本 ──
    cli_args: list[str] = config.get("cli_args", [])
    formatted_args: list[str] = _format_cli_args(
        cli_args, project_dir, raw_data_dir, demand_file
    )

    script_path: Path = script_dir / f"{script_name}.py"
    if not script_path.exists():
        print(f"警告: 脚本文件不存在: {script_path}")
        return False

    print(f"  执行: {script_path.name}")
    result: subprocess.CompletedProcess = subprocess.run(
        [sys.executable, str(script_path)] + formatted_args,
        capture_output=True,
        text=True,
    )

    if result.stdout:
        for line in result.stdout.strip().split("\n"):
            print(f"    {line}")

    if result.returncode != 0:
        print(f"  ❌ 失败 (returncode={result.returncode})")
        if result.stderr:
            for line in result.stderr.strip().split("\n"):
                print(f"    [stderr] {line}")
        all_success = False
    else:
        print(f"  ✅ 成功")

    # ── 执行附加脚本 ──
    additional_scripts: list[dict[str, Any]] = config.get("additional_scripts", [])
    for additional in additional_scripts:
        additional_script_name: str = additional["script"]
        additional_cli_args: list[str] = additional.get("cli_args", [])
        additional_formatted_args: list[str] = _format_cli_args(
            additional_cli_args, project_dir, raw_data_dir, demand_file
        )

        additional_script_path: Path = script_dir / f"{additional_script_name}.py"
        if not additional_script_path.exists():
            print(f"  警告: 附加脚本文件不存在: {additional_script_path}")
            all_success = False
            continue

        print(f"  执行: {additional_script_path.name}")
        additional_result: subprocess.CompletedProcess = subprocess.run(
            [sys.executable, str(additional_script_path)] + additional_formatted_args,
            capture_output=True,
            text=True,
        )

        if additional_result.stdout:
            for line in additional_result.stdout.strip().split("\n"):
                print(f"    {line}")

        if additional_result.returncode != 0:
            print(f"  ❌ 失败 (returncode={additional_result.returncode})")
            if additional_result.stderr:
                for line in additional_result.stderr.strip().split("\n"):
                    print(f"    [stderr] {line}")
            all_success = False
        else:
            print(f"  ✅ 成功")

    return all_success


def _format_cli_args(
    cli_args: list[str],
    project_dir: Path,
    raw_data_dir: Path | None,
    demand_file: Path | None,
) -> list[str]:
    """
    格式化命令行参数，替换占位符。

    Parameters
    ----------
    cli_args : list[str]
        包含占位符的 CLI 参数列表。
    project_dir : Path
        项目工作目录路径。
    raw_data_dir : Path | None
        原始数据文件目录路径。
    demand_file : Path | None
        需求端数据文件路径。

    Returns
    -------
    list[str]
        格式化后的 CLI 参数列表。
    """
    formatted: list[str] = []
    for arg in cli_args:
        if "{project_dir}" in arg:
            arg = arg.replace("{project_dir}", str(project_dir))
        if "{raw_data_dir}" in arg and raw_data_dir is not None:
            arg = arg.replace("{raw_data_dir}", str(raw_data_dir))
        if "{demand_file}" in arg and demand_file is not None:
            arg = arg.replace("{demand_file}", str(demand_file))
        formatted.append(arg)
    return formatted


# ============================================================================
# 生成菜单
# ============================================================================

def generate_menu(status: dict[str, dict[str, Any]]) -> str:
    """
    生成模块状态菜单字符串。

    Parameters
    ----------
    status : dict[str, dict[str, Any]]
        模块状态信息。

    Returns
    -------
    str
        格式化的菜单字符串。
    """
    lines: list[str] = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "📊 供应链分析模块状态",
        "",
    ]

    status_icons: dict[str, str] = {
        "completed": "✅",
        "ready": "🔄",
        "blocked": "❌",
    }

    for module_key in sorted(status.keys(), key=lambda k: status[k]["index"]):
        info: dict[str, Any] = status[module_key]
        icon: str = status_icons.get(info["status"], "❓")
        line: str = f"{info['index']}. {icon} {info['name']} ({module_key})"

        if info["status"] == "blocked":
            missing: list[str] = info.get("missing_required", [])
            line += f" — 缺少: {', '.join(missing)}"
        elif info.get("missing_optional"):
            missing_opt: list[str] = info.get("missing_optional", [])
            line += f" — 可选缺失: {', '.join(missing_opt)}"

        if info.get("user_input_required"):
            line += f"\n   ⚠️ 需要用户提供: {info['user_input_required']}"

        lines.append(line)

    lines.extend([
        "",
        "请选择需要执行的模块（输入编号，多个用逗号分隔）：",
        "或输入 \"all\" 执行全部可执行模块。",
        "或输入 \"q\" 退出。",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ])

    return "\n".join(lines)


# ============================================================================
# 交互模式
# ============================================================================

def interactive_select_modules(status: dict[str, dict[str, Any]]) -> list[str] | None:
    """
    交互式引导用户选择要执行的模块。

    Parameters
    ----------
    status : dict[str, dict[str, Any]]
        模块状态信息。

    Returns
    -------
    list[str] | None
        用户选择的模块键列表，None 表示退出。
    """
    menu: str = generate_menu(status)
    print(menu)

    index_to_key: dict[int, str] = {
        MODULE_CONFIG[k]["index"]: k for k in MODULE_CONFIG
    }

    while True:
        try:
            user_input: str = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            return None

        if user_input.lower() == "q":
            return None

        if user_input.lower() == "all":
            selected: list[str] = [
                k for k, s in status.items()
                if s["status"] in ("ready", "completed")
            ]
            print(f"已选择全部可执行模块: {len(selected)} 个")
            return selected

        if "," in user_input:
            indices: list[int] = []
            for part in user_input.split(","):
                part = part.strip()
                if part.isdigit():
                    indices.append(int(part))
            if indices:
                selected = []
                for idx in indices:
                    if idx in index_to_key:
                        module_key: str = index_to_key[idx]
                        if status[module_key]["status"] != "blocked":
                            selected.append(module_key)
                        else:
                            print(f"警告: 模块 {idx} ({status[module_key]['name']}) 不可执行，已跳过。")
                if selected:
                    print(f"已选择模块: {len(selected)} 个")
                    return selected
                else:
                    print("没有可执行的模块，请重新选择。")
            else:
                print("输入无效，请输入模块编号（如: 1,2,3）或 'all' 或 'q'。")
        else:
            print("输入无效，请输入模块编号（用逗号分隔多个）或 'all' 或 'q'。")


# ============================================================================
# 主入口
# ============================================================================

def main() -> None:
    """命令行入口，执行供应链分析编排。"""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="供应链分析编排器 — 供应链智能分析平台"
    )
    parser.add_argument("--project-dir", required=True, help="项目工作目录路径")
    parser.add_argument("--raw-data-dir", type=str, default=None,
                        help="原始数据文件夹路径（data-inspector 需要）")
    parser.add_argument("--demand-file", type=str, default=None,
                        help="需求端数据文件路径（supply-demand-matcher 需要）")
    parser.add_argument("--modules", type=str, default=None,
                        help="要执行的模块编号列表（逗号分隔，如: 1,2,3）")
    parser.add_argument("--all", action="store_true",
                        help="执行全部可执行模块")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅扫描状态和生成执行计划，不实际执行")
    parser.add_argument("--status-only", action="store_true",
                        help="仅扫描并显示模块状态")
    args: argparse.Namespace = parser.parse_args()

    project_dir: Path = Path(args.project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)

    scripts_base_dir: Path = Path(__file__).parent.parent
    raw_data_dir: Path | None = Path(args.raw_data_dir) if args.raw_data_dir else None
    demand_file: Path | None = Path(args.demand_file) if args.demand_file else None

    # ── 扫描模块状态 ──
    print("正在扫描项目目录...")
    status: dict[str, dict[str, Any]] = scan_module_status(project_dir)

    completed_count: int = sum(
        1 for s in status.values() if s["status"] == "completed"
    )
    ready_count: int = sum(
        1 for s in status.values() if s["status"] == "ready"
    )
    blocked_count: int = sum(
        1 for s in status.values() if s["status"] == "blocked"
    )

    print(f"扫描完成: ✅ {completed_count} 已完成, 🔄 {ready_count} 可执行, ❌ {blocked_count} 不可执行")

    # ── 仅状态模式 ──
    if args.status_only:
        menu: str = generate_menu(status)
        print(menu)
        return

    # ── 确定要执行的模块 ──
    index_to_key: dict[int, str] = {
        MODULE_CONFIG[k]["index"]: k for k in MODULE_CONFIG
    }

    selected_modules: list[str] = []

    if args.all:
        selected_modules = [
            k for k, s in status.items()
            if s["status"] in ("ready", "completed")
        ]
        print(f"\n已选择全部可执行模块: {len(selected_modules)} 个")
    elif args.modules:
        indices: list[int] = [
            int(i.strip())
            for i in args.modules.split(",")
            if i.strip().isdigit()
        ]
        for idx in indices:
            if idx in index_to_key:
                module_key: str = index_to_key[idx]
                if status[module_key]["status"] != "blocked":
                    selected_modules.append(module_key)
                else:
                    print(
                        f"警告: 模块 {idx} ({status[module_key]['name']}) 不可执行，已跳过。"
                    )
        print(f"\n已选择模块: {len(selected_modules)} 个")
    else:
        # ── 交互模式 ──
        selected = interactive_select_modules(status)
        if selected is None:
            print("已退出。")
            return
        selected_modules = selected

    if not selected_modules:
        print("没有可执行的模块。")
        return

    # ── 依赖解析 ──
    execution_order: list[str] = resolve_execution_order(selected_modules)
    print(f"\n执行计划:")
    for i, module_key in enumerate(execution_order, 1):
        print(f"  {i}. {MODULE_CONFIG[module_key]['name']}")

    # ── 空跑模式 ──
    if args.dry_run:
        print("\n[空跑模式] 不执行实际操作。")
        return

    # ── 按序执行 ──
    results: dict[str, bool] = {}
    for module_key in execution_order:
        success: bool = execute_module(
            module_key, project_dir, scripts_base_dir, raw_data_dir, demand_file
        )
        results[module_key] = success
        if not success:
            print(f"\n⚠️ 模块 {MODULE_CONFIG[module_key]['name']} 执行失败。")
            try:
                cont: str = input("是否继续执行后续模块？(y/n): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                cont = "n"
            if cont != "y":
                print("已中止。")
                break

    # ── 汇总 ──
    print(f"\n{'=' * 60}")
    print("执行汇总")
    print(f"{'=' * 60}")
    success_count: int = sum(1 for v in results.values() if v)
    fail_count: int = sum(1 for v in results.values() if not v)
    print(f"成功: {success_count} 个, 失败: {fail_count} 个")

    for module_key, module_success in results.items():
        icon: str = "✅" if module_success else "❌"
        print(f"  {icon} {MODULE_CONFIG[module_key]['name']}")

    # ── 保存执行记录 ──
    history_path: Path = project_dir / "orchestrator_history.json"
    history: list[dict[str, Any]] = []
    if history_path.exists():
        with open(history_path, "r", encoding="utf-8") as fp:
            data: dict[str, Any] = json.load(fp)
            history = data.get("history", [])

    history.append({
        "timestamp": datetime.now().isoformat(),
        "selected_modules": selected_modules,
        "execution_results": {
            k: "success" if v else "failed" for k, v in results.items()
        },
    })

    if len(history) > 10:
        history = history[-10:]

    with open(history_path, "w", encoding="utf-8") as fp:
        json.dump({"history": history}, fp, ensure_ascii=False, indent=2)

    print(f"\n编排器执行记录已保存: {history_path}")
    print("供应链分析编排完成。")


if __name__ == "__main__":
    main()