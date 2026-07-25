"""
数据工具公共模块 (utils.py)

供应链智能分析平台 — 通用工具函数

功能：提供跨脚本复用的通用功能。
     1. 编码自动检测
     2. CSV 惰性读取
     3. 流式 DataFrame 拼接

作者: Supply Chain Intelligence Team
版本: 0.2.5
"""
from __future__ import annotations

from itertools import chain
from pathlib import Path
from typing import Iterable, Literal

import polars as pl


# ============================================================================
# 编码检测配置
# ============================================================================

ENCODING_CANDIDATES: list[str] = [
    "utf-8", "utf-8-sig", "gbk", "gb2312", "latin-1"
]


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
# CSV 惰性读取
# ============================================================================

def read_csv_lazy(file_path: Path, encoding: str) -> pl.LazyFrame:
    """
    使用惰性扫描或 Eager 读取 CSV 文件，返回 LazyFrame。

    优先使用 scan_csv（支持惰性扫描，可触发谓词下推和投影下推优化）。
    如果编码不被 scan_csv 支持，回退到 read_csv + lazy()，
    并输出警告建议用户将文件转换为 UTF-8 以获得更好的性能。

    企业级大数据量适配：
        - UTF-8 文件：惰性扫描，内存占用极低
        - GBK 文件：尝试 scan_csv 传入 encoding，若失败则回退 read_csv
        - 对于千万级数据量的 GBK 文件，建议预先转换为 UTF-8

    Parameters
    ----------
    file_path : Path
        CSV 文件路径。
    encoding : str
        检测到的文件编码。

    Returns
    -------
    pl.LazyFrame
        惰性 DataFrame。
    """
    # 标准化编码名称为 Polars 接受的格式
    encoding_lower: str = encoding.lower()
    if encoding_lower in ("utf-8", "utf-8-sig"):
        scan_encoding: str = "utf8"
    elif encoding_lower in ("gbk", "gb2312"):
        scan_encoding = "gbk"
    elif encoding_lower == "latin-1":
        scan_encoding = "latin-1"
    else:
        scan_encoding = encoding_lower

    # 优先尝试惰性扫描
    try:
        return pl.scan_csv(
            file_path,
            encoding=scan_encoding,
            separator=",",
            has_header=False,
            truncate_ragged_lines=True,
        )
    except (ValueError, TypeError):
        # scan_csv 不支持该编码，回退到 read_csv + lazy
        print(
            f"警告: scan_csv 不支持编码 '{encoding}'，"
            f"将使用 read_csv 全量加载后转 Lazy。\n"
            f"对于大数据量文件，建议将文件转换为 UTF-8 编码以获得更好的性能。"
        )
        eager_df: pl.DataFrame = pl.read_csv(
            file_path,
            encoding=encoding,
            separator=",",
            has_header=False,
            truncate_ragged_lines=True,
        )
        return eager_df.lazy()


# ============================================================================
# 流式 DataFrame 拼接
# ============================================================================

def concat_dataframes_stream(
    dfs: Iterable[pl.DataFrame],
    how: Literal["diagonal"] = "diagonal",
    rechunk: bool = False,
) -> pl.DataFrame | None:
    """
    流式拼接 DataFrame 序列，避免内存峰值。

    使用生成器表达式替代列表收集，逐个消费 DataFrame 并传入
    pl.concat。Polars C++ 引擎以零拷贝方式将数据吸入底层连续矩阵，
    配合 how="diagonal" 自动处理异构 Schema（缺失列补 null）。

    默认不去重，如需去重请在调用后自行 .unique()。

    Parameters
    ----------
    dfs : Iterable[pl.DataFrame]
        可迭代的 DataFrame 序列（生成器或列表）。
    how : str
        Polars concat 方式，默认 "diagonal"（纵向对齐拼接，
        列名不一致时自动补 null，不会因 Schema 不匹配而崩溃）。
    rechunk : bool
        是否重整内存连续性，默认 False 以降低内存开销。

    Returns
    -------
    pl.DataFrame or None
        拼接后的 DataFrame。若输入为空迭代器则返回 None。

    Examples
    --------
    >>> dfs = (df.filter(pl.col("物料编码") == code) for code in codes)
    >>> result = concat_dataframes_stream(dfs)
    >>> if result is not None:
    ...     result = result.unique(subset=["物料编码"])
    """
    stream: Iterable[pl.DataFrame] = (df for df in dfs if df is not None)

    try:
        first_df: pl.DataFrame = next(stream)
    except StopIteration:
        return None

    return pl.concat(
        chain([first_df], stream),
        how=how,
        rechunk=rechunk,
    )