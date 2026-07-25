## 文件一：供应链智能分析平台（SCI）Skill 工作流使用指南

```markdown
# 供应链智能分析平台（SCI）Skill 工作流使用指南

> 版本: 0.2.5 | 最后更新: 2026-07-24 | 作者: Supply Chain Intelligence Team

---

## 一、平台简介

供应链智能分析平台（Supply-Chain-Intelligence，简称 SCI）是一套面向供应链执行层的智能数据分析工具集。它以进销存类表格为核心数据源，通过 8 个预置 Skill 组成的引导式工作流，覆盖从数据验表到采购决策建议的完整分析链路。

**核心特点**：
- **确定性强**：所有数据处理由预置 Python 脚本执行，相同输入必定产生相同输出
- **企业级性能**：基于 Polars 高性能数据处理引擎，支持千万级数据量
- **引导式交互**：从新手到大采购角色均可使用，逐步引导完成分析

---

## 二、Skill 体系总览

| # | Skill 名称 | 中文名称 | 功能概要 |
|---|-----------|---------|---------|
| 1 | `data-inspector` | 数据验表与探查 | 读取原始文件，自动识别结构，提取字段，输出标准化数据 |
| 2 | `inventory-overview` | 库存全景分析 | 存量/流量总览、周转效率、成本分析、产品流分析 |
| 3 | `category-classifier` | 分类与策略 | ABC-XYZ 分类、组合矩阵、差异化管控策略 |
| 4 | `supplier-analyzer` | 供应商分析 | 交期/质量/风险评估 |
| 5 | `supply-demand-matcher` | 供需匹配 | 需求端 vs 供给端缺口分析 |
| 6 | `inventory-planner` | 库存计划与预警 | 需求预测、安全库存、ROP、缺货/积压/效期预警 |
| 7 | `purchase-advisor` | 采购决策建议 | 采购优先级、建议采购量、供应商分配、综合报告 |
| 8 | `supply-chain-orchestrator` | 供应链分析编排器 | 顶层入口，菜单式引导，按依赖顺序串联子 Skill |

---

## 三、环境要求

- **Python 版本**: 3.11.14
- **包管理器**: uv（https://docs.astral.sh/uv/）
- **主要依赖**: Polars >= 1.40.0, openpyxl >= 3.1.0, scipy, numpy

### 环境搭建

```bash
cd sci/
uv venv --python 3.11.14
uv sync
```

---

## 四、Skill 详细使用说明

### Skill 01：数据验表与探查 (data-inspector)

**用途**：读取用户提供的任意格式原始库存明细文件（CSV/Excel），自动分析数据结构，提取关键字段，输出标准化的双文件供下游 Skill 消费。

**适用场景**：
- "帮我检查这份库存表能不能用"
- "把 ERP 导出的表格整理成标准格式"
- "分析一下这个文件的表头结构"

**输入**：
- 原始库存明细文件（单个或多个 `.csv` / `.xlsx`）
- 可选：物料主数据文件（包含生命周期字段）
- 可选：JSON 生命周期配置

**输出**：
- `extracted_summary.parquet`：汇总数据（物料编码 + 期初/入库/出库/结存 + 生命周期字段）
- `extracted_weekly.parquet`：周度明细数据（物料编码 + ISO_Week + 周入库/周出库/周结存）
- `raw_data_profile.json`：数据结构分析报告
- `error_report.json`：数据异常项清单

**命令行示例**：
```bash
uv run skills/data-inspector/scripts/data_extractor.py \
  --input ./raw_data/ \
  --output ./projects/my_project/ \
  --column-mapping '{"物料编码":"物料编码","库存量":"库存","入库数量":"入库","出库数量":"出库","结存数量":"结存"}' \
  --header-row 1 \
  --data-start-row 2
```

**可选参数**：
| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--material-master` | 物料主数据文件路径 | 无 |
| `--lifecycle-config` | JSON 生命周期配置 | 无 |
| `--exclude-keywords` | 自定义排除关键词（逗号分隔） | 合计,总计,小计 |
| `--exclude-columns` | 自定义检测列名（逗号分隔） | 前7列 |

---

### Skill 02：库存全景分析 (inventory-overview)

**用途**：对已提取的结构化库存数据进行存量和流量全景分析、周转效率分析、成本与资金分析。

**适用场景**：
- "帮我看看库存整体情况"
- "算一下库存周转率"
- "库存资金占了多少"

**前置条件**：`extracted_summary.parquet` 和 `extracted_weekly.parquet` 已生成。

**输入**：
- `extracted_summary.parquet`（必需）
- `extracted_weekly.parquet`（用于周转率精确计算）

**输出**：
- `inventory_overview.json`：库存全景报告
- `efficiency_cost_report.json`：效率成本报告

**命令行示例**：
```bash
# 步骤 1：库存全景
uv run skills/inventory-overview/scripts/data_aggregator.py \
  --input projects/my_project/extracted_summary.parquet \
  --output projects/my_project/inventory_overview.json

# 步骤 2：周转效率（周度精确计算）
uv run skills/inventory-overview/scripts/inventory_turnover.py \
  --input projects/my_project/extracted_summary.parquet \
  --weekly projects/my_project/extracted_weekly.parquet \
  --output projects/my_project/efficiency_cost_report.json

# 步骤 3：成本与资金分析（追加模式）
uv run skills/inventory-overview/scripts/cost_analyzer.py \
  --input projects/my_project/extracted_summary.parquet \
  --output projects/my_project/efficiency_cost_report.json \
  --append
```

---

### Skill 03：分类与策略 (category-classifier)

**用途**：基于 ABC-XYZ 分类法对物料进行分级，生成 9 宫格管控策略矩阵。

**适用场景**：
- "帮我分一下物料等级"
- "哪些是 A 类物料"
- "给物料做 ABC-XYZ 分类"

**前置条件**：`extracted_summary.parquet` 和 `extracted_weekly.parquet` 已生成。

**输入**：
- `extracted_summary.parquet`（ABC 分类）
- `extracted_weekly.parquet`（XYZ 分类，基于周度变异系数）

**输出**：
- `abc_xyz_result.json`：分类结果与管控策略

**命令行示例**：
```bash
# 步骤 1：ABC 分类
uv run skills/category-classifier/scripts/abc_classifier.py \
  --input projects/my_project/extracted_summary.parquet \
  --output projects/my_project/abc_xyz_result.json

# 步骤 2：XYZ 分类 + 组合矩阵（追加模式）
uv run skills/category-classifier/scripts/xyz_classifier.py \
  --input projects/my_project/extracted_weekly.parquet \
  --output projects/my_project/abc_xyz_result.json \
  --append
```

**可选参数**：
| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--x-threshold` | X 类变异系数上限 | 0.3 |
| `--y-threshold` | Y 类变异系数上限 | 0.8 |
| `--cost-report` | 效率成本报告路径（用于生命周期增强） | 无 |

---

### Skill 04：供应商分析 (supplier-analyzer)

**用途**：对供应商进行多维度评估，包括交货准时率、质量合格率、综合风险评分。

**适用场景**：
- "帮我看看供应商表现"
- "哪个供应商交期最准时"
- "供应商合格率排名"

**前置条件**：`extracted_summary.parquet` 已生成，且数据中包含供应商相关字段。

**输入**：
- `extracted_summary.parquet`（需包含供应商名称、计划交期、实际交期、合格数量等字段）

**输出**：
- `supplier_report.json`：供应商评估报告

**命令行示例**：
```bash
uv run skills/supplier-analyzer/scripts/supplier_evaluator.py \
  --input projects/my_project/extracted_summary.parquet \
  --output projects/my_project/supplier_report.json
```

**注意**：若数据中不包含供应商字段，脚本会自动跳过并输出提示。

---

### Skill 05：供需匹配 (supply-demand-matcher)

**用途**：整合供给端和需求端数据，计算供需缺口，评估物料保障能力。

**适用场景**：
- "哪些物料供不应求"
- "哪些物料库存过剩"
- "帮我看看供需平衡"

**前置条件**：`extracted_summary.parquet` 已生成，用户提供需求端数据。

**输入**：
- `extracted_summary.parquet`（必需）
- 需求端数据文件（Excel/CSV/JSON，必需）
- `supplier_report.json`（可选）

**输出**：
- `supply_demand_gap.json`：供需匹配报告

**命令行示例**：
```bash
uv run skills/supply-demand-matcher/scripts/supply_demand_matcher.py \
  --supply projects/my_project/extracted_summary.parquet \
  --demand projects/my_project/demand_data.json \
  --output projects/my_project/supply_demand_gap.json
```

**需求端数据格式**（JSON 示例）：
```json
[
  {"物料编码": "1000001", "需求量": 5000},
  {"物料编码": "1000002", "需求量": 3500}
]
```

---

### Skill 06：库存计划与预警 (inventory-planner)

**用途**：核心分析 Skill，集成三道防线——需求预测（第一道防线）、库存计划（第二道防线）、执行预警（第三道防线）。

**适用场景**：
- "帮我算安全库存"
- "哪些物料该补货了"
- "生成需求预测"
- "调参优化预测模型"

**前置条件**：`extracted_weekly.parquet`、`extracted_summary.parquet`、`abc_xyz_result.json` 已生成。

**输入**：
- `extracted_weekly.parquet`（必需）
- `extracted_summary.parquet`（必需，用于生命周期字段）
- `abc_xyz_result.json`（必需）
- `optimal_params.json`（可选，调参结果）

**输出**：
- `forecast_result.json`：需求预测报告
- `inventory_plan.json`：库存计划报告
- `alert_list.json`：预警清单
- `optimal_params.json`：最优参数文件（调参产出）

**命令行示例**：

```bash
# 可选步骤：调参（建议定期执行）
uv run skills/inventory-planner/scripts/hyperparameter_tuner.py \
  --input projects/my_project/extracted_weekly.parquet \
  --output projects/my_project/optimal_params.json \
  --method auto --workers 4

# 步骤 1：需求预测（第一道防线）
uv run skills/inventory-planner/scripts/demand_forecast.py \
  --input projects/my_project/extracted_weekly.parquet \
  --output projects/my_project/forecast_result.json \
  --optimal-params projects/my_project/optimal_params.json

# 步骤 2：库存计划（第二道防线）
uv run skills/inventory-planner/scripts/inventory_planning.py \
  --data projects/my_project/extracted_weekly.parquet \
  --summary projects/my_project/extracted_summary.parquet \
  --classification projects/my_project/abc_xyz_result.json \
  --forecast projects/my_project/forecast_result.json \
  --output projects/my_project/inventory_plan.json \
  --std-method std_all

# 步骤 3：执行预警（第三道防线）
uv run skills/inventory-planner/scripts/inventory_alert.py \
  --data projects/my_project/extracted_weekly.parquet \
  --plan projects/my_project/inventory_plan.json \
  --summary projects/my_project/extracted_summary.parquet \
  --output projects/my_project/alert_list.json
```

**可选参数**：
| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--optimal-params` | 最优参数 JSON 文件路径 | 无 |
| `--std-method` | 标准差计算方式：`std_all`（全周期）/ `std_nonzero`（仅非零值） | std_all |
| `--lead-time-weeks` | 提前期（周） | 1 |
| `--ordering-cost` | 每次订货成本 | 100 |
| `--holding-rate` | 年持有成本率 | 0.2 |

**预测方法说明**：
| 需求模式 | 条件 | 自动选择的方法 |
|---------|------|-------------|
| 平滑需求 | ADI ≤ 1.32, CV² ≤ 0.49 | Holt / Holt-Winters |
| 波动需求 | ADI ≤ 1.32, CV² > 0.49 | Holt-Winters |
| 间歇性需求 | ADI > 1.32, CV² > 0.49 | TSB |
| 块状需求 | ADI > 1.32, CV² ≤ 0.49 | IMAPA |

---

### Skill 07：采购决策建议 (purchase-advisor)

**用途**：基于预警清单生成采购优先级排序、建议采购量计算、供应商分配建议、采购预算估算，并生成综合闭环报告。

**适用场景**：
- "生成采购计划"
- "帮我看看该买什么"
- "采购优先级排序"

**前置条件**：`alert_list.json` 已生成。

**输入**：
- `alert_list.json`（必需）
- `supplier_report.json`（可选）
- `supply_demand_gap.json`（可选）
- `inventory_plan.json`（可选，用于 EOQ 校验）

**输出**：
- `purchase_plan.json`：采购行动计划
- `final_report.json`：综合分析报告
- `action_history.json`：行动闭环记录

**命令行示例**：

```bash
# 步骤 1：采购计划生成
uv run skills/purchase-advisor/scripts/purchase_planner.py \
  --alerts projects/my_project/alert_list.json \
  --supply-demand projects/my_project/supply_demand_gap.json \
  --inventory-plan projects/my_project/inventory_plan.json \
  --output projects/my_project/purchase_plan.json

# 步骤 2：综合报告生成
uv run skills/purchase-advisor/scripts/report_generator.py \
  --project-dir projects/my_project/ \
  --output projects/my_project/final_report.json
```

**可选参数**：
| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--moq` | 最小起订量 | 0 |
| `--supplier-report` | 供应商报告路径 | 无 |

---

### Skill 08：供应链分析编排器 (supply-chain-orchestrator)

**用途**：顶层入口 Skill，引导用户选择分析模块，按依赖顺序自动编排子 Skill，追踪执行进度，汇总最终报告。

**适用场景**：
- "帮我全面分析库存"
- "从头开始跑一遍分析流程"
- "供应链全景分析"

**输入**：
- 原始数据文件路径（首次使用时必需）
- 项目工作目录路径

**输出**：
- 各子 Skill 的中间产出文件
- `final_report.json`：综合分析报告
- `action_history.json`：行动闭环记录

**命令行示例**：

```bash
# 查看模块状态
uv run skills/supply-chain-orchestrator/scripts/orchestrator.py \
  --project-dir projects/my_project/ \
  --status-only

# 空跑模式（仅生成执行计划）
uv run skills/supply-chain-orchestrator/scripts/orchestrator.py \
  --project-dir projects/my_project/ \
  --all --dry-run

# 执行全部可执行模块
uv run skills/supply-chain-orchestrator/scripts/orchestrator.py \
  --project-dir projects/my_project/ \
  --raw-data-dir ./raw_data/ \
  --all

# 执行指定模块
uv run skills/supply-chain-orchestrator/scripts/orchestrator.py \
  --project-dir projects/my_project/ \
  --modules 1,2,3
```

**交互模式**：不指定 `--modules` 或 `--all` 参数时，编排器进入交互模式，展示模块状态菜单，引导用户选择。

---

## 五、典型工作流

### 场景一：首次使用，完整流程

```bash
# 0. 环境搭建
cd sci/
uv venv --python 3.11.14 && uv sync

# 1. 数据验表与探查
uv run skills/data-inspector/scripts/data_extractor.py \
  --input ./raw_data/ \
  --output ./projects/my_project/

# 2. 库存全景分析
uv run skills/inventory-overview/scripts/data_aggregator.py \
  --input projects/my_project/extracted_summary.parquet \
  --output projects/my_project/inventory_overview.json

# 3. 分类与策略
uv run skills/category-classifier/scripts/abc_classifier.py \
  --input projects/my_project/extracted_summary.parquet \
  --output projects/my_project/abc_xyz_result.json

uv run skills/category-classifier/scripts/xyz_classifier.py \
  --input projects/my_project/extracted_weekly.parquet \
  --output projects/my_project/abc_xyz_result.json --append

# 4. 库存计划与预警（三道防线）
uv run skills/inventory-planner/scripts/demand_forecast.py \
  --input projects/my_project/extracted_weekly.parquet \
  --output projects/my_project/forecast_result.json

uv run skills/inventory-planner/scripts/inventory_planning.py \
  --data projects/my_project/extracted_weekly.parquet \
  --summary projects/my_project/extracted_summary.parquet \
  --classification projects/my_project/abc_xyz_result.json \
  --forecast projects/my_project/forecast_result.json \
  --output projects/my_project/inventory_plan.json

uv run skills/inventory-planner/scripts/inventory_alert.py \
  --data projects/my_project/extracted_weekly.parquet \
  --plan projects/my_project/inventory_plan.json \
  --summary projects/my_project/extracted_summary.parquet \
  --output projects/my_project/alert_list.json

# 5. 采购决策建议
uv run skills/purchase-advisor/scripts/purchase_planner.py \
  --alerts projects/my_project/alert_list.json \
  --inventory-plan projects/my_project/inventory_plan.json \
  --output projects/my_project/purchase_plan.json

uv run skills/purchase-advisor/scripts/report_generator.py \
  --project-dir projects/my_project/ \
  --output projects/my_project/final_report.json
```

### 场景二：使用编排器一键执行

```bash
# 查看状态
uv run skills/supply-chain-orchestrator/scripts/orchestrator.py \
  --project-dir projects/my_project/ --status-only

# 执行全部
uv run skills/supply-chain-orchestrator/scripts/orchestrator.py \
  --project-dir projects/my_project/ --raw-data-dir ./raw_data/ --all
```

### 场景三：仅更新预警清单（数据更新后）

```bash
# 重新提取数据
uv run skills/data-inspector/scripts/data_extractor.py \
  --input ./raw_data/ --output ./projects/my_project/

# 重新生成预警
uv run skills/inventory-planner/scripts/demand_forecast.py \
  --input projects/my_project/extracted_weekly.parquet \
  --output projects/my_project/forecast_result.json

uv run skills/inventory-planner/scripts/inventory_planning.py \
  --data projects/my_project/extracted_weekly.parquet \
  --summary projects/my_project/extracted_summary.parquet \
  --classification projects/my_project/abc_xyz_result.json \
  --forecast projects/my_project/forecast_result.json \
  --output projects/my_project/inventory_plan.json

uv run skills/inventory-planner/scripts/inventory_alert.py \
  --data projects/my_project/extracted_weekly.parquet \
  --plan projects/my_project/inventory_plan.json \
  --summary projects/my_project/extracted_summary.parquet \
  --output projects/my_project/alert_list.json
```

---

## 六、常见问题

**Q1：我的 ERP 导出文件格式不同，能用吗？**
A：能。Skill 01 支持多级表头解析、编码自动检测（UTF-8/GBK）、三级列名映射，适配 SAP、金蝶、用友、浪潮等主流 ERP 系统的导出格式。尾部合计行通过跨列关键词自动排除，不依赖特定物料编码格式。

**Q2：我没有物料主数据文件，能分析吗？**
A：能。所有物料默认使用"正常在售"状态，保质期约束不生效。如需启用食品行业的效期预警，请提供包含"保质期天数"和"生产日期"的物料主数据文件。

**Q3：预测模型该选哪个？**
A：系统会自动根据数据特征选择最优模型。您也可以通过 `hyperparameter_tuner.py` 进行离线调参优化，或手动指定 `--method` 参数。

**Q4：数据量很大（10,000+ SKU），性能如何？**
A：所有脚本基于 Polars 高性能引擎构建，支持流式处理和并行计算。在 M1 Pro (8 CPU, 16GB RAM) 上，10,000 SKU × 260 周的完整分析链路可在 120 秒内完成。

**Q5：如何验证分析结果的正确性？**
A：运行 `uv run pytest tests/ -v` 可执行 19+ 项自动化测试。端到端测试产出文件保存在 `projects/e2e_test/` 目录中，可直接查看验证。

---

## 七、文件结构速查

```
sci/
├── pyproject.toml                     # 项目配置
├── templates/                          # 原始模板（只读）
├── projects/                           # 处理实例
│   └── {项目名}/
│       ├── extracted_summary.parquet   # 汇总数据
│       ├── extracted_weekly.parquet    # 周度数据
│       ├── forecast_result.json        # 需求预测
│       ├── inventory_plan.json         # 库存计划
│       ├── alert_list.json             # 预警清单
│       ├── purchase_plan.json          # 采购计划
│       ├── final_report.json           # 综合报告
│       └── error_report.json           # 错误报告
├── skills/
│   └── data-preparation/
│       ├── data-inspector/             # Skill 01
│       ├── inventory-overview/         # Skill 02
│       ├── category-classifier/        # Skill 03
│       ├── supplier-analyzer/          # Skill 04
│       ├── supply-demand-matcher/      # Skill 05
│       ├── inventory-planner/          # Skill 06
│       ├── purchase-advisor/           # Skill 07
│       └── supply-chain-orchestrator/  # Skill 08
└── tests/                              # 自动化测试
```

---

> **下一步**：从 Skill 01（data-inspector）开始，提供您的原始库存明细文件，系统将自动引导您完成全部分析。