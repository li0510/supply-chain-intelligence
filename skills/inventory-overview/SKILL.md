---
name: inventory-overview
description: >
  触发此 Skill 的场景包括但不限于："库存全景分析""库存总览""进销存汇总"
  "库存周转分析""库存成本分析""库存资金占用""物料产品流分析"
  "帮我看看库存整体情况""算一下库存周转率""库存资金占了多少"。
  当用户需要对已提取的结构化库存数据进行存量和流量全景分析、
  周转效率分析、成本与资金分析、产品流动分析时使用。
---

# 库存全景分析 (Inventory Overview)

## 核心能力
调用内置的确定性 Python 脚本，完成以下操作：
- 存量总览：总库存数量/金额、按物料分类汇总、TOP-N/LAST-N 排名
- 流量总览：入库/出库汇总、净增/净减趋势、同比/环比变化
- 周转效率（周度精确版）：库存周转率 = 总出库量 / 平均周库存（Σ每周结存 / 可用周数）、周转天数 = 91 / 周转率、周转周数 = 13 / 周转率
- 库存持有天数（DOH）：当前结存 / 周均出库量 × 7
- 呆滞库存识别（周转周数 > 12 周）+ 呆滞库存金额占比
- 资金效率：库存占用资金、资金周转天数、出入库金额分析
- 总成本视角（TCO）：持有成本 + 采购成本 + 缺货成本估算
- 产品流分析：物料流向、需求变化趋势、生命周期判断

## 输入
- `extracted_summary.parquet`：来自 data-inspector 的汇总数据（**必需**）
- `extracted_weekly.parquet`：来自 data-inspector 的周度数据（**用于周转率精确计算**）
- `raw_data_profile.json`：数据探查报告（可选，用于辅助分析）

## 输出
- `inventory_overview.json`：库存全景分析报告
- `efficiency_cost_report.json`：效率与成本分析报告

---

## 执行流程

### 步骤 0：环境验证
- 检查 Python 版本是否为 3.11.14，若不匹配则提示用户安装/切换并终止。
- 检查 `uv` 是否可用，若不可用则提示安装并终止。
- 所有脚本调用均使用 `uv run`。

### 步骤 1：前置条件检查
调用 `precondition_checker.py` 检查：
- 必需文件：`extracted_summary.parquet`
- 可选文件：`extracted_weekly.parquet`、`raw_data_profile.json`

若必需文件缺失，拒绝执行并告知用户获取路径。
若可选文件缺失，提醒用户部分功能可能受限。

### 步骤 2：库存全景分析
调用 `data_aggregator.py`：
```bash
uv run scripts/data_aggregator.py --input <extracted_summary.parquet路径> \
  --output <inventory_overview.json路径>
```

输出内容包括：
- 存量总览：总库存量、按分类汇总、TOP-10/LAST-10
- 流量总览：总入库量、总出库量、净增/净减

### 步骤 3：周转效率分析
调用 `inventory_turnover.py`：
```bash
uv run scripts/inventory_turnover.py --input <extracted_summary.parquet路径> \
  --weekly <extracted_weekly.parquet路径> \
  --output <efficiency_cost_report.json路径>
```

输出内容包括：
- 各物料周转率（周度精确版：总出库量 / 平均周库存）
- 周转天数 = 91 / 周转率
- 周转周数 = 13 / 周转率
- DOH（库存持有天数）= 当前结存 / 周均出库量 × 7
- 呆滞库存清单（周转周数 > 12 周）
- 呆滞库存金额占比
- 周转效率排名

### 步骤 4：成本与资金分析
调用 `cost_analyzer.py`：
```bash
uv run scripts/cost_analyzer.py --input <extracted_summary.parquet路径> \
  --output <efficiency_cost_report.json路径> --append
```

输出内容包括：
- 库存占用资金（如有单价字段）
- 资金周转天数
- TCO 总成本估算
- 产品流分析（物料流向、生命周期判断）

**条件依赖**：若缺少单价字段，成本与资金分析部分自动跳过，并告知用户。

### 步骤 5：结果反馈
- `inventory_overview.json` 路径
- `efficiency_cost_report.json` 路径
- 分析摘要（总库存、周转率、呆滞占比、预警项）

---

## 注意事项（职责边界）
- 本 Skill 不自行编写任何数据分析代码，所有处理逻辑由预置脚本确定性执行。
- 单价字段缺失时，成本与资金分析自动跳过，不影响其他分析。
- 周转率基于周度数据精确计算（平均周库存 = Σ每周结存 / 可用周数），而非简化版 (期初+期末)/2。
- 所有数值计算使用 Float32/Float64 精度。

## 接口约定
- 上游：data-inspector 产出的 `extracted_summary.parquet` + `extracted_weekly.parquet`
- 下游：`inventory_overview.json` 和 `efficiency_cost_report.json` 供 category-classifier、supply-chain-orchestrator 消费