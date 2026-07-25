"""
列映射脚本 (column_mapper.py)

供应链智能分析平台 — data-inspector 子 Skill

功能：三级列名匹配。
     第一级：精确匹配（大小写、空格、下划线不敏感）。
     第二级：预设中文别名表模糊匹配。
     第三级：用户手动指定映射。

用法:
    uv run column_mapper.py --columns <JSON列名列表> \
      --manual-mapping <JSON手动映射(可选)> --output <输出路径>

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


def normalize_key(s: str) -> str:
    """标准化列名：去空格、小写、去下划线，用于模糊匹配。"""
    return s.replace(" ", "").replace("_", "").lower()


# ============================================================================
# 主函数
# ============================================================================

def resolve_columns(
    columns: list[str],
    manual_mapping: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    三级列名匹配，返回映射结果和状态信息。

    第一级：精确匹配（大小写、空格、下划线不敏感）。
    第二级：预设中文别名表模糊匹配。
    第三级：用户手动指定映射。

    若三级均失败，返回未完成状态而非退出，由调用方决定处理方式。

    Parameters
    ----------
    columns : list[str]
        源文件中检测到的列名列表。
    manual_mapping : dict[str, str] | None
        用户手动提供的映射表，键为标准字段名，值为源文件列名。

    Returns
    -------
    dict[str, Any]
        {
            "status": "success" | "partial" | "failed",
            "mapping": {标准字段: 实际列名},
            "missing": [未能匹配的标准字段列表],
            "matched_level": {标准字段: "exact"|"alias"|"manual"}
        }
    """
    norm_map: dict[str, str] = {normalize_key(c): c for c in columns}
    mapping: dict[str, str] = {}
    matched_level: dict[str, str] = {}

    # ── 第一级：精确匹配 ──
    for std in STANDARD_FIELDS:
        nk: str = normalize_key(std)
        if nk in norm_map:
            mapping[std] = norm_map[nk]
            matched_level[std] = "exact"

    # ── 第二级：中文映射表 ──
    still_missing: list[str] = [f for f in STANDARD_FIELDS if f not in mapping]
    if still_missing:
        for std in still_missing:
            for alias in ALIAS_MAP[std]:
                na: str = normalize_key(alias)
                if na in norm_map:
                    mapping[std] = norm_map[na]
                    matched_level[std] = "alias"
                    break

    # ── 第三级：用户手动映射 ──
    still_missing = [f for f in STANDARD_FIELDS if f not in mapping]
    if still_missing and manual_mapping:
        for std in still_missing:
            if std in manual_mapping and manual_mapping[std] in columns:
                mapping[std] = manual_mapping[std]
                matched_level[std] = "manual"

    # ── 确定状态 ──
    still_missing = [f for f in STANDARD_FIELDS if f not in mapping]
    if not still_missing:
        status: str = "success"
    elif len(still_missing) < len(STANDARD_FIELDS):
        status = "partial"
    else:
        status = "failed"

    return {
        "status": status,
        "mapping": mapping,
        "missing": still_missing,
        "matched_level": matched_level,
    }


def main() -> None:
    """命令行入口，执行列映射并输出结果。"""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="列映射 — 供应链智能分析平台"
    )
    parser.add_argument("--columns", required=True, help="JSON 格式的列名列表")
    parser.add_argument("--manual-mapping", type=str, default=None,
                        help="JSON 格式的手动映射表（可选）")
    parser.add_argument("--output", required=True, help="输出文件路径")
    args: argparse.Namespace = parser.parse_args()

    columns: list[str] = json.loads(args.columns)
    manual_mapping: dict[str, str] | None = None
    if args.manual_mapping:
        manual_mapping = json.loads(args.manual_mapping)

    result: dict[str, Any] = resolve_columns(columns, manual_mapping)

    output_path: Path = Path(args.output)
    with open(output_path, "w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False, indent=2)

    if result["status"] == "failed":
        print(f"错误: 所有标准字段均无法匹配。缺失字段: {result['missing']}")
        print(f"文件中的列名: {columns}")
        sys.exit(1)
    elif result["status"] == "partial":
        print(f"警告: 部分字段未能匹配。已匹配: {list(result['mapping'].keys())}, "
              f"缺失: {result['missing']}")
    else:
        print(f"列映射成功。匹配级别: {result['matched_level']}")

    print(f"映射结果已保存: {output_path}")


if __name__ == "__main__":
    main()