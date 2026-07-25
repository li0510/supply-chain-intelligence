---
name: category-classifier
description: >
  触发此 Skill 的场景包括但不限于："ABC分类""XYZ分类""物料分类"
  "库存自存法分类""ABC-XYZ组合矩阵""帮我分一下物料等级"
  "哪些是A类物料""需求波动分析""品类管控策略"。
  当用户需要对已提取的结构化库存数据进行 ABC 分类（基于出库金额）、
  XYZ 分类（基于需求波动）、生成组合矩阵与差异化管控策略时使用。
---

# 分类与策略 (Category Classifier)

## 核心能力
调用内置的确定性 Python 脚本，完成以下操作：
- 品类策略回顾（如有历史策略文件）
- ABC 分类：基于出库金额占比，按帕累托原则分为 A/B/C 三类
- XYZ 分类（周度校准版）：基于周度出库量的变异系数（CV），阈值 X≤0.3 / Y≤0.8（可通过命令行参数自定义）
- ABC-XYZ 组合矩阵：9 种详细管控策略（含服务水平、补货机制、盘点频率、安全库存策略）
- 生命周期增强：结合物料生命周期状态优化分类（如 CZ + 衰退 → 建议淘汰）
- 差异化管控建议：针对不同组合类型输出定制化策略

## 输入
- `extracted_summary.parquet`：来自 data-inspector 的汇总数据（**ABC 分类使用**）
- `extracted_weekly.parquet`：来自 data-inspector 的周度数据（**XYZ 分类使用**）
- `efficiency_cost_report.json`：效率成本报告（可选，用于生命周期增强，通过 `--cost-report` 指定）
- `category_strategy.json`：历史品类策略文件（可选）

## 输出
- `abc_xyz_result.json`：ABC-XYZ 分类结果与管控策略

---

## 执行流程

### 步骤 0：环境验证
- 检查 Python 版本是否为 3.11.14，若不匹配则提示用户安装/切换并终止。
- 检查 `uv` 是否可用，若不可用则提示安装并终止。
- 所有脚本调用均使用 `uv run`。

### 步骤 1：前置条件检查
调用 `precondition_checker.py` 检查：
- 必需文件：`extracted_summary.parquet`（ABC 分类）、`extracted_weekly.parquet`（XYZ 分类）
- 可选文件：`efficiency_cost_report.json`、`category_strategy.json`

若必需文件缺失，拒绝执行并告知用户获取路径。

### 步骤 2：品类策略回顾（可选）
若存在 `category_strategy.json`，展示上次策略摘要，询问用户是否需要调整。
若不存在，跳过此步骤。

### 步骤 3：ABC 分类
调用 `abc_classifier.py`：
```bash
uv run scripts/abc_classifier.py --input <extracted_summary.parquet路径> \
  --output <abc_xyz_result.json路径>
```

输出内容包括：
- 各物料出库金额占比
- 累计占比
- ABC 分类结果（A: 前70%, B: 70-90%, C: 90-100%）

**条件依赖**：若缺少金额数据，按出库数量分类。

### 步骤 4：XYZ 分类 + 生命周期增强
调用 `xyz_classifier.py`：
```bash
uv run scripts/xyz_classifier.py --input <extracted_weekly.parquet路径> \
  --output <abc_xyz_result.json路径> --append \
  [--x-threshold <X阈值>] [--y-threshold <Y阈值>] \
  [--cost-report <efficiency_cost_report.json路径>]
```

输出内容包括：
- 各物料需求变异系数（CV = 周出库标准差 / 周出库均值）
- XYZ 分类结果（X: CV ≤ 0.3, Y: 0.3 < CV ≤ 0.8, Z: CV > 0.8，阈值可通过 `--x-threshold`、`--y-threshold` 自定义）
- ABC-XYZ 组合矩阵
- 9 宫格差异化管控策略（含服务水平、补货机制、盘点频率）
- 生命周期增强结果（需通过 `--cost-report` 提供效率成本报告）

### 步骤 5：结果反馈
- `abc_xyz_result.json` 路径
- 分类统计摘要（A/B/C 各多少、X/Y/Z 各多少、组合分布）
- 重点管控清单（AX/BX/AY 等关键组合）
- 生命周期增强建议

---

## 注意事项（职责边界）
- 本 Skill 不自行编写任何分类算法代码，所有处理逻辑由预置脚本确定性执行。
- ABC 分类依赖汇总数据（`extracted_summary.parquet`），XYZ 分类依赖周度数据（`extracted_weekly.parquet`）。
- XYZ 分类基于周度出库量的变异系数，阈值可通过命令行参数自定义。
- 数据量少于 3 条时无法计算 CV，标记为"数据不足"。
- 生命周期增强为可选功能，需提供效率成本报告。

## 接口约定
- 上游：data-inspector（`extracted_summary.parquet` + `extracted_weekly.parquet`）、inventory-overview（`efficiency_cost_report.json`，可选）
- 下游：`abc_xyz_result.json` 供 inventory-planner、supply-chain-orchestrator 消费