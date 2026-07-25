"""
端到端集成测试 (test_09_e2e_integration.py)

供应链智能分析平台 — 全链路集成测试

功能：按依赖顺序串联执行全部 7 个子 Skill，验证最终交付物
     的存在性、格式正确性和业务逻辑合理性。
     产出文件保留在固定路径 projects/e2e_test/ 下，供用户直接查看。

更新内容：
    - 验证 1：排除合计行后行数 = 30
    - 在端到端流程中增加调参步骤（hyperparameter_tuner.py → optimal_params.json → demand_forecast.py --optimal-params）

测试覆盖的业务验证（12 项）：
    1. 汇总数据行数正确（排除合计行后为 30）
    2. 平衡校验通过率 > 90%
    3. ABC 分类总和 = 总物料数
    4. XYZ 分类总和 = 总物料数
    5. AX 类服务水平 = 99%
    6. X 类安全库存 < Z 类安全库存
    7. AX 补货策略 = "定期定量(JIT)"
    8. 采购计划按优先级降序
    9. 预测结果包含 MAE/RMSE/Bias
    10. action_history 非空
    11. final_report 存在且包含 executive_summary
    12. 所有产出文件在固定路径下可见

用法:
    uv run pytest tests/test_09_e2e_integration.py -v -s

作者: Supply Chain Intelligence Team
版本: 0.2.5
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import polars as pl
import pytest


# ============================================================================
# 路径配置
# ============================================================================

@pytest.fixture(scope="module")
def e2e_dir(project_root: Path) -> Path:
    """端到端测试的固定输出目录（测试结束后保留）。"""
    return project_root / "projects" / "e2e_test"


@pytest.fixture(scope="module")
def scripts_base_dir(project_root: Path) -> Path:
    """脚本基础目录。"""
    return project_root / "skills"


@pytest.fixture(scope="module")
def test_data_dir(project_root: Path) -> Path:
    """测试数据目录。"""
    return project_root / "tests" / "test_data"


# ============================================================================
# 辅助函数
# ============================================================================

def _run_script(script_path: Path, args: list[str]) -> subprocess.CompletedProcess:
    """运行 Python 脚本并返回结果。"""
    return subprocess.run(
        [sys.executable, str(script_path)] + args,
        capture_output=True,
        text=True,
    )


# ============================================================================
# 测试：全链路端到端执行
# ============================================================================

def test_e2e_full_pipeline(
    scripts_base_dir: Path,
    test_data_dir: Path,
    e2e_dir: Path,
) -> None:
    """
    端到端集成测试：按依赖顺序执行全部 7 个子 Skill，
    验证最终交付物和业务逻辑合理性。
    """
    e2e_dir.mkdir(parents=True, exist_ok=True)

    column_mapping: str = (
        '{"物料编码":"物料编码","库存量":"库存",'
        '"入库数量":"入库","出库数量":"出库","结存数量":"结存"}'
    )

    # ── Skill 01: data-inspector ──
    print("\n[1/7] data-inspector")
    result = _run_script(
        scripts_base_dir / "data-inspector" / "scripts" / "data_extractor.py",
        [
            "--input", str(test_data_dir),
            "--output", str(e2e_dir),
            "--column-mapping", column_mapping,
            "--header-row", "1",
            "--data-start-row", "2",
        ],
    )
    assert result.returncode == 0, f"data_extractor 失败: {result.stderr}"

    # ── Skill 02: inventory-overview ──
    print("\n[2/7] inventory-overview")
    result = _run_script(
        scripts_base_dir / "inventory-overview" / "scripts" / "data_aggregator.py",
        ["--input", str(e2e_dir / "extracted_summary.parquet"),
         "--output", str(e2e_dir / "inventory_overview.json")],
    )
    assert result.returncode == 0, f"data_aggregator 失败: {result.stderr}"

    result = _run_script(
        scripts_base_dir / "inventory-overview" / "scripts" / "inventory_turnover.py",
        ["--input", str(e2e_dir / "extracted_summary.parquet"),
         "--weekly", str(e2e_dir / "extracted_weekly.parquet"),
         "--output", str(e2e_dir / "efficiency_cost_report.json")],
    )
    assert result.returncode == 0, f"inventory_turnover 失败: {result.stderr}"

    result = _run_script(
        scripts_base_dir / "inventory-overview" / "scripts" / "cost_analyzer.py",
        ["--input", str(e2e_dir / "extracted_summary.parquet"),
         "--output", str(e2e_dir / "efficiency_cost_report.json"),
         "--append"],
    )
    assert result.returncode == 0, f"cost_analyzer 失败: {result.stderr}"

    # ── Skill 03: category-classifier ──
    print("\n[3/7] category-classifier")
    result = _run_script(
        scripts_base_dir / "category-classifier" / "scripts" / "abc_classifier.py",
        ["--input", str(e2e_dir / "extracted_summary.parquet"),
         "--output", str(e2e_dir / "abc_xyz_result.json")],
    )
    assert result.returncode == 0, f"abc_classifier 失败: {result.stderr}"

    result = _run_script(
        scripts_base_dir / "category-classifier" / "scripts" / "xyz_classifier.py",
        ["--input", str(e2e_dir / "extracted_weekly.parquet"),
         "--output", str(e2e_dir / "abc_xyz_result.json"),
         "--append"],
    )
    assert result.returncode == 0, f"xyz_classifier 失败: {result.stderr}"

    # ── Skill 04: supplier-analyzer ──
    print("\n[4/7] supplier-analyzer")
    result = _run_script(
        scripts_base_dir / "supplier-analyzer" / "scripts" / "supplier_evaluator.py",
        ["--input", str(e2e_dir / "extracted_summary.parquet"),
         "--output", str(e2e_dir / "supplier_report.json")],
    )
    assert result.returncode == 0, f"supplier_evaluator 失败: {result.stderr}"

    # ── Skill 05: supply-demand-matcher ──
    print("\n[5/7] supply-demand-matcher")
    demand_path: Path = e2e_dir / "demand_data.json"
    demand_data: list[dict[str, Any]] = [
        {"物料编码": "GSN-0001", "需求量": 5000},
        {"物料编码": "GSN-0002", "需求量": 3500},
        {"物料编码": "GSN-0003", "需求量": 15000},
        {"物料编码": "GSN-0004", "需求量": 500},
        {"物料编码": "GSN-0005", "需求量": 8000},
    ]
    with open(demand_path, "w", encoding="utf-8") as fp:
        json.dump(demand_data, fp, ensure_ascii=False, indent=2)

    result = _run_script(
        scripts_base_dir / "supply-demand-matcher" / "scripts" / "supply_demand_matcher.py",
        ["--supply", str(e2e_dir / "extracted_summary.parquet"),
         "--demand", str(demand_path),
         "--output", str(e2e_dir / "supply_demand_gap.json")],
    )
    assert result.returncode == 0, f"supply_demand_matcher 失败: {result.stderr}"

    # ── Skill 06: inventory-planner（含调参→预测链路）──
    print("\n[6/7] inventory-planner")

    # 步骤 6a：调参
    print("  执行 hyperparameter_tuner.py ...")
    optimal_params_path: Path = e2e_dir / "optimal_params.json"
    result = _run_script(
        scripts_base_dir / "inventory-planner" / "scripts" / "hyperparameter_tuner.py",
        ["--input", str(e2e_dir / "extracted_weekly.parquet"),
         "--output", str(optimal_params_path),
         "--method", "auto",
         "--workers", "2"],
    )
    assert result.returncode == 0, f"hyperparameter_tuner 失败: {result.stderr}"
    assert optimal_params_path.exists(), "optimal_params.json 未生成"

    # 步骤 6b：使用最优参数预测
    print("  使用最优参数执行 demand_forecast.py ...")
    result = _run_script(
        scripts_base_dir / "inventory-planner" / "scripts" / "demand_forecast.py",
        ["--input", str(e2e_dir / "extracted_weekly.parquet"),
         "--output", str(e2e_dir / "forecast_result.json"),
         "--optimal-params", str(optimal_params_path)],
    )
    assert result.returncode == 0, f"demand_forecast 失败: {result.stderr}"

    # 验证参数来源
    with open(e2e_dir / "forecast_result.json", "r", encoding="utf-8") as fp:
        forecast_data: dict[str, Any] = json.load(fp)
    assert forecast_data["demand_forecast"]["parameter_source"] == "optimal_params", (
        "参数来源未标记为 optimal_params"
    )

    result = _run_script(
        scripts_base_dir / "inventory-planner" / "scripts" / "inventory_planning.py",
        ["--data", str(e2e_dir / "extracted_weekly.parquet"),
         "--summary", str(e2e_dir / "extracted_summary.parquet"),
         "--classification", str(e2e_dir / "abc_xyz_result.json"),
         "--forecast", str(e2e_dir / "forecast_result.json"),
         "--output", str(e2e_dir / "inventory_plan.json")],
    )
    assert result.returncode == 0, f"inventory_planning 失败: {result.stderr}"

    result = _run_script(
        scripts_base_dir / "inventory-planner" / "scripts" / "inventory_alert.py",
        ["--data", str(e2e_dir / "extracted_weekly.parquet"),
         "--plan", str(e2e_dir / "inventory_plan.json"),
         "--summary", str(e2e_dir / "extracted_summary.parquet"),
         "--output", str(e2e_dir / "alert_list.json")],
    )
    assert result.returncode == 0, f"inventory_alert 失败: {result.stderr}"

    # ── Skill 07: purchase-advisor ──
    print("\n[7/7] purchase-advisor")
    result = _run_script(
        scripts_base_dir / "purchase-advisor" / "scripts" / "purchase_planner.py",
        ["--alerts", str(e2e_dir / "alert_list.json"),
         "--supply-demand", str(e2e_dir / "supply_demand_gap.json"),
         "--inventory-plan", str(e2e_dir / "inventory_plan.json"),
         "--output", str(e2e_dir / "purchase_plan.json")],
    )
    assert result.returncode == 0, f"purchase_planner 失败: {result.stderr}"

    result = _run_script(
        scripts_base_dir / "purchase-advisor" / "scripts" / "report_generator.py",
        ["--project-dir", str(e2e_dir),
         "--output", str(e2e_dir / "final_report.json")],
    )
    assert result.returncode == 0, f"report_generator 失败: {result.stderr}"

    # =====================================================================
    # 业务内容验证（12 项）
    # =====================================================================

    # ── 验证 1：汇总数据行数 = 30（排除合计行后）──
    summary_df: pl.DataFrame = pl.read_parquet(e2e_dir / "extracted_summary.parquet")
    assert summary_df.height == 30, (
        f"验证1失败: 期望 30 行（排除合计行后）, 实际 {summary_df.height}"
    )

    # ── 验证 2：平衡校验通过率 > 90% ──
    summary_df = summary_df.with_columns(
        (pl.col("库存量") + pl.col("入库数量")
         - pl.col("出库数量") - pl.col("结存数量")).abs().alias("balance_diff")
    )
    balanced_count: int = summary_df.filter(pl.col("balance_diff") < 0.01).height
    balance_rate: float = balanced_count / summary_df.height * 100
    assert balance_rate > 90, (
        f"验证2失败: 平衡校验通过率 {balance_rate:.1f}% < 90%"
    )

    # ── 验证 3：ABC 分类总和 = 总物料数 ──
    with open(e2e_dir / "abc_xyz_result.json", "r", encoding="utf-8") as fp:
        abc_xyz_data: dict[str, Any] = json.load(fp)
    abc: dict[str, Any] = abc_xyz_data["abc_classification"]
    assert abc["a_count"] + abc["b_count"] + abc["c_count"] == abc["total_items"], (
        f"验证3失败: A+B+C ≠ {abc['total_items']}"
    )

    # ── 验证 4：XYZ 分类总和 = 总物料数 ──
    xyz: dict[str, Any] = abc_xyz_data["xyz_classification"]
    xyz_sum: int = xyz["x_count"] + xyz["y_count"] + xyz["z_count"] + xyz["insufficient_data_count"]
    assert xyz_sum == xyz["total_items"], (
        f"验证4失败: X+Y+Z+不足 ≠ {xyz['total_items']}"
    )

    # ── 验证 5：AX 类服务水平 = 99% ──
    with open(e2e_dir / "inventory_plan.json", "r", encoding="utf-8") as fp:
        plan_data: dict[str, Any] = json.load(fp)
    ax_items: list[dict] = [
        item for item in plan_data["inventory_plan"]["item_details"]
        if item["ABC-XYZ分类"] == "AX"
    ]
    if ax_items:
        for item in ax_items:
            assert item["服务水平"] == 0.99, (
                f"验证5失败: AX 物料 {item['物料编码']} 服务水平={item['服务水平']}"
            )

    # ── 验证 6：A 类中 X 类安全库存 < Z 类安全库存 ──
    a_x_items: list[float] = [
        item["安全库存"] for item in plan_data["inventory_plan"]["item_details"]
        if item["ABC-XYZ分类"] == "AX"
    ]
    a_z_items: list[float] = [
        item["安全库存"] for item in plan_data["inventory_plan"]["item_details"]
        if item["ABC-XYZ分类"] == "AZ"
    ]
    if a_x_items and a_z_items:
        avg_ax_ss: float = sum(a_x_items) / len(a_x_items)
        avg_az_ss: float = sum(a_z_items) / len(a_z_items)
        assert avg_ax_ss < avg_az_ss, (
            f"验证6失败: AX 平均安全库存 {avg_ax_ss:.2f} ≥ AZ {avg_az_ss:.2f}"
        )

    # ── 验证 7：AX 补货策略 = "定期定量" ──
    if ax_items:
        for item in ax_items:
            policy_type: str = item.get("补货策略类型", "")
            assert "定期定量" in policy_type, (
                f"验证7失败: AX 物料 {item['物料编码']} 补货策略={policy_type}"
            )

    # ── 验证 8：采购计划按优先级降序 ──
    with open(e2e_dir / "purchase_plan.json", "r", encoding="utf-8") as fp:
        purchase_data: dict[str, Any] = json.load(fp)
    purchase_items: list[dict] = purchase_data["purchase_plan"].get("purchase_items", [])
    if purchase_items:
        scores: list[int] = [item["优先级分数"] for item in purchase_items]
        assert scores == sorted(scores, reverse=True), (
            "验证8失败: 采购计划未按优先级降序排列"
        )

    # ── 验证 9：预测结果包含 MAE/RMSE/Bias ──
    first_item: dict = forecast_data["demand_forecast"]["item_details"][0]
    assert "MAE" in first_item and "RMSE" in first_item and "Bias" in first_item, (
        f"验证9失败: 预测结果缺少 MAE/RMSE/Bias"
    )

    # ── 验证 10：action_history 非空 ──
    history_path: Path = e2e_dir / "action_history.json"
    if history_path.exists():
        with open(history_path, "r", encoding="utf-8") as fp:
            history_data: dict[str, Any] = json.load(fp)
        assert len(history_data.get("history", [])) >= 1, (
            "验证10失败: action_history 为空"
        )

    # ── 验证 11：final_report 存在且包含 executive_summary ──
    final_path: Path = e2e_dir / "final_report.json"
    assert final_path.exists(), "验证11失败: final_report.json 不存在"
    with open(final_path, "r", encoding="utf-8") as fp:
        final_data: dict[str, Any] = json.load(fp)
    assert "executive_summary" in final_data, (
        "验证11失败: final_report 缺少 executive_summary"
    )

    # ── 验证 12：所有产出文件在固定路径下可见 ──
    expected_files: list[str] = [
        "extracted_summary.parquet",
        "extracted_weekly.parquet",
        "inventory_overview.json",
        "efficiency_cost_report.json",
        "abc_xyz_result.json",
        "alert_list.json",
        "purchase_plan.json",
        "final_report.json",
        "optimal_params.json",
        "error_report.json",
    ]
    for filename in expected_files:
        file_path: Path = e2e_dir / filename
        assert file_path.exists(), (
            f"验证12失败: {filename} 不存在于 {e2e_dir}"
        )

    print(f"\n✅ 端到端集成测试通过！全部 12 项业务验证通过。")
    print(f"   产出文件路径: {e2e_dir}")