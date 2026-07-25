"""
流式拼接脚本 (concat_dataframes_stream.py)

供应链智能分析平台 — data-inspector 子 Skill

功能：流式拼接多个 DataFrame，避免内存峰值。
     使用 next() + itertools.chain 模式 + pl.concat(diagonal, rechunk=False)。

符合 Polars 高性能数据处理原则体系：
    - pl.concat + diagonal + 生成器 + rechunk=False

用法:
    from concat_dataframes_stream import concat_dataframes_stream

作者: Supply Chain Intelligence Team
版本: 0.2.5
"""
from __future__ import annotations

from itertools import chain
from typing import Iterable, Literal

import polars as pl


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
    # 过滤掉 None 值，保留有效 DataFrame
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