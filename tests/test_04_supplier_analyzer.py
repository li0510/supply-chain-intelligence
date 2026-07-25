"""
供应商分析测试 (test_04_supplier_analyzer.py)

验证 supplier-analyzer 子 Skill 的优雅降级功能。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


# ============================================================================
# 测试 4.1：供应商字段缺失时的优雅降级
# ============================================================================

def test_supplier_analyzer_skip_when_no_supplier_fields(
    scripts_base_dir: Path,
    output_dir: Path,
) -> None:
    """测试：无供应商字段时，脚本应优雅跳过并输出提示。"""
    script_path: Path = scripts_base_dir / "supplier-analyzer" / "scripts" / "supplier_evaluator.py"
    parquet_path: Path = output_dir / "extracted_summary.parquet"

    assert parquet_path.exists(), f"前置条件不满足: {parquet_path} 不存在。"

    result: subprocess.CompletedProcess = subprocess.run(
        [
            sys.executable, str(script_path),
            "--input", str(parquet_path),
            "--output", str(output_dir / "supplier_report.json"),
        ],
        capture_output=True,
        text=True,
    )

    # 脚本应正常退出（即使跳过分析）
    assert result.returncode == 0, (
        f"supplier_evaluator.py 异常退出。\nstderr: {result.stderr}"
    )

    output_path: Path = output_dir / "supplier_report.json"
    assert output_path.exists(), f"产出文件不存在: {output_path}"

    with open(output_path, "r", encoding="utf-8") as fp:
        data: dict[str, Any] = json.load(fp)

    # 预期状态为 skipped
    assert data.get("status") == "skipped", (
        f"期望 status='skipped', 实际 status='{data.get('status')}'"
    )
    assert "reason" in data, "缺少跳过原因说明"
    print(f"供应商分析跳过原因: {data['reason']}")