"""
字段提取与数据准备脚本 (data_extractor.py)

供应链智能分析平台 — data-inspector 子 Skill

功能：从用户提供的原始库存明细文件（宽表格式）中提取关键字段，
     输出双文件：
     1. extracted_summary.parquet — 物料级别的汇总数据（5列 + 生命周期字段）
     2. extracted_weekly.parquet — 物料 × ISO Week 的周度明细数据（5列）

支持格式：CSV（自动检测编码）、Excel（.xlsx/.xls）
宽表结构要求：左侧固定列为物料主数据（物料编码等），
              右侧为按日期展开的每日入库/出库/结存三元组列。
              ** 支持多级表头 **：第 0 行为日期/标签行，第 1 行为业务动作行。

新增功能：
    - 支持通过 --material-master 加载物料主数据文件（生命周期字段）
    - 支持通过 --lifecycle-config 传入 JSON 格式的生命周期配置
    - 无物料主数据时使用默认值（正常在售，无保质期约束）
    - 自动检测并排除尾部合计行（跨列关键词检测，默认前七列）

更新内容：
    - 删除硬编码的 STANDARD_CODE_PATTERN（GSN-XXXXX 格式依赖）
    - _build_exclusion_mask 改为跨列关键词检测（默认前七列）
    - 新增 --exclude-keywords 和 --exclude-columns 可选参数
    - 适配任意 ERP 系统的物料编码格式

高性能设计（企业级千万级数据量适配）：
    - 不使用全量列重命名（避免重复列名冲突）
    - 通过业务动作行索引定位列，配合 pl.col().alias() 精确选择
    - 单次 collect 完成全部数据提取

符合 Polars 高性能数据处理原则体系：
    - Lazy API + 流式引擎（单次 collect）
    - 原生表达式，零 Python 循环
    - scan_* 替代 read_*
    - concat_dataframes_stream + 生成器模式

用法:
    uv run data_extractor.py --input <输入文件夹路径> --output <输出目录路径> \
      [--column-mapping <JSON映射>] [--header-row <表头行号>] \
      [--data-start-row <数据起始行号>] \
      [--material-master <物料主数据文件路径>] \
      [--lifecycle-config <JSON生命周期配置>] \
      [--exclude-keywords <自定义排除关键词>] \
      [--exclude-columns <自定义检测列名>]

作者: Supply Chain Intelligence Team
版本: 0.2.5
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Generator

import polars as pl

# 导入公共工具模块
from utils import detect_encoding, read_csv_lazy, concat_dataframes_stream


# ============================================================================
# 配置
# ============================================================================

STANDARD_FIELDS: list[str] = [
    "物料编码", "库存量", "入库数量", "出库数量", "结存数量"
]

NUMERIC_FIELDS: list[str] = [
    "库存量", "入库数量", "出库数量", "结存数量"
]

# 宽表日期列的正则模式：匹配 "M/D" 或 "MM/DD" 格式
DATE_PATTERN: re.Pattern = re.compile(r"^\d{1,2}/\d{1,2}$")

# 宽表中每日三元组的业务动作列名（按顺序：入库、出库、结存）
DAILY_TRIPLET: list[str] = ["入库", "出库", "结存"]

# 13 周滚动窗口配置
EXPECTED_WEEKS: int = 13

# ── 物料主数据生命周期字段（中文字段名）──
LIFECYCLE_FIELDS: list[str] = [
    "生命周期状态",
    "保质期天数",
    "生产日期",
    "过期日期",
    "剩余保质期天数",
    "新品上市日期",
    "老品下市日期",
]

LIFECYCLE_DEFAULTS: dict[str, Any] = {
    "生命周期状态": "正常在售",
    "保质期天数": None,
    "生产日期": None,
    "过期日期": None,
    "剩余保质期天数": None,
    "新品上市日期": None,
    "老品下市日期": None,
}

# 尾部合计行检测关键词（默认值）
SUMMARY_KEYWORDS: list[str] = ["合计", "总计", "小计"]

# 默认检测列数（前 N 列用于关键词检测）
DEFAULT_DETECT_COLUMNS_COUNT: int = 7


# ============================================================================
# 日期标签提取
# ============================================================================

def _extract_date_label(raw: str) -> str:
    """
    从 Polars 读取的日期行单元格值中提取日期标签。

    Polars 读取 CSV 时可能将日期值读取为以下格式之一：
        - 原始 "9/1" 格式（M/D）
        - 带 BOM 的 "\\ufeff9/1"
        - 完整 datetime 字符串 "2021-09-01 00:00:00"
        - 日期字符串 "2021-09-01"

    本函数统一将其转换为 "M/D" 格式（如 "9/1"），
    如果无法识别则返回空字符串。

    Parameters
    ----------
    raw : str
        原始单元格值。

    Returns
    -------
    str
        日期标签（如 "9/1"、"本月累计"、空字符串）。
    """
    if not raw:
        return ""

    raw = raw.lstrip("\ufeff").strip()

    if DATE_PATTERN.match(raw):
        return raw

    try:
        dt: datetime = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        return f"{dt.month}/{dt.day}"
    except ValueError:
        pass

    try:
        dt = datetime.strptime(raw, "%Y-%m-%d")
        return f"{dt.month}/{dt.day}"
    except ValueError:
        pass

    return raw


# ============================================================================
# 宽表列解析（多级表头版本）
# ============================================================================

def parse_wide_columns(
    date_row: list[str],
    action_row: list[str],
) -> dict[str, Any]:
    """
    解析宽表的列结构，使用日期行（第 0 行）和业务动作行（第 1 行）。

    宽表结构（多级表头）：
        第 0 行（日期/标签行）：原料库存明细表, 空, 空, 8/31, 本月累计, 空, 空, 9/1, 9/1, 9/1, 9/2, ...
        第 1 行（业务动作行）：物料分类, 物料编码, 物料名称, 库存, 入库, 出库, 结存, 入库, 出库, 结存, ...
        第 2 行起：数据行

    Parameters
    ----------
    date_row : list[str]
        第 0 行（日期/标签行），包含 "8/31"、"本月累计"、"9/1" 等。
    action_row : list[str]
        第 1 行（业务动作行），包含 "库存"、"入库"、"出库"、"结存" 等。

    Returns
    -------
    dict[str, Any]
        {
            "fixed_columns": {"物料分类": 0, "物料编码": 1, "物料名称": 2},
            "opening_stock_col": 3,
            "monthly_summary_start": 4,
            "daily_blocks": [
                {"date": "9/1", "in_col": 7, "out_col": 8, "balance_col": 9},
                ...
            ]
        }
    """
    result: dict[str, Any] = {
        "fixed_columns": {"物料分类": 0, "物料编码": 1, "物料名称": 2},
        "opening_stock_col": None,
        "monthly_summary_start": None,
        "daily_blocks": [],
    }

    total_cols: int = len(action_row)

    if 3 < total_cols and action_row[3] == "库存":
        result["opening_stock_col"] = 3

    i: int = 4
    while i < total_cols:
        date_label_raw: str = date_row[i] if i < len(date_row) else ""
        action_label: str = action_row[i] if i < len(action_row) else ""

        date_clean: str = _extract_date_label(date_label_raw)

        if date_clean == "本月累计" and i + 2 < total_cols:
            result["monthly_summary_start"] = i
            i += 3
            continue

        if DATE_PATTERN.match(date_clean) and i + 2 < total_cols:
            daily_block: dict[str, Any] = {
                "date": date_clean,
                "in_col": i,
                "out_col": i + 1,
                "balance_col": i + 2,
            }
            result["daily_blocks"].append(daily_block)
            i += 3
            continue

        i += 1

    return result


# ============================================================================
# 汇总数据提取（索引定位 + alias 精确选择，避免重复列错误）
# ============================================================================

def extract_summary(
    lf: pl.LazyFrame,
    column_mapping: dict[str, str],
    action_row: list[str],
    original_columns: list[str],
    dsr: int,
) -> pl.DataFrame:
    """
    从宽表中提取物料编码 + 期初库存 + 本月累计入库/出库/结存，
    输出物料级别的汇总数据。

    不使用全量列重命名，而是通过 action_row 定位每个标准字段
    首次出现的列索引，然后用 pl.col(original_columns[idx]).alias(field)
    进行精确列选择和重命名。这从根本上避免了业务动作行中重复值
    （如多个"入库"）导致的重命名冲突。

    Parameters
    ----------
    lf : pl.LazyFrame
        惰性 DataFrame（使用 Polars 生成的原始列名）。
    column_mapping : dict[str, str]
        列映射表 {标准字段: 源文件列名}。
    action_row : list[str]
        业务动作行（用于定位列索引）。
    original_columns : list[str]
        Polars 生成的原始列名字符串列表。
    dsr : int
        数据起始行号（0-based）。

    Returns
    -------
    pl.DataFrame
        汇总后的 Eager DataFrame（5列）。

    Raises
    ------
    ValueError
        当无法找到所有标准字段的对应列时抛出。
    """
    lf = lf.slice(dsr)

    select_exprs: list[pl.Expr] = []
    for field in STANDARD_FIELDS:
        target_name: str = column_mapping[field]
        found_idx: int | None = None
        for idx, name in enumerate(action_row):
            if name == target_name:
                found_idx = idx
                break
        if found_idx is not None and found_idx < len(original_columns):
            orig_col: str = original_columns[found_idx]
            select_exprs.append(pl.col(orig_col).alias(field))
        else:
            print(f"警告: 在业务动作行中未找到列 '{target_name}'")

    if len(select_exprs) != len(STANDARD_FIELDS):
        raise ValueError(
            f"无法找到所有标准字段的对应列。"
            f"已找到: {len(select_exprs)}/{len(STANDARD_FIELDS)}"
        )

    lf = lf.select(select_exprs)
    lf = lf.fill_null(strategy="forward")
    lf = lf.with_columns(pl.col("物料编码").cast(pl.Categorical, strict=False))
    for num_col in NUMERIC_FIELDS:
        lf = lf.with_columns(pl.col(num_col).cast(pl.Float32, strict=False))

    return lf.collect(engine="streaming")


# ============================================================================
# 周度数据提取（单次 collect，已使用索引定位）
# ============================================================================

def extract_weekly(
    lf: pl.LazyFrame,
    wide_structure: dict[str, Any],
    dsr: int,
    reference_year: int = 2021,
) -> pl.DataFrame:
    """
    从宽表中一次性提取所有每日列，展开为长表，然后按 ISO Week 聚合。

    本函数使用 wide_structure 中的列索引 + Polars 原始列名来定位列，
    不依赖全量列重命名，因此不受业务动作行重复值的影响。

    Parameters
    ----------
    lf : pl.LazyFrame
        惰性 DataFrame（使用 Polars 生成的原始列名）。
    wide_structure : dict[str, Any]
        宽表列解析结果（来自 parse_wide_columns）。
    dsr : int
        数据起始行号（0-based）。
    reference_year : int
        参考年份，用于解析 "M/D" 格式的日期。默认 2021。

    Returns
    -------
    pl.DataFrame
        周度聚合后的 Eager DataFrame（5列）。
    """
    daily_blocks: list[dict[str, Any]] = wide_structure["daily_blocks"]
    if not daily_blocks:
        print("警告: 未检测到每日流水列，无法生成周度数据。")
        print("请确认原始数据包含日期行（第 0 行）和业务动作行（第 1 行）的多级表头结构。")
        return pl.DataFrame()

    material_col_idx: int = wide_structure["fixed_columns"].get("物料编码", -1)
    if material_col_idx < 0:
        print("警告: 未找到物料编码列，无法生成周度数据。")
        return pl.DataFrame()

    lf = lf.slice(dsr)
    original_columns: list[str] = lf.collect_schema().names()

    select_exprs: list[pl.Expr] = [
        pl.col(original_columns[material_col_idx]).alias("物料编码")
    ]

    for block in daily_blocks:
        date_str: str = block["date"]
        in_col_idx: int = block["in_col"]
        out_col_idx: int = block["out_col"]
        balance_col_idx: int = block["balance_col"]

        in_col_name: str = (
            original_columns[in_col_idx]
            if in_col_idx < len(original_columns)
            else ""
        )
        out_col_name: str = (
            original_columns[out_col_idx]
            if out_col_idx < len(original_columns)
            else ""
        )
        balance_col_name: str = (
            original_columns[balance_col_idx]
            if balance_col_idx < len(original_columns)
            else ""
        )

        if not in_col_name:
            continue

        in_alias: str = f"_in_{date_str}"
        out_alias: str = f"_out_{date_str}"
        balance_alias: str = f"_balance_{date_str}"

        select_exprs.append(
            pl.col(in_col_name).cast(pl.Float32, strict=False).alias(in_alias)
        )
        select_exprs.append(
            pl.col(out_col_name).cast(pl.Float32, strict=False).alias(out_alias)
        )
        select_exprs.append(
            pl.col(balance_col_name).cast(pl.Float32, strict=False).alias(balance_alias)
        )

    all_daily_df: pl.DataFrame = lf.select(select_exprs).collect(engine="streaming")

    def _gen_daily_long() -> Generator[pl.DataFrame, None, None]:
        for block in daily_blocks:
            date_str: str = block["date"]
            in_alias: str = f"_in_{date_str}"
            out_alias: str = f"_out_{date_str}"
            balance_alias: str = f"_balance_{date_str}"

            month_day: list[str] = date_str.split("/")
            if len(month_day) != 2:
                continue
            month: int = int(month_day[0])
            day: int = int(month_day[1])
            parsed_date: datetime = datetime(reference_year, month, day)

            day_df: pl.DataFrame = all_daily_df.select([
                pl.col("物料编码"),
                pl.col(in_alias).alias("当日入库"),
                pl.col(out_alias).alias("当日出库"),
                pl.col(balance_alias).alias("当日结存"),
            ]).with_columns(pl.lit(parsed_date).alias("日期"))

            yield day_df

    daily_long: pl.DataFrame | None = concat_dataframes_stream(_gen_daily_long())
    if daily_long is None:
        print("警告: 未能从任何日期块中提取每日数据。")
        return pl.DataFrame()

    weekly_agg: pl.DataFrame = daily_long.group_by([
        "物料编码",
        pl.col("日期").dt.week().alias("ISO_Week"),
    ]).agg(
        pl.col("当日入库").sum().alias("周入库量"),
        pl.col("当日出库").sum().alias("周出库量"),
        pl.col("当日结存").last().alias("周结存"),
    ).sort(["物料编码", "ISO_Week"])

    return weekly_agg


# ============================================================================
# 13 周元数据
# ============================================================================

def add_window_metadata(weekly_df: pl.DataFrame) -> pl.DataFrame:
    """
    在 DataFrame 的 schema metadata 中写入 13 周窗口信息，
    并标注第一周和最后一周是否为不完整周。

    Parameters
    ----------
    weekly_df : pl.DataFrame
        周度聚合后的 DataFrame。

    Returns
    -------
    pl.DataFrame
        带有 metadata 的 DataFrame。
    """
    if weekly_df.height == 0:
        return weekly_df

    available_weeks: int = weekly_df["ISO_Week"].n_unique()
    min_week: int = int(weekly_df["ISO_Week"].min())
    max_week: int = int(weekly_df["ISO_Week"].max())

    metadata: dict[str, str] = {
        "available_weeks": str(available_weeks),
        "expected_weeks": str(EXPECTED_WEEKS),
        "window_start": f"W{min_week}",
        "window_end": f"W{max_week}",
    }

    if available_weeks < EXPECTED_WEEKS:
        metadata["warning"] = (
            f"数据不足{EXPECTED_WEEKS}周（实际{available_weeks}周），"
            "预测精度可能受影响。"
        )

    for week_val, label in [(min_week, "first_week"), (max_week, "last_week")]:
        week_data: pl.DataFrame = weekly_df.filter(pl.col("ISO_Week") == week_val)
        daily_count_per_item: pl.DataFrame = week_data.group_by("物料编码").len()
        if daily_count_per_item.height > 0:
            max_days_in_week: int = int(daily_count_per_item["len"].max())
            if max_days_in_week < 7:
                metadata[f"{label}_complete"] = "false"
                metadata[f"{label}_max_days"] = str(max_days_in_week)
                metadata[f"{label}_note"] = (
                    f"第{week_val}周最多仅{max_days_in_week}天数据，"
                    "为不完整周，周度聚合值可能偏低。"
                )
            else:
                metadata[f"{label}_complete"] = "true"

    return weekly_df


# ============================================================================
# 交叉验证
# ============================================================================

def cross_validate(
    summary_df: pl.DataFrame,
    weekly_df: pl.DataFrame,
) -> list[dict[str, Any]]:
    """
    交叉验证汇总数据与每日汇总数据的一致性。

    验证项：
        1. 本月累计入库 == Σ(每日入库)
        2. 本月累计出库 == Σ(每日出库)
        3. 本月累计结存 == 最后一周结存

    Parameters
    ----------
    summary_df : pl.DataFrame
        汇总数据（5列）。
    weekly_df : pl.DataFrame
        周度数据（5列）。

    Returns
    -------
    list[dict[str, Any]]
        不一致项清单。
    """
    issues: list[dict[str, Any]] = []

    if weekly_df.height == 0:
        return issues

    summary_df = summary_df.with_columns(
        pl.col("物料编码").cast(pl.Utf8)
    )

    weekly_agg: pl.DataFrame = weekly_df.group_by("物料编码").agg(
        pl.col("周入库量").sum().alias("总入库"),
        pl.col("周出库量").sum().alias("总出库"),
    )

    last_balance_df: pl.DataFrame = (
        weekly_df.sort(["物料编码", "ISO_Week"])
        .group_by("物料编码")
        .agg(pl.col("周结存").last().alias("最后一周结存"))
    )

    joined: pl.DataFrame = summary_df.join(
        weekly_agg, on="物料编码", how="left"
    ).join(
        last_balance_df, on="物料编码", how="left"
    )

    for row in joined.iter_rows(named=True):
        code: str = row["物料编码"]
        summary_in: float = float(row["入库数量"]) if row["入库数量"] is not None else 0.0
        summary_out: float = float(row["出库数量"]) if row["出库数量"] is not None else 0.0
        summary_balance: float = float(row["结存数量"]) if row["结存数量"] is not None else 0.0
        weekly_in: float = float(row["总入库"]) if row["总入库"] is not None else 0.0
        weekly_out: float = float(row["总出库"]) if row["总出库"] is not None else 0.0
        last_week_balance: float = float(row["最后一周结存"]) if row["最后一周结存"] is not None else 0.0

        if abs(summary_in - weekly_in) > 0.01:
            issues.append({
                "物料编码": code,
                "验证项": "本月累计入库 vs Σ每日入库",
                "汇总值": summary_in,
                "每日合计": weekly_in,
                "差异": round(summary_in - weekly_in, 2),
            })

        if abs(summary_out - weekly_out) > 0.01:
            issues.append({
                "物料编码": code,
                "验证项": "本月累计出库 vs Σ每日出库",
                "汇总值": summary_out,
                "每日合计": weekly_out,
                "差异": round(summary_out - weekly_out, 2),
            })

        if abs(summary_balance - last_week_balance) > 0.01:
            issues.append({
                "物料编码": code,
                "验证项": "本月累计结存 vs 最后一周结存",
                "汇总值": summary_balance,
                "最后一周结存": last_week_balance,
                "差异": round(summary_balance - last_week_balance, 2),
            })

    return issues


# ============================================================================
# 尾部合计行 / 异常行检测与排除
# ============================================================================

def _build_exclusion_mask(
    df: pl.DataFrame,
    detect_columns: list[str] | None = None,
    keywords: list[str] | None = None,
) -> pl.Expr:
    """
    构建尾部合计行的排除掩码。

    检测范围：默认前 7 列（或 DataFrame 的总列数），可通过 detect_columns 自定义。
    检测逻辑：任意检测列中包含任意关键词即标记为排除行。
             同时检测物料编码列为空字符串的行。

    不再依赖特定物料编码格式（如 GSN-XXXXX），
    适配任意 ERP 系统的物料编码体系。

    Parameters
    ----------
    df : pl.DataFrame
        待检测的 DataFrame。
    detect_columns : list[str] | None
        要检测关键词的列名列表，默认使用前 7 列。
    keywords : list[str] | None
        要检测的关键词列表，默认 ["合计", "总计", "小计"]。

    Returns
    -------
    pl.Expr
        满足任一排除条件的布尔表达式。
    """
    if keywords is None:
        keywords = SUMMARY_KEYWORDS

    if detect_columns is None:
        detect_columns = df.columns[: min(DEFAULT_DETECT_COLUMNS_COUNT, df.width)]

    # 构建跨列关键词掩码
    keyword_masks: list[pl.Expr] = []
    for col_name in detect_columns:
        if col_name not in df.columns:
            continue
        col_expr: pl.Expr = pl.col(col_name).cast(pl.Utf8)
        for kw in keywords:
            keyword_masks.append(col_expr.str.contains(kw))

    # 物料编码列空字符串检测
    if "物料编码" in df.columns:
        code_expr: pl.Expr = pl.col("物料编码").cast(pl.Utf8)
        keyword_masks.append(code_expr.str.strip_chars() == "")

    if not keyword_masks:
        return pl.lit(False)

    # 用 OR 连接所有条件
    combined_mask: pl.Expr = keyword_masks[0]
    for mask in keyword_masks[1:]:
        combined_mask = combined_mask | mask

    return combined_mask


def _record_excluded_rows(
    df: pl.DataFrame,
    exclusion_mask: pl.Expr,
) -> list[dict[str, Any]]:
    """
    记录被排除的行信息。

    Parameters
    ----------
    df : pl.DataFrame
        原始 DataFrame。
    exclusion_mask : pl.Expr
        排除掩码。

    Returns
    -------
    list[dict[str, Any]]
        被排除行的记录列表。
    """
    excluded_df: pl.DataFrame = df.filter(exclusion_mask)
    if excluded_df.height == 0:
        return []

    records: list[dict[str, Any]] = []
    for row in excluded_df.iter_rows(named=True):
        code_val: str = str(row.get("物料编码", ""))
        reason: str = (
            "物料编码为空字符串"
            if code_val.strip() == ""
            else "跨列关键词检测（合计/总计/小计）"
        )
        records.append({
            "物料编码": code_val,
            "排除原因": reason,
        })
    return records


# ============================================================================
# 物料主数据合并
# ============================================================================

def merge_material_master(
    summary_df: pl.DataFrame,
    material_master_path: Path | None,
    lifecycle_config_json: str | None,
) -> pl.DataFrame:
    """
    将物料主数据（生命周期字段）合并到汇总数据中。

    支持三种获取方式（按优先级）：
        1. 物料主数据文件（--material-master）：Excel/CSV/Parquet
        2. 命令行 JSON 参数（--lifecycle-config）
        3. 默认值（全部物料为"正常在售"，无保质期约束）

    Parameters
    ----------
    summary_df : pl.DataFrame
        汇总数据。
    material_master_path : Path | None
        物料主数据文件路径。
    lifecycle_config_json : str | None
        JSON 格式的生命周期配置。

    Returns
    -------
    pl.DataFrame
        合并了生命周期字段的汇总数据。
    """
    # ── 方式一：物料主数据文件 ──
    if material_master_path is not None and material_master_path.exists():
        suffix: str = material_master_path.suffix.lower()
        if suffix in (".xlsx", ".xls"):
            master_df: pl.DataFrame = pl.read_excel(material_master_path)
        elif suffix == ".csv":
            master_df = pl.read_csv(material_master_path, encoding="utf-8")
        elif suffix == ".parquet":
            master_df = pl.read_parquet(material_master_path)
        else:
            raise ValueError(f"不支持的物料主数据文件格式: {suffix}")

        if "物料编码" not in master_df.columns:
            raise ValueError(
                "物料主数据文件必须包含 '物料编码' 列。"
                f"当前列名: {master_df.columns}"
            )

        available_lifecycle_cols: list[str] = [
            col for col in LIFECYCLE_FIELDS if col in master_df.columns
        ]
        master_df = master_df.select(["物料编码"] + available_lifecycle_cols)

        result: pl.DataFrame = summary_df.join(
            master_df, on="物料编码", how="left"
        )
        print(f"已加载物料主数据: {material_master_path.name} "
              f"({len(available_lifecycle_cols)} 个生命周期字段)")
        return result

    # ── 方式二：JSON 配置 ──
    if lifecycle_config_json is not None:
        config: dict[str, Any] = json.loads(lifecycle_config_json)
        result = summary_df
        for field in LIFECYCLE_FIELDS:
            if field in config:
                result = result.with_columns(
                    pl.lit(config[field]).alias(field)
                )
        print("已应用生命周期配置（JSON）")
        return result

    # ── 方式三：默认值 ──
    result = summary_df
    for field, default_value in LIFECYCLE_DEFAULTS.items():
        result = result.with_columns(
            pl.lit(default_value).alias(field)
        )
    print("未提供物料主数据，使用默认生命周期值（正常在售，无保质期约束）")
    return result


# ============================================================================
# 单文件处理
# ============================================================================

def extract_from_file(
    file_path: Path,
    column_mapping: dict[str, str],
    header_row: int | None,
    data_start_row: int | None,
    exclude_keywords: list[str] | None = None,
    exclude_columns: list[str] | None = None,
) -> tuple[pl.DataFrame | None, pl.DataFrame | None, list[dict[str, Any]], list[dict[str, Any]]]:
    """
    从单个文件中提取汇总数据和周度数据。

    ** 多级表头支持 **：
        第 0 行：日期/标签行（含 "8/31"、"本月累计"、"9/1" 等）
        第 1 行：业务动作行（含 "物料编码"、"库存"、"入库"、"出库"、"结存" 等）
        第 2 行起：数据行

    ** 不使用全量列重命名 **：
        业务动作行中存在重复值（如 32 个"入库"），全量重命名会导致
        Polars DuplicateError。本函数通过 action_row 定位每个标准字段
        首次出现的列索引，在 extract_summary 和 extract_weekly 中
        使用 pl.col(original_columns[idx]).alias(field) 进行精确列选择。

    ** 尾部合计行自动排除 **：
        使用跨列关键词检测（默认前七列），自动排除尾部合计行。
        不依赖特定物料编码格式，适配任意 ERP 系统。

    Parameters
    ----------
    file_path : Path
        源文件路径。
    column_mapping : dict[str, str]
        列映射表 {标准字段: 源文件列名}。
    header_row : int | None
        用户指定的业务动作行行号（0-based），默认 1。
    data_start_row : int | None
        用户指定的数据起始行号（0-based），默认 header_row + 1。
    exclude_keywords : list[str] | None
        自定义排除关键词列表，默认 ["合计", "总计", "小计"]。
    exclude_columns : list[str] | None
        自定义检测列名列表，默认前七列。

    Returns
    -------
    tuple[pl.DataFrame | None, pl.DataFrame | None, list[dict], list[dict]]
        (汇总 DataFrame, 周度 DataFrame, 错误记录, 交叉验证问题)
    """
    suffix: str = file_path.suffix.lower()
    hr: int = header_row if header_row is not None else 1
    dsr: int = data_start_row if data_start_row is not None else (hr + 1)

    if suffix == ".csv":
        detected_encoding: str = "gbk"
        try:
            detected_encoding = detect_encoding(file_path)
            print(f"检测到编码: {detected_encoding}")
        except ValueError:
            print("编码检测失败，使用默认 GBK")

        lf: pl.LazyFrame = read_csv_lazy(file_path, detected_encoding)

        header_df: pl.DataFrame = pl.read_csv(
            file_path,
            encoding=detected_encoding,
            separator=",",
            has_header=False,
            n_rows=hr + 1,
            truncate_ragged_lines=True,
        )
        date_row: list[str] = [
            str(c) if c is not None else ""
            for c in header_df.row(0)
        ]
        action_row: list[str] = [
            str(c) if c is not None else f"col_{i}"
            for i, c in enumerate(header_df.row(hr))
        ]
    elif suffix in (".xlsx", ".xls"):
        eager_df: pl.DataFrame = pl.read_excel(file_path, sheet_id=1, has_header=False)
        lf = eager_df.lazy()

        date_row = [
            str(c) if c is not None else ""
            for c in eager_df.row(0)
        ]
        action_row = [
            str(c) if c is not None else f"col_{i}"
            for i, c in enumerate(eager_df.row(hr))
        ]
    else:
        raise ValueError(f"不支持的文件格式: {suffix}")

    wide_structure: dict[str, Any] = parse_wide_columns(date_row, action_row)
    print(f"解析到 {len(wide_structure['daily_blocks'])} 个每日日期块")

    original_columns: list[str] = lf.collect_schema().names()

    # ── 汇总数据提取 ──
    summary_df: pl.DataFrame | None = None
    try:
        summary_df = extract_summary(
            lf, column_mapping, action_row, original_columns, dsr
        )
        print(f"汇总数据（过滤前）: {summary_df.height} 行")
    except Exception as e:
        print(f"警告: 汇总数据提取失败: {e}")

    # ── 排除尾部合计行（跨列关键词检测）──
    excluded_summary_rows: list[dict[str, Any]] = []
    if summary_df is not None and summary_df.height > 0:
        exclusion_mask: pl.Expr = _build_exclusion_mask(
            summary_df,
            detect_columns=exclude_columns,
            keywords=exclude_keywords,
        )
        excluded_summary_rows = _record_excluded_rows(summary_df, exclusion_mask)
        summary_filtered: pl.DataFrame = summary_df.filter(~exclusion_mask)
        excluded_count: int = summary_df.height - summary_filtered.height
        if excluded_count > 0:
            print(f"已自动排除 {excluded_count} 行尾部合计数据（跨列关键词检测）")
            summary_df = summary_filtered
            print(f"汇总数据（过滤后）: {summary_df.height} 行")

    # ── 周度数据提取 ──
    weekly_df: pl.DataFrame | None = None
    try:
        weekly_df = extract_weekly(lf, wide_structure, dsr)
        if weekly_df.height > 0:
            weekly_exclusion_mask: pl.Expr = _build_exclusion_mask(
                weekly_df,
                detect_columns=exclude_columns,
                keywords=exclude_keywords,
            )
            weekly_df = weekly_df.filter(~weekly_exclusion_mask)
            weekly_df = add_window_metadata(weekly_df)
            print(f"周度数据: {weekly_df.height} 行, "
                  f"{weekly_df['ISO_Week'].n_unique()} 周")
    except Exception as e:
        print(f"警告: 周度数据提取失败: {e}")

    # ── 交叉验证 ──
    validation_issues: list[dict[str, Any]] = []
    if summary_df is not None and weekly_df is not None and weekly_df.height > 0:
        validation_issues = cross_validate(summary_df, weekly_df)
        if validation_issues:
            print(f"交叉验证: {len(validation_issues)} 项不一致")
        else:
            print("交叉验证: 全部通过")

    # ── 错误报告 ──
    error_records: list[dict[str, Any]] = []
    if summary_df is not None:
        mask_code: pl.Series = summary_df["物料编码"].is_null() | (
            summary_df["物料编码"].cast(pl.Utf8).str.strip_chars() == ""
        )
        for row_idx in mask_code.arg_true():
            error_records.append({
                "file": file_path.name,
                "row": row_idx + dsr + 1,
                "column": "物料编码",
                "original_value": str(summary_df["物料编码"][row_idx]),
                "reason": "缺失",
            })

        for num_col in NUMERIC_FIELDS:
            mask_null: pl.Series = summary_df[num_col].is_null()
            for row_idx in mask_null.arg_true():
                error_records.append({
                    "file": file_path.name,
                    "row": row_idx + dsr + 1,
                    "column": num_col,
                    "original_value": "N/A（cast 失败）",
                    "reason": "非数字文本",
                })

    return summary_df, weekly_df, error_records, validation_issues


# ============================================================================
# 主入口
# ============================================================================

def main() -> None:
    """命令行入口，执行字段提取和双文件输出。"""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="字段提取与数据准备 — 供应链智能分析平台"
    )
    parser.add_argument("--input", required=True, help="输入文件夹路径")
    parser.add_argument("--output", required=True, help="输出目录路径")
    parser.add_argument("--column-mapping", type=str, default=None,
                        help="JSON 格式的列映射表")
    parser.add_argument("--header-row", type=int, default=None,
                        help="手动指定业务动作行行号 (0-based)，默认 1")
    parser.add_argument("--data-start-row", type=int, default=None,
                        help="手动指定数据起始行号 (0-based)，默认 header_row + 1")
    parser.add_argument("--material-master", type=str, default=None,
                        help="物料主数据文件路径（Excel/CSV/Parquet，包含生命周期字段）")
    parser.add_argument("--lifecycle-config", type=str, default=None,
                        help="JSON 格式的生命周期配置（少量物料快速测试用）")
    parser.add_argument("--exclude-keywords", type=str, default=None,
                        help="自定义排除关键词列表（逗号分隔），默认：合计,总计,小计")
    parser.add_argument("--exclude-columns", type=str, default=None,
                        help="自定义关键词检测列名列表（逗号分隔），默认前7列")
    args: argparse.Namespace = parser.parse_args()

    input_dir: Path = Path(args.input)
    output_dir: Path = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    column_mapping: dict[str, str] = {}
    if args.column_mapping:
        column_mapping = json.loads(args.column_mapping)

    # ── 解析排除参数 ──
    exclude_keywords: list[str] | None = None
    if args.exclude_keywords:
        exclude_keywords = [kw.strip() for kw in args.exclude_keywords.split(",")]

    exclude_columns: list[str] | None = None
    if args.exclude_columns:
        exclude_columns = [col.strip() for col in args.exclude_columns.split(",")]

    files: list[Path] = sorted(
        list(input_dir.glob("*.csv"))
        + list(input_dir.glob("*.xlsx"))
        + list(input_dir.glob("*.xls"))
    )
    if not files:
        print(f"错误: 在 {input_dir} 中未找到 CSV 或 Excel 文件")
        sys.exit(1)

    all_errors: list[dict[str, Any]] = []
    all_validation: list[dict[str, Any]] = []
    _weekly_dfs_list: list[pl.DataFrame] = []

    def _gen_summaries() -> Generator[pl.DataFrame, None, None]:
        for f in files:
            print(f"提取文件: {f.name}")
            summary_df, weekly_df, errors, validation = extract_from_file(
                f, column_mapping, args.header_row, args.data_start_row,
                exclude_keywords=exclude_keywords,
                exclude_columns=exclude_columns,
            )
            all_errors.extend(errors)
            all_validation.extend(validation)
            if weekly_df is not None and weekly_df.height > 0:
                _weekly_dfs_list.append(weekly_df)
            if summary_df is not None:
                yield summary_df

    def _gen_weeklies() -> Generator[pl.DataFrame, None, None]:
        for f in files:
            _, weekly_df, _, _ = extract_from_file(
                f, column_mapping, args.header_row, args.data_start_row,
                exclude_keywords=exclude_keywords,
                exclude_columns=exclude_columns,
            )
            if weekly_df is not None and weekly_df.height > 0:
                yield weekly_df

    merged_summary: pl.DataFrame | None = concat_dataframes_stream(_gen_summaries())
    if merged_summary is not None:
        material_master_path: Path | None = (
            Path(args.material_master) if args.material_master else None
        )
        merged_summary = merge_material_master(
            merged_summary, material_master_path, args.lifecycle_config
        )
        merged_summary = merged_summary.unique(subset=["物料编码"])
        summary_path: Path = output_dir / "extracted_summary.parquet"
        merged_summary.write_parquet(summary_path)
        print(f"汇总数据已保存: {summary_path} ({merged_summary.height} 行)")
    else:
        print("警告: 无有效汇总数据")

    merged_weekly: pl.DataFrame | None = concat_dataframes_stream(_gen_weeklies())
    if merged_weekly is not None:
        weekly_path: Path = output_dir / "extracted_weekly.parquet"
        merged_weekly.write_parquet(weekly_path)
        weeks_count: int = merged_weekly["ISO_Week"].n_unique()
        print(f"周度数据已保存: {weekly_path} "
              f"({merged_weekly.height} 行, {weeks_count} 周)")
    else:
        print("警告: 无有效周度数据")

    error_path: Path = output_dir / "error_report.json"
    error_report: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "files_processed": [f.name for f in files],
        "total_errors": len(all_errors),
        "errors": all_errors,
        "validation_issues": all_validation,
    }
    with open(error_path, "w", encoding="utf-8") as fp:
        json.dump(error_report, fp, ensure_ascii=False, indent=2)
    print(f"错误报告已保存: {error_path} "
          f"(错误: {len(all_errors)} 条, 交叉验证问题: {len(all_validation)} 条)")


if __name__ == "__main__":
    main()