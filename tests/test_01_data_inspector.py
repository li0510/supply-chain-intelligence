"""
数据验表与探查测试 (test_01_data_inspector.py)

验证 data-inspector 子 Skill 的全部脚本。

v2.0.0 更新：
    - test_data_extractor 适配双输出模式 + 生命周期字段（列数 ≥ 5）
    - test_data_validator 输入路径更新为 extracted_summary.parquet
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
# 测试 1.1：数据结构分析
# ============================================================================

def test_data_profiler_execution(
    scripts_base_dir: Path,
    raw_data_file: Path,
    output_dir: Path,
) -> None:
    """测试 data_profiler.py 能否成功执行并产出正确的结构报告。"""
    script_path: Path = scripts_base_dir / "data-inspector" / "scripts" / "data_profiler.py"

    result: subprocess.CompletedProcess = subprocess.run(
        [
            sys.executable, str(script_path),
            "--input", str(raw_data_file.parent),
            "--output", str(output_dir),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"data_profiler.py 执行失败。\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )

    profile_path: Path = output_dir / "raw_data_profile.json"
    assert profile_path.exists(), f"产出文件不存在: {profile_path}"

    with open(profile_path, "r", encoding="utf-8") as fp:
        data: dict[str, Any] = json.load(fp)

    assert "file_reports" in data, "缺少 file_reports 字段"
    assert len(data["file_reports"]) > 0, "file_reports 为空"

    report: dict[str, Any] = data["file_reports"][0]
    assert report["total_rows"] > 0, "行数为 0"
    assert report["total_columns"] > 0, "列数为 0"
    assert report["has_multi_header"] is True, (
        f"应检测到多行表头，实际: {report['has_multi_header']}"
    )
    assert len(report["possible_header_rows"]) > 0, "未检测到表头行"


# ============================================================================
# 测试 1.2：列映射
# ============================================================================

def test_column_mapper_exact_match(scripts_base_dir: Path, output_dir: Path) -> None:
    """测试 column_mapper.py 的精确匹配功能。"""
    script_path: Path = scripts_base_dir / "data-inspector" / "scripts" / "column_mapper.py"

    columns_json: str = json.dumps(
        ["物料编码", "库存量", "入库数量", "出库数量", "结存数量"],
        ensure_ascii=False,
    )
    output_path: Path = output_dir / "column_mapping_exact.json"

    result: subprocess.CompletedProcess = subprocess.run(
        [
            sys.executable, str(script_path),
            "--columns", columns_json,
            "--output", str(output_path),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"列映射失败: {result.stderr}"

    with open(output_path, "r", encoding="utf-8") as fp:
        data: dict[str, Any] = json.load(fp)

    assert data["status"] == "success", f"期望 success, 实际 {data['status']}"
    assert len(data["mapping"]) == 5, f"期望 5 个映射, 实际 {len(data['mapping'])}"


def test_column_mapper_alias_match(scripts_base_dir: Path, output_dir: Path) -> None:
    """测试 column_mapper.py 的别名匹配功能。"""
    script_path: Path = scripts_base_dir / "data-inspector" / "scripts" / "column_mapper.py"

    columns_json: str = json.dumps(
        ["分类", "编号", "品名", "期初库存", "本期入库", "本期出库", "期末库存"],
        ensure_ascii=False,
    )
    output_path: Path = output_dir / "column_mapping_alias.json"

    result: subprocess.CompletedProcess = subprocess.run(
        [
            sys.executable, str(script_path),
            "--columns", columns_json,
            "--output", str(output_path),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"列映射失败: {result.stderr}"

    with open(output_path, "r", encoding="utf-8") as fp:
        data: dict[str, Any] = json.load(fp)

    assert data["status"] == "partial", f"期望 partial, 实际 {data['status']}"
    assert data["mapping"].get("库存量") == "期初库存", "期初库存应映射到库存量"
    assert data["mapping"].get("入库数量") == "本期入库", "本期入库应映射到入库数量"
    assert data["mapping"].get("出库数量") == "本期出库", "本期出库应映射到出库数量"
    assert data["mapping"].get("结存数量") == "期末库存", "期末库存应映射到结存数量"


# ============================================================================
# 测试 1.3：字段提取（双输出模式 + 生命周期字段）
# ============================================================================

def test_data_extractor(
    scripts_base_dir: Path,
    raw_data_file: Path,
    output_dir: Path,
    column_mapping: dict[str, str],
) -> None:
    """测试 data_extractor.py 的字段提取和类型转换（双输出模式）。"""
    script_path: Path = scripts_base_dir / "data-inspector" / "scripts" / "data_extractor.py"

    result: subprocess.CompletedProcess = subprocess.run(
        [
            sys.executable, str(script_path),
            "--input", str(raw_data_file.parent),
            "--output", str(output_dir),
            "--column-mapping", json.dumps(column_mapping, ensure_ascii=False),
            "--header-row", "1",
            "--data-start-row", "2",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"data_extractor.py 执行失败。\nstderr: {result.stderr}"
    )

    # 断言：双输出文件均存在
    summary_path: Path = output_dir / "extracted_summary.parquet"
    weekly_path: Path = output_dir / "extracted_weekly.parquet"
    assert summary_path.exists(), f"汇总文件不存在: {summary_path}"
    assert weekly_path.exists(), f"周度文件不存在: {weekly_path}"

    # 断言：汇总数据内容正确（至少包含 5 个核心字段 + 生命周期字段）
    summary_df: pl.DataFrame = pl.read_parquet(summary_path)
    # 排除合计行后，实际物料数为 30
    assert summary_df.height == 30, f"期望 30 行汇总数据（排除合计行后）, 实际 {summary_df.height}"
    assert summary_df.width >= 5, f"期望至少 5 列, 实际 {summary_df.width}"
    core_fields: set[str] = {"物料编码", "库存量", "入库数量", "出库数量", "结存数量"}
    assert core_fields.issubset(set(summary_df.columns)), (
        f"缺少核心字段: {core_fields - set(summary_df.columns)}"
    )

    # 断言：周度数据内容正确
    weekly_df: pl.DataFrame = pl.read_parquet(weekly_path)
    assert weekly_df.height > 0, "周度数据为空"
    assert weekly_df.width == 5, f"期望 5 列, 实际 {weekly_df.width}"
    assert set(weekly_df.columns) == {"物料编码", "ISO_Week", "周入库量", "周出库量", "周结存"}, (
        f"列名不匹配: {weekly_df.columns}"
    )

    # 断言：错误报告存在
    error_path: Path = output_dir / "error_report.json"
    assert error_path.exists(), f"错误报告不存在: {error_path}"


# ============================================================================
# 测试 1.4：数据质量检查
# ============================================================================

def test_data_validator(
    scripts_base_dir: Path,
    output_dir: Path,
) -> None:
    """测试 data_validator.py 的平衡校验和异常检测。"""
    script_path: Path = scripts_base_dir / "data-inspector" / "scripts" / "data_validator.py"
    summary_path: Path = output_dir / "extracted_summary.parquet"

    assert summary_path.exists(), (
        f"前置条件不满足: {summary_path} 不存在。请先运行 test_data_extractor。"
    )

    result: subprocess.CompletedProcess = subprocess.run(
        [
            sys.executable, str(script_path),
            "--input", str(summary_path),
            "--output", str(output_dir),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"data_validator.py 执行失败。\nstderr: {result.stderr}"
    )

    report_path: Path = output_dir / "validation_report.json"
    assert report_path.exists(), f"验证报告不存在: {report_path}"

    with open(report_path, "r", encoding="utf-8") as fp:
        data: dict[str, Any] = json.load(fp)

    assert "imbalance_count" in data, "缺少 imbalance_count 字段"
    assert "outlier_count" in data, "缺少 outlier_count 字段"