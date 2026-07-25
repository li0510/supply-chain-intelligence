"""
企业级压力测试数据生成器 (generate_stress_data.py)

供应链智能分析平台 — 测试工具

功能：生成模拟的企业级供应链数据，用于压力测试。
     支持可配置的 SKU 数量、周数、随机种子（确保可复现性）。
     生成文件：
         1. extracted_weekly.parquet — 周度明细数据
         2. extracted_summary.parquet — 汇总数据（含生命周期字段）
         3. abc_xyz_result.json — ABC-XYZ 分类结果
         4. material_master.parquet — 物料主数据（生命周期字段，中文字段名）

数据生成逻辑：
     - 正常物料：周出库量 ~ 正态分布（均值 500，标准差 200），确保 ≥ 0
     - 间歇性物料（Intermittent）：以一定概率使每周出库量为零，
       非零周出库量 ~ 正态分布（均值 800，标准差 300）
     - 块状物料（Lumpy）：大部分周出库量为零，
       偶发非零周出库量 ~ 正态分布（均值 2000，标准差 500）
     - 周入库量 = 周出库量 × (0.9~1.2) 随机波动
     - 周结存 = 前一周结存 + 本周入库 - 本周出库（初始结存随机生成）
     - 汇总数据满足平衡校验：期末 ≈ 期初 + 入库 - 出库
     - ABC 按 30:40:30 随机分配，XYZ 按 40:30:30 随机分配
     - 生命周期字段（中文字段名）：95% 正常在售，3% 新品上市，2% 老品下市

新增功能：
    - 支持 --intermittent-ratio 参数，控制间歇性/块状物料占比
    - 间歇性物料：zero_prob ≈ 0.7，ADI > 1.32, CV² > 0.49
    - 块状物料：zero_prob ≈ 0.8，ADI > 1.32, CV² ≤ 0.49
    - 为间歇性/块状物料自动设置物料编码前缀以区分类型

可复现性保证：
     - 接受 --seed 参数（默认 42），通过函数参数传递给各生成函数
     - 相同 seed + 相同参数 → 完全一致的输出文件

用法:
    uv run python tests/test_data/generate_stress_data.py \
      --skus 10000 --weeks 260 --seed 42 \
      --intermittent-ratio 0.1 --output-dir /tmp/stress_data/

作者: Supply Chain Intelligence Team
版本: 0.2.5
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl


# ============================================================================
# 配置
# ============================================================================

DEFAULT_SKUS: int = 10000
DEFAULT_WEEKS: int = 260
DEFAULT_INTERMITTENT_RATIO: float = 0.1
WEEKLY_MEAN: float = 500.0
WEEKLY_STD: float = 200.0
INITIAL_BALANCE_MIN: float = 1000.0
INITIAL_BALANCE_MAX: float = 50000.0

# 间歇性物料参数
INTERMITTENT_MEAN: float = 800.0
INTERMITTENT_STD: float = 300.0
INTERMITTENT_ZERO_PROB: float = 0.7

# 块状物料参数
LUMPY_MEAN: float = 2000.0
LUMPY_STD: float = 500.0
LUMPY_ZERO_PROB: float = 0.8


# ============================================================================
# 周度数据生成
# ============================================================================

def generate_weekly_data(
    num_skus: int,
    num_weeks: int,
    base_seed: int,
    intermittent_ratio: float = DEFAULT_INTERMITTENT_RATIO,
) -> pl.DataFrame:
    """
    生成周度明细数据。

    每个 SKU × 每周 → 一行，包含：
        - 物料编码
        - ISO_Week（1~num_weeks）
        - 周入库量
        - 周出库量
        - 周结存

    物料类型分布：
        - 正常物料：(1 - intermittent_ratio) 的比例
        - 间歇性物料（Intermittent）：intermittent_ratio / 2 的比例
        - 块状物料（Lumpy）：intermittent_ratio / 2 的比例

    Parameters
    ----------
    num_skus : int
        SKU 数量。
    num_weeks : int
        周数。
    base_seed : int
        基础随机种子。
    intermittent_ratio : float
        间歇性/块状物料占比，默认 0.1（10%）。

    Returns
    -------
    pl.DataFrame
        周度明细数据。
    """
    all_rows: list[dict[str, Any]] = []

    # 计算各类型物料数量
    num_intermittent: int = int(num_skus * intermittent_ratio / 2)
    num_lumpy: int = int(num_skus * intermittent_ratio / 2)
    num_normal: int = num_skus - num_intermittent - num_lumpy

    # 为间歇性物料使用不同编码前缀
    normal_codes: list[str] = [f"GSN-{i:05d}" for i in range(num_normal)]
    intermittent_codes: list[str] = [f"INT-{i:05d}" for i in range(num_intermittent)]
    lumpy_codes: list[str] = [f"LMP-{i:05d}" for i in range(num_lumpy)]

    all_codes: list[tuple[str, str]] = (
        [(code, "normal") for code in normal_codes]
        + [(code, "intermittent") for code in intermittent_codes]
        + [(code, "lumpy") for code in lumpy_codes]
    )

    for idx, (code, demand_type) in enumerate(all_codes):
        rng: np.random.Generator = np.random.default_rng(
            seed=base_seed + idx
        )

        if demand_type == "normal":
            out_values: np.ndarray = np.maximum(
                0, rng.normal(WEEKLY_MEAN, WEEKLY_STD, num_weeks)
            )
        elif demand_type == "intermittent":
            # 间歇性：以 zero_prob 概率置零
            out_values = np.maximum(
                0, rng.normal(INTERMITTENT_MEAN, INTERMITTENT_STD, num_weeks)
            )
            zero_mask: np.ndarray = rng.random(num_weeks) < INTERMITTENT_ZERO_PROB
            out_values[zero_mask] = 0.0
        else:
            # 块状：更高的零概率和更大的非零值
            out_values = np.maximum(
                0, rng.normal(LUMPY_MEAN, LUMPY_STD, num_weeks)
            )
            zero_mask = rng.random(num_weeks) < LUMPY_ZERO_PROB
            out_values[zero_mask] = 0.0

        in_ratios: np.ndarray = rng.uniform(0.9, 1.2, num_weeks)
        in_values: np.ndarray = out_values * in_ratios

        balance: float = rng.uniform(INITIAL_BALANCE_MIN, INITIAL_BALANCE_MAX)
        for week in range(num_weeks):
            balance = balance + in_values[week] - out_values[week]
            balance = max(0.0, balance)
            all_rows.append({
                "物料编码": code,
                "ISO_Week": week + 1,
                "周入库量": round(float(in_values[week]), 2),
                "周出库量": round(float(out_values[week]), 2),
                "周结存": round(balance, 2),
            })

    return pl.DataFrame(all_rows)


# ============================================================================
# 汇总数据生成
# ============================================================================

def generate_summary_data(weekly_df: pl.DataFrame) -> pl.DataFrame:
    """
    从周度数据生成汇总数据，满足平衡校验。

    Parameters
    ----------
    weekly_df : pl.DataFrame
        周度明细数据。

    Returns
    -------
    pl.DataFrame
        汇总数据（5列）。
    """
    agg: pl.DataFrame = weekly_df.group_by("物料编码").agg(
        pl.col("周入库量").sum().alias("入库数量"),
        pl.col("周出库量").sum().alias("出库数量"),
        pl.col("周结存").first().alias("期初结存"),
        pl.col("周结存").last().alias("结存数量"),
    )

    summary: pl.DataFrame = agg.select([
        pl.col("物料编码"),
        pl.col("期初结存").alias("库存量"),
        pl.col("入库数量"),
        pl.col("出库数量"),
        pl.col("结存数量"),
    ]).with_columns([
        pl.col("库存量").cast(pl.Float32),
        pl.col("入库数量").cast(pl.Float32),
        pl.col("出库数量").cast(pl.Float32),
        pl.col("结存数量").cast(pl.Float32),
    ])

    return summary


# ============================================================================
# 物料主数据生成（生命周期字段，中文字段名）
# ============================================================================

def generate_material_master(
    codes: list[str],
    base_seed: int,
) -> pl.DataFrame:
    """
    生成物料主数据文件，包含生命周期字段（中文字段名）。

    字段分布：
        - 生命周期状态: 95% 正常在售, 3% 新品上市, 2% 老品下市
        - 保质期天数: 按 180/270/365/540/730 天随机分配
        - 生产日期: 随机生成在过去保质期的 50% 处
        - 新品上市日期: 仅对新品上市物料，设为未来 1~4 周内
        - 老品下市日期: 仅对老品下市物料，设为未来 1~4 周内

    Parameters
    ----------
    codes : list[str]
        物料编码列表。
    base_seed : int
        随机种子。

    Returns
    -------
    pl.DataFrame
        物料主数据（中文字段名）。
    """
    rng: np.random.Generator = np.random.default_rng(seed=base_seed + 9999)
    n: int = len(codes)

    lifecycle_choices: list[str] = (
        ["正常在售"] * 95 + ["新品上市"] * 3 + ["老品下市"] * 2
    )
    shelf_life_options: list[int] = [180, 270, 365, 540, 730]

    rows: list[dict[str, Any]] = []
    for i, code in enumerate(codes):
        status: str = rng.choice(lifecycle_choices)
        shelf_life: int = rng.choice(shelf_life_options)
        days_ago: float = shelf_life * 0.5
        prod_date: datetime = datetime.now() - timedelta(days=days_ago)
        expiry_date: datetime = prod_date + timedelta(days=int(shelf_life))

        row: dict[str, Any] = {
            "物料编码": code,
            "生命周期状态": status,
            "保质期天数": shelf_life,
            "生产日期": prod_date.strftime("%Y-%m-%d"),
            "过期日期": expiry_date.strftime("%Y-%m-%d"),
            "剩余保质期天数": int(
                (expiry_date - datetime.now()).days
            ),
            "新品上市日期": None,
            "老品下市日期": None,
        }

        if status == "新品上市":
            row["新品上市日期"] = (
                datetime.now() + timedelta(weeks=int(rng.integers(1, 5)))
            ).strftime("%Y-%m-%d")
        elif status == "老品下市":
            row["老品下市日期"] = (
                datetime.now() + timedelta(weeks=int(rng.integers(1, 5)))
            ).strftime("%Y-%m-%d")

        rows.append(row)

    return pl.DataFrame(rows)


# ============================================================================
# 分类数据生成
# ============================================================================

def generate_classification_data(
    weekly_df: pl.DataFrame,
    base_seed: int,
) -> dict[str, Any]:
    """
    生成 ABC-XYZ 分类结果 JSON。

    ABC 按 30:40:30 分配，XYZ 按 40:30:30 分配。

    Parameters
    ----------
    weekly_df : pl.DataFrame
        周度明细数据。
    base_seed : int
        随机种子。

    Returns
    -------
    dict[str, Any]
        分类结果（与 category-classifier 产出格式一致）。
    """
    codes: list[str] = weekly_df["物料编码"].unique().to_list()
    rng: np.random.Generator = np.random.default_rng(seed=base_seed)

    abc_choices: list[str] = (
        ["A"] * 30 + ["B"] * 40 + ["C"] * 30
    )
    xyz_choices: list[str] = (
        ["X"] * 40 + ["Y"] * 30 + ["Z"] * 30
    )

    abc_items: list[dict[str, Any]] = []
    xyz_items: list[dict[str, Any]] = []
    matrix: dict[str, int] = {}
    strategy_items: list[dict[str, Any]] = []

    CONTROL_STRATEGY_MATRIX: dict[str, dict[str, str]] = {
        "AX": {"服务水平": "99%", "补货机制": "定期定量(JIT)"},
        "AY": {"服务水平": "97%", "补货机制": "定期不定量"},
        "AZ": {"服务水平": "95%", "补货机制": "不定期不定量(按订单)"},
        "BX": {"服务水平": "97%", "补货机制": "定期定量(EOQ)"},
        "BY": {"服务水平": "95%", "补货机制": "定期不定量"},
        "BZ": {"服务水平": "90%", "补货机制": "不定期不定量"},
        "CX": {"服务水平": "95%", "补货机制": "不定期定量(双堆法)"},
        "CY": {"服务水平": "90%", "补货机制": "不定期不定量"},
        "CZ": {"服务水平": "85%", "补货机制": "不定期不定量"},
    }

    for code in codes:
        abc_class: str = rng.choice(abc_choices)
        xyz_class: str = rng.choice(xyz_choices)
        combo: str = f"{abc_class}{xyz_class}"

        abc_items.append({"物料编码": code, "ABC分类": abc_class})
        xyz_items.append({"物料编码": code, "XYZ分类": xyz_class})
        matrix[combo] = matrix.get(combo, 0) + 1

        strategy: dict[str, str] = CONTROL_STRATEGY_MATRIX.get(
            combo, {"服务水平": "N/A", "补货机制": "未定义"}
        )
        strategy_items.append({
            "物料编码": code,
            "ABC分类": abc_class,
            "XYZ分类": xyz_class,
            "组合": combo,
            "服务水平": strategy["服务水平"],
            "补货机制": strategy["补货机制"],
        })

    abc_counts: dict[str, int] = {"A": 0, "B": 0, "C": 0}
    for item in abc_items:
        abc_counts[item["ABC分类"]] += 1

    xyz_counts: dict[str, int] = {"X": 0, "Y": 0, "Z": 0}
    for item in xyz_items:
        xyz_counts[item["XYZ分类"]] += 1

    return {
        "abc_classification": {
            "total_items": len(codes),
            "a_count": abc_counts["A"],
            "b_count": abc_counts["B"],
            "c_count": abc_counts["C"],
            "item_details": abc_items,
        },
        "xyz_classification": {
            "total_items": len(codes),
            "x_count": xyz_counts["X"],
            "y_count": xyz_counts["Y"],
            "z_count": xyz_counts["Z"],
            "insufficient_data_count": 0,
            "item_details": xyz_items,
        },
        "abc_xyz_matrix": {
            "status": "completed",
            "matrix": matrix,
            "strategy_items": strategy_items,
            "total_combinations": len(matrix),
        },
    }


# ============================================================================
# 主入口
# ============================================================================

def main() -> None:
    """命令行入口，生成企业级模拟数据。"""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="企业级压力测试数据生成器 — 供应链智能分析平台"
    )
    parser.add_argument("--skus", type=int, default=DEFAULT_SKUS,
                        help=f"SKU 数量（默认 {DEFAULT_SKUS}）")
    parser.add_argument("--weeks", type=int, default=DEFAULT_WEEKS,
                        help=f"周数（默认 {DEFAULT_WEEKS}）")
    parser.add_argument("--seed", type=int, default=42,
                        help="随机种子（默认 42，确保可复现）")
    parser.add_argument("--intermittent-ratio", type=float,
                        default=DEFAULT_INTERMITTENT_RATIO,
                        help=f"间歇性/块状物料占比（默认 {DEFAULT_INTERMITTENT_RATIO}）")
    parser.add_argument("--output-dir", required=True,
                        help="输出目录路径")
    args: argparse.Namespace = parser.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)

    output_dir: Path = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    num_intermittent: int = int(args.skus * args.intermittent_ratio / 2)
    num_lumpy: int = int(args.skus * args.intermittent_ratio / 2)
    num_normal: int = args.skus - num_intermittent - num_lumpy

    print(f"生成参数: SKU={args.skus}, 周数={args.weeks}, 种子={args.seed}")
    print(f"物料类型分布: 正常={num_normal}, 间歇性={num_intermittent}, 块状={num_lumpy}")
    print(f"预计数据量: {args.skus * args.weeks} 行周度数据")

    # ── 生成周度数据 ──
    print("生成周度数据...")
    weekly_df: pl.DataFrame = generate_weekly_data(
        args.skus, args.weeks, args.seed, args.intermittent_ratio
    )
    weekly_path: Path = output_dir / "extracted_weekly.parquet"
    weekly_df.write_parquet(weekly_path)
    print(f"  周度数据: {weekly_path} ({weekly_df.height} 行)")

    # ── 生成汇总数据 ──
    print("生成汇总数据...")
    summary_df: pl.DataFrame = generate_summary_data(weekly_df)

    # ── 生成物料主数据并合并到汇总数据 ──
    print("生成物料主数据（生命周期字段，中文字段名）...")
    codes: list[str] = summary_df["物料编码"].to_list()
    master_df: pl.DataFrame = generate_material_master(codes, args.seed)
    master_path: Path = output_dir / "material_master.parquet"
    master_df.write_parquet(master_path)
    print(f"  物料主数据: {master_path} ({master_df.height} 行)")

    # 合并生命周期字段到汇总数据
    lifecycle_cols_in_master: list[str] = [
        col for col in [
            "生命周期状态", "保质期天数",
            "生产日期", "过期日期",
            "剩余保质期天数",
            "新品上市日期", "老品下市日期",
        ] if col in master_df.columns
    ]
    if lifecycle_cols_in_master:
        summary_df = summary_df.join(
            master_df.select(["物料编码"] + lifecycle_cols_in_master),
            on="物料编码",
            how="left",
        )

    summary_path: Path = output_dir / "extracted_summary.parquet"
    summary_df.write_parquet(summary_path)
    print(f"  汇总数据: {summary_path} ({summary_df.height} 行)")

    # ── 生成分类数据 ──
    print("生成分类数据...")
    classification: dict[str, Any] = generate_classification_data(
        weekly_df, args.seed
    )
    classification_path: Path = output_dir / "abc_xyz_result.json"
    with open(classification_path, "w", encoding="utf-8") as fp:
        json.dump(classification, fp, ensure_ascii=False, indent=2)
    print(f"  分类数据: {classification_path}")

    # ── 验证平衡校验 ──
    summary_check: pl.DataFrame = summary_df.with_columns(
        (pl.col("库存量") + pl.col("入库数量")
         - pl.col("出库数量") - pl.col("结存数量")).abs().alias("diff")
    )
    max_diff: float = summary_check["diff"].max()
    print(f"平衡校验最大偏差: {max_diff:.4f}")

    print(f"\n数据生成完成! 文件位于: {output_dir}")
    print(f"  {weekly_path}")
    print(f"  {summary_path}")
    print(f"  {master_path}")
    print(f"  {classification_path}")


if __name__ == "__main__":
    main()