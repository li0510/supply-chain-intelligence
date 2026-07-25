"""
数据结构分析脚本 (data_profiler.py)

供应链智能分析平台 — data-inspector 子 Skill

功能：读取用户提供的原始库存明细文件，分析并输出结构化报告。
     包括列名列表、数据类型推断、行数统计、合并单元格检测、
     多行表头检测、基本统计摘要。

符合 Polars 高性能数据处理原则体系：
    - Lazy API
    - 原生表达式
    - scan_* 替代 read_*

用法:
    uv run data_profiler.py --input <输入文件夹路径> --output <输出目录路径>

作者: Supply Chain Intelligence Team
版本: 0.2.5
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl


# ============================================================================
# 配置
# ============================================================================

STANDARD_FIELDS: list[str] = [
    "物料编码", "库存量", "入库数量", "出库数量", "结存数量"
]

ALIAS_MAP: dict[str, list[str]] = {
    "物料编码": ["原料编码", "物料号", "物料ID", "材料编码", "物代码", "编码"],
    "库存量":   ["库存数量", "期初库存", "当前库存", "库存", "结余库存"],
    "入库数量": ["入库数", "进货数量", "收货数量", "本期入库", "入库"],
    "出库数量": ["出库数", "领用数量", "发货数量", "本期出库", "出库"],
    "结存数量": ["结余数量", "结存", "期末库存", "实际结存", "期末结存"],
}

ENCODING_CANDIDATES: list[str] = [
    "utf-8", "utf-8-sig", "gbk", "gb2312", "latin-1"
]


def normalize_key(s: str) -> str:
    """标准化列名：去空格、小写、去下划线，用于模糊匹配。"""
    return s.replace(" ", "").replace("_", "").lower()


# ============================================================================
# 编码检测
# ============================================================================

def detect_encoding(file_path: Path) -> str:
    """
    检测 CSV 文件的实际编码。

    按优先级尝试候选编码列表，返回第一个能成功解码文件的编码。

    Parameters
    ----------
    file_path : Path
        源文件路径。

    Returns
    -------
    str
        检测到的编码名称。

    Raises
    ------
    ValueError
        所有候选编码都无法成功解码文件时抛出。
    """
    for encoding in ENCODING_CANDIDATES:
        try:
            # 尝试用当前编码读取前 100 行
            _ = pl.read_csv(
                file_path,
                encoding=encoding,
                separator=",",
                has_header=False,
                truncate_ragged_lines=True,
                n_rows=100,
            )
            return encoding
        except (UnicodeDecodeError, Exception):
            continue

    raise ValueError(
        f"无法自动检测文件编码。已尝试: {ENCODING_CANDIDATES}。\n"
        f"请确认文件编码格式，或手动指定编码参数。\n"
        f"文件路径: {file_path}"
    )


# ============================================================================
# 主函数
# ============================================================================

def profile_file(file_path: Path) -> dict[str, Any]:
    """
    分析单个文件的数据结构。

    Parameters
    ----------
    file_path : Path
        源文件路径。

    Returns
    -------
    dict[str, Any]
        结构分析报告字典。
    """
    suffix: str = file_path.suffix.lower()

    # ── 编码检测 ──
    detected_encoding: str = "gbk"  # 默认值
    if suffix == ".csv":
        try:
            detected_encoding = detect_encoding(file_path)
            print(f"检测到文件编码: {detected_encoding}")
        except ValueError as e:
            print(f"警告: {e}")
            print("将使用默认 GBK 编码尝试读取...")
            detected_encoding = "gbk"

    # ── 读取前 50 行用于分析 ──
    if suffix == ".csv":
        preview_df: pl.DataFrame = pl.read_csv(
            file_path,
            encoding=detected_encoding,
            separator=",",
            has_header=False,
            truncate_ragged_lines=True,
            n_rows=50,
        )
    elif suffix in (".xlsx", ".xls"):
        preview_df = pl.read_excel(file_path, sheet_id=1, has_header=False).head(50)
    else:
        raise ValueError(f"不支持的文件格式: {suffix}")

    total_rows: int = preview_df.height
    total_cols: int = preview_df.width
    columns_raw: list[str] = [str(c) if c is not None else "" for c in preview_df.row(0)]

    # ── 检测多行表头 ──
    has_multi_header: bool = False
    possible_header_rows: list[int] = []
    for i in range(min(10, total_rows)):
        row_values: list[str] = [
            str(v) if v is not None else "" for v in preview_df.row(i)
        ]
        norm_values: set[str] = {normalize_key(v) for v in row_values if v}
        match_count: int = 0
        for std in STANDARD_FIELDS:
            if normalize_key(std) in norm_values:
                match_count += 1
                continue
            for alias in ALIAS_MAP[std]:
                if normalize_key(alias) in norm_values:
                    match_count += 1
                    break
        if match_count >= 3:
            possible_header_rows.append(i)

    # 模式 A：多个包含标准字段的行
    if len(possible_header_rows) > 1:
        has_multi_header = True
    # 模式 B：第一个匹配行之前存在非空标题行
    elif len(possible_header_rows) == 1:
        first_match_row: int = possible_header_rows[0]
        # 检查第一个匹配行之前的行
        for i in range(first_match_row):
            row_values: list[str] = [
                str(v) if v is not None else "" for v in preview_df.row(i)
            ]
            # 如果该行有非空单元格且不是纯数字行（不是数据行），认为是标题行
            non_empty_count: int = sum(1 for v in row_values if v.strip())
            numeric_count: int = 0
            for v in row_values:
                if v.strip():
                    try:
                        float(v)
                        numeric_count += 1
                    except ValueError:
                        pass
            # 该行有非空内容，且大部分单元格不是纯数字（即不是数据行）
            if non_empty_count > 0 and numeric_count < non_empty_count * 0.5:
                has_multi_header = True
                break

    # ── 检测合并单元格（检查 null 值分布） ──
    null_cells: int = 0
    for col_idx in range(total_cols):
        null_cells += preview_df[:, col_idx].null_count()
    has_merged_cells: bool = null_cells > (total_rows * total_cols * 0.1)

    # ── 推断数据类型 ──
    type_inference: dict[str, str] = {}
    for col_idx in range(total_cols):
        col_name: str = str(preview_df[:, col_idx].name)
        series: pl.Series = preview_df[:, col_idx]
        non_null: pl.Series = series.drop_nulls()
        if non_null.len() == 0:
            type_inference[col_name] = "unknown"
            continue
        try:
            non_null.cast(pl.Float64, strict=True)
            type_inference[col_name] = "numeric"
        except Exception:
            try:
                non_null.cast(pl.Date, strict=True)
                type_inference[col_name] = "date"
            except Exception:
                type_inference[col_name] = "text"

    # ── 构建报告 ──
    report: dict[str, Any] = {
        "file_name": file_path.name,
        "file_format": suffix,
        "detected_encoding": detected_encoding,
        "total_rows": total_rows,
        "total_columns": total_cols,
        "raw_columns": columns_raw,
        "has_multi_header": has_multi_header,
        "possible_header_rows": possible_header_rows,
        "has_merged_cells": has_merged_cells,
        "type_inference": type_inference,
        "analyzed_at": datetime.now().isoformat(),
    }
    return report


def main() -> None:
    """命令行入口，分析输入文件夹中所有文件的数据结构。"""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="数据结构分析 — 供应链智能分析平台"
    )
    parser.add_argument("--input", required=True, help="输入文件夹路径")
    parser.add_argument("--output", required=True, help="输出目录路径")
    args: argparse.Namespace = parser.parse_args()

    input_dir: Path = Path(args.input)
    output_dir: Path = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    files: list[Path] = sorted(
        list(input_dir.glob("*.csv"))
        + list(input_dir.glob("*.xlsx"))
        + list(input_dir.glob("*.xls"))
    )
    if not files:
        print(f"错误: 在 {input_dir} 中未找到 CSV 或 Excel 文件")
        sys.exit(1)

    all_reports: list[dict[str, Any]] = []
    for f in files:
        print(f"分析文件: {f.name}")
        report: dict[str, Any] = profile_file(f)
        all_reports.append(report)

    # ── 汇总报告 ──
    summary: dict[str, Any] = {
        "analyzed_at": datetime.now().isoformat(),
        "total_files": len(files),
        "file_reports": all_reports,
    }

    output_path: Path = output_dir / "raw_data_profile.json"
    with open(output_path, "w", encoding="utf-8") as fp:
        json.dump(summary, fp, ensure_ascii=False, indent=2)
    print(f"结构分析报告已保存: {output_path}")


if __name__ == "__main__":
    main()