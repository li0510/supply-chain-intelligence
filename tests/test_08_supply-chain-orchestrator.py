"""
编排器测试 (test_08_orchestrator.py)

验证 orchestrator 编排器的状态扫描和空跑模式。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


# ============================================================================
# 测试 8.1：状态扫描
# ============================================================================

def test_orchestrator_status_only(
    scripts_base_dir: Path,
    output_dir: Path,
) -> None:
    """测试 orchestrator.py 的状态扫描功能。"""
    script_path: Path = (
        scripts_base_dir / "supply-chain-orchestrator" / "scripts" / "orchestrator.py"
    )

    result: subprocess.CompletedProcess = subprocess.run(
        [
            sys.executable, str(script_path),
            "--project-dir", str(output_dir),
            "--status-only",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"orchestrator.py 状态扫描失败。\nstderr: {result.stderr}"
    )

    # 验证输出包含模块状态信息
    output: str = result.stdout
    assert "供应链分析模块状态" in output, "缺少标题"
    assert "data-inspector" in output, "缺少 data-inspector"
    assert "inventory-overview" in output, "缺少 inventory-overview"
    assert "category-classifier" in output, "缺少 category-classifier"
    assert "supplier-analyzer" in output, "缺少 supplier-analyzer"
    assert "supply-demand-matcher" in output, "缺少 supply-demand-matcher"
    assert "inventory-planner" in output, "缺少 inventory-planner"
    assert "purchase-advisor" in output, "缺少 purchase-advisor"


# ============================================================================
# 测试 8.2：空跑模式
# ============================================================================

def test_orchestrator_dry_run(
    scripts_base_dir: Path,
    output_dir: Path,
) -> None:
    """测试 orchestrator.py 的空跑模式（生成计划不执行）。"""
    script_path: Path = (
        scripts_base_dir / "supply-chain-orchestrator" / "scripts" / "orchestrator.py"
    )

    result: subprocess.CompletedProcess = subprocess.run(
        [
            sys.executable, str(script_path),
            "--project-dir", str(output_dir),
            "--all",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"orchestrator.py 空跑失败。\nstderr: {result.stderr}"
    )

    output: str = result.stdout
    assert "执行计划" in output, "缺少执行计划"
    assert "[空跑模式]" in output, "缺少空跑模式标记"