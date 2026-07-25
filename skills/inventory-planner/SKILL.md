---
name: inventory-planner
description: >
  触发此 Skill 的场景包括但不限于："库存计划""安全库存计算""再订购点"
  "ROP计算""最高库存""库存预警""缺货预警""积压预警""采购提醒"
  "三道防线""库存策略""帮我算安全库存""哪些物料该补货了"
  "需求预测""调参优化""间歇性需求""慢流动物料""效期预警"。
  当用户需要基于历史数据和分类结果计算安全库存、再订购点、
  最高库存，并生成分类差异化的缺货/积压/采购/效期预警清单时使用。
---

# 库存计划与预警 (Inventory Planner)

## 核心能力
调用内置的确定性 Python 脚本，完成以下操作：

### 第一道防线：需求预测
- 生命周期状态感知预测：
  - **新品上市**：无历史数据时使用类比法，少量数据时使用 SES，标记 `预测方法: 新品类比`
  - **正常在售**：正常执行统计预测
  - **老品下市**：预测值 = 0（清仓预测），保留清仓期需求估计
  - **已淘汰**：跳过预测
- 需求模式自动分类：基于 ADI（Average Demand Interval）和 CV²（变异系数平方）自动识别四类需求模式：
  - **Smooth（平滑需求）**：ADI ≤ 1.32, CV² ≤ 0.49 → Holt / Holt-Winters
  - **Erratic（波动需求）**：ADI ≤ 1.32, CV² > 0.49 → Holt-Winters
  - **Intermittent（间歇性需求）**：ADI > 1.32, CV² > 0.49 → **TSB（Teunter-Syntetos-Babai）**
  - **Lumpy（块状需求）**：ADI > 1.32, CV² ≤ 0.49 → **IMAPA（多粒度聚合预测）**
- 多统计方法支持：
  - SES（简单指数平滑）— 数据点 < 8 周
  - Holt's Linear（趋势调整指数平滑）— 数据点 8~103 周
  - Holt-Winters（季节指数平滑）— 数据点 ≥ 104 周，基于 STL 分解 + 假设检验自动选择加法/乘法模型
  - TSB — 间歇性需求，基于需求概率的独立指数平滑
  - IMAPA — 块状需求，基于多时间粒度聚合（1/2/4 周）+ SES 选择最优窗口
- 预测评估指标：MAE、RMSE、Bias
- 预测区间（Prediction Interval）：基于服务水平 Z 值动态计算
- 13 周滚动窗口标注
- 调参→预测完整链路：通过 `hyperparameter_tuner.py` 自动搜索最优参数，产出 `optimal_params.json`，`demand_forecast.py` 通过 `--optimal-params` 读取

### 第二道防线：库存计划
- 安全库存（含保质期约束）：
  - 公式：`Z × σ_week × √LT_weeks`（固定提前期）
  - 扩展公式：`Z × √(LT × σ²_demand + D²_weekly × σ²_LT)`（变动提前期）
  - **保质期上限**：`最大安全库存 = min(Z×σ×√LT, 周均需求×剩余保质期周数×0.8)`
- 再订购点 ROP：
  - 公式：`周均需求 × LT_weeks + 安全库存`
  - **动态 ROP**：输出当前周 ROP + 未来 4 周预测 ROP
  - AZ/CZ 物料标记 ROP = "不适用（MTO）"
- 最高库存：
  - 不定期补货：`ROP + EOQ`
  - 定期补货：`周均需求 × (补货周期 + LT_weeks) + 安全库存`
  - **保质期上限**：`最高库存 = min(ROP+EOQ, 周均需求×剩余保质期周数×0.6)`
- 补货策略矩阵（4 种机制）：
  - 定期定量（AX, BX）：按固定周期补货至最高库存
  - 定期不定量（AY, BY）：按固定周期动态调整补货量
  - 不定期定量（CX）：库存低于 ROP 时补 EOQ 量
  - 不定期不定量（AZ, BZ, CZ）：按订单采购或紧急补货
- 分类差异化：基于 ABC-XYZ 分类设置不同的服务水平（AX: 99% ~ CZ: 85%）
- 生命周期差异化：
  - 新品上市：安全库存 × 2，服务水平 99%
  - 老品下市：安全库存 = 0，ROP = 停止补货
  - 已淘汰：跳过库存计划
- 间歇性需求适配：支持选择标准差计算方式（`std_all` 含零值 / `std_nonzero` 仅非零值）
- 库存水位指标：最低库存（SS）、目标库存（ROP）、当前水位（%）
- TCO 总成本估算：持有成本 + 订货成本 + 总成本

### 第三道防线：执行预警
- 缺货预警：当前结存 < 安全库存
- 积压预警：当前结存 > 最高库存
- 采购提醒：当前结存 < 再订购点
- 按分类差异化颜色标记（A类红色、B类橙色、C类黄色）
- 效期预警（食品行业核心）：
  - **效期警告**（高）：剩余保质期 ≤ 30 天 → 立即促销/折价处理/报废
  - **效期预警**（中）：剩余保质期 ≤ 保质期 × 30% → 优先消耗、调整补货计划
- **老品下市清仓预警**：`lifecycle_status = 老品下市` 且 `当前结存 > 0` → 启动清仓计划
- 预计缺货日期（可支撑周数）
- 建议补货日期（预计缺货日期 - 提前期）
- 预警趋势（↑↓→）
- 按供应商聚合

### 离线优化
- 高性能并行调参：`hyperparameter_tuner.py`
- 搜索空间：
  - SES：α ∈ [0.05, 0.95]，19 组合
  - Holt：α, β ∈ [0.05, 0.95]，361 组合
  - Holt-Winters：α, β, γ ∈ [0.05, 0.95]，6,859 组合
  - TSB：α, β ∈ [0.05, 0.40]，64 组合
  - IMAPA：窗口选择 [1, 2, 4]（及可选 8, 16），3 窗口
- 三级并行策略：ProcessPoolExecutor（SKU 级）+ ThreadPoolExecutor（参数级）+ NumPy 向量化
- 结果缓存：`optimal_params.json`，数据未变时跳过
- 调参目标：最小化 RMSE

## 输入
- `extracted_weekly.parquet`：来自 data-inspector 的周度数据（**必需**）
- `extracted_summary.parquet`：来自 data-inspector 的汇总数据（**必需**，用于库存计划中的生命周期字段）
- `abc_xyz_result.json`：来自 category-classifier 的分类结果（**必需**）
- `supply_demand_gap.json`：来自 supply-demand-matcher 的供需数据（可选）
- `optimal_params.json`：来自 hyperparameter_tuner.py 的调参结果（可选，通过 `--optimal-params` 指定）

## 输出
- `forecast_result.json`：需求预测报告（含 `parameter_source`、`需求模式` 字段）
- `inventory_plan.json`：库存计划报告（含 `标准差计算方式` 字段）
- `alert_list.json`：预警清单（含 `expiry_alerts` 字段）
- `optimal_params.json`：最优参数文件（调参产出）

---

## 执行流程

### 步骤 0：环境验证
- 检查 Python 版本是否为 3.11.14，若不匹配则提示用户安装/切换并终止。
- 检查 `uv` 是否可用，若不可用则提示安装并终止。
- 所有脚本调用均使用 `uv run`。

### 步骤 1：前置条件检查
调用 `precondition_checker.py` 检查：
- 必需文件：`extracted_weekly.parquet`、`extracted_summary.parquet`、`abc_xyz_result.json`
- 可选文件：`supply_demand_gap.json`

若必需文件缺失，拒绝执行并告知用户获取路径：
- 缺 `extracted_weekly.parquet` → "请先运行 data-inspector 完成数据验表与提取。"
- 缺 `extracted_summary.parquet` → "请先运行 data-inspector 完成数据验表与提取。"
- 缺 `abc_xyz_result.json` → "请先运行 category-classifier 完成 ABC-XYZ 分类。"
若 `supply_demand_gap.json` 缺失，提醒用户：将基于历史出库数据估算需求预测，精准度可能不足。

### 步骤 2：离线调参（可选，建议定期执行）
调用 `hyperparameter_tuner.py`：
```bash
uv run scripts/hyperparameter_tuner.py --input <extracted_weekly.parquet路径> \
  --output <optimal_params.json路径> \
  --method auto --workers 4
```
- 为每个物料自动搜索最优预测参数
- 自动识别需求模式：间歇性→TSB，块状→IMAPA，平滑→Holt/Holt-Winters
- 自动跳过生命周期异常物料（已淘汰/老品下市）
- 调参结果缓存，数据未变时自动跳过
- 使用 `--force` 强制重新调参

### 步骤 3：需求预测（第一道防线）
调用 `demand_forecast.py`：
```bash
uv run scripts/demand_forecast.py --input <extracted_weekly.parquet路径> \
  --output <forecast_result.json路径> \
  [--optimal-params <optimal_params.json路径>] \
  [--imapa-max-window <IMAPA最大聚合窗口>]
```

输出内容包括：
- 各物料的需求模式分类（Smooth/Erratic/Intermittent/Lumpy）
- 生命周期状态感知预测
- 预测方法和最优参数
- 预测区间（Prediction Interval）
- MAE、RMSE、Bias 评估指标
- `parameter_source` 字段标注参数来源（"optimal_params" 或 "default"）

**条件依赖**：若历史数据点不足（< 3 条），使用简单均值作为预测，并告知用户。

### 步骤 4：库存计划（第二道防线）
调用 `inventory_planning.py`：
```bash
uv run scripts/inventory_planning.py \
  --data <extracted_weekly.parquet路径> \
  --summary <extracted_summary.parquet路径> \
  --classification <abc_xyz_result.json路径> \
  --forecast <forecast_result.json路径> \
  --output <inventory_plan.json路径> \
  [--lead-time-weeks <提前期>] [--ordering-cost <订货成本>] \
  [--holding-rate <持有成本率>] [--sigma-lt <提前期标准差>] \
  [--std-method <std_all|std_nonzero>]
```

输出内容包括：
- 安全库存（含保质期约束和标准差计算方式标注）
- 再订购点 ROP（含动态 ROP 未来 4 周）
- 最高库存（含保质期上限）
- 经济订货批量 EOQ
- 补货策略分配（基于 ABC-XYZ 矩阵 + 生命周期状态）
- 库存水位指标
- TCO 总成本估算

**条件依赖**：若 `--summary` 不提供，保质期约束和生命周期策略不生效。
**间歇性物料建议**：使用 `--std-method std_nonzero` 以获得更合理的标准差估计。

### 步骤 5：执行预警（第三道防线）
调用 `inventory_alert.py`：
```bash
uv run scripts/inventory_alert.py \
  --data <extracted_weekly.parquet路径> \
  --plan <inventory_plan.json路径> \
  --summary <extracted_summary.parquet路径> \
  --output <alert_list.json路径> \
  [--supplier-report <supplier_report.json路径>]
```

输出内容包括：
- 缺货预警：当前结存 < 安全库存
- 积压预警：当前结存 > 最高库存
- 采购提醒：当前结存 < 再订购点
- 效期警告（高）：剩余保质期 ≤ 30 天
- 效期预警（中）：剩余保质期 ≤ 总保质期 × 30%
- 老品下市清仓预警
- 预计缺货日期、建议补货日期、预警趋势（↑↓→）
- 按分类差异化颜色标记
- 按供应商聚合

### 步骤 6：结果反馈
- `forecast_result.json` 路径
- `inventory_plan.json` 路径
- `alert_list.json` 路径
- `optimal_params.json` 路径（如有调参）
- 预警摘要（缺货 N 项、积压 M 项、需采购 K 项、效期 L 项）
- TOP-N 紧急采购清单

---

## 注意事项（职责边界）
- 本 Skill 不自行编写任何分析代码，所有处理逻辑由预置脚本确定性执行。
- 需求模式分类基于 Syntetos et al. (2005) 的 ADI + CV² 标准，自动完成。
- TSB 方法依据 Teunter, Syntetos & Babai (2011)，适用于间歇性需求。
- IMAPA 方法依据 Nikolopoulos et al. (2011) 的 ADIDA 框架，适用于块状需求。
- 安全库存计算基于正态分布假设，实际业务中可根据物料特性调整。
- 保质期约束依赖物料主数据中的"保质期天数"字段，缺失时不生效。
- 效期预警依赖物料主数据中的"生产日期"和"保质期天数"字段。
- 调参为离线任务，建议每月/每季度执行一次。

## 接口约定
- 上游：data-inspector（`extracted_weekly.parquet` + `extracted_summary.parquet`）、category-classifier（`abc_xyz_result.json`）、supply-demand-matcher（`supply_demand_gap.json`，可选）
- 下游：`alert_list.json`、`inventory_plan.json`、`forecast_result.json`、`optimal_params.json` 供 purchase-advisor、supply-chain-orchestrator 消费