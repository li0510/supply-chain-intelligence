"""
pytest 全局配置文件 (conftest.py)

供应链智能分析平台 — 测试套件

功能：定义全局 fixtures、路径配置、测试数据准备。

用法:
    cd sci/
    uv run pytest tests/ -v

作者: Supply Chain Intelligence Team
版本: 0.2.5
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any, Generator

import pytest


# ============================================================================
# 路径配置
# ============================================================================

@pytest.fixture(scope="session")
def project_root() -> Path:
    """项目根目录路径。"""
    return Path(__file__).parent.parent.resolve()


@pytest.fixture(scope="session")
def test_data_dir(project_root: Path) -> Path:
    """测试数据目录路径。"""
    return project_root / "tests" / "test_data"


@pytest.fixture(scope="session")
def output_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """测试输出目录（pytest 临时目录）。"""
    return tmp_path_factory.mktemp("sci_test_output")


@pytest.fixture(scope="session")
def scripts_base_dir(project_root: Path) -> Path:
    """脚本基础目录。"""
    return project_root / "skills"


@pytest.fixture(scope="session")
def raw_data_file(test_data_dir: Path) -> Path:
    """原始测试数据文件路径。"""
    return test_data_dir / "原料库存明细表.csv"


@pytest.fixture(scope="session")
def template_file(test_data_dir: Path) -> Path:
    """模板文件路径。"""
    return test_data_dir / "库存明细表_模板.xlsx"


# ============================================================================
# 环境检查
# ============================================================================

@pytest.fixture(scope="session", autouse=True)
def check_environment(project_root: Path) -> None:
    """自动检查测试环境。"""
    # 检查 Python 版本
    python_version: str = f"{sys.version_info.major}.{sys.version_info.minor}"
    assert sys.version_info >= (3, 11), (
        f"需要 Python >= 3.11，当前版本: {python_version}"
    )

    # 检查 pyproject.toml
    pyproject_path: Path = project_root / "pyproject.toml"
    assert pyproject_path.exists(), f"pyproject.toml 不存在: {pyproject_path}"

    # 检查原始数据文件
    raw_data: Path = project_root / "tests" / "test_data" / "原料库存明细表.csv"
    if not raw_data.exists():
        print(f"警告: 原始数据文件不存在: {raw_data}")
        print("请将测试数据文件放置到 tests/test_data/ 目录下。")


# ============================================================================
# 共享 fixtures
# ============================================================================

@pytest.fixture(scope="session")
def column_mapping() -> dict[str, str]:
    """标准列映射配置。"""
    return {
        "物料编码": "物料编码",
        "库存量": "库存",
        "入库数量": "入库",
        "出库数量": "出库",
        "结存数量": "结存",
    }


@pytest.fixture(scope="session")
def demand_data(output_dir: Path) -> Path:
    """创建模拟需求数据文件。"""
    demand_path: Path = output_dir / "demand_data.json"
    demand: list[dict[str, Any]] = [
        {"物料编码": "GSN-0001", "需求量": 5000},
        {"物料编码": "GSN-0002", "需求量": 3500},
        {"物料编码": "GSN-0003", "需求量": 15000},
        {"物料编码": "GSN-0004", "需求量": 500},
        {"物料编码": "GSN-0005", "需求量": 8000},
    ]
    with open(demand_path, "w", encoding="utf-8") as fp:
        json.dump(demand, fp, ensure_ascii=False, indent=2)
    return demand_path