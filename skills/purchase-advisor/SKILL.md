---
name: purchase-advisor
description: >
  触发此 Skill 的场景包括但不限于："采购建议""采购计划""采购优先级"
  "补货建议""采购订单生成""帮我看看该买什么""采购量建议"
  "生成采购清单""综合报告""采购行动报告"。
  当用户需要基于预警清单和供应商数据生成采购优先级排序、
  建议采购量、供应商分配建议以及综合闭环报告时使用。
---

# 采购决策建议 (Purchase Advisor)

## 核心能力
调用内置的确定性 Python 脚本，完成以下操作：
- 采购优先级排序：基于 ABC 分类和紧急程度自动排序
- 建议采购量计算：需求量 + 安全库存 - 当前结存 - 在途量
- EOQ 校验：建议采购量 < EOQ 时提示合并采购以达到经济订货批量
- MOQ 校验：采购量 < 最小起订量时自动调整至 MOQ
- 多供应商分配建议：战略物料（A类）按 70:30 分配主供应商和备选供应商
- 采购预算估算：Σ(采购量 × 预估单价)，缺少单价时标注
- 综合报告生成：汇总所有上游分析结果，生成决策级报告
- 行动闭环记录：保存本次行动记录，供下次步骤 0 回顾

## 输入
- `alert_list.json`：来自 inventory-planner 的预警清单（**必需**）
- `supplier_report.json`：来自 supplier-analyzer 的供应商评估（可选）
- `supply_demand_gap.json`：来自 supply-demand-matcher 的供需数据（可选）
- `inventory_plan.json`：来自 inventory-planner 的库存计划（可选，用于获取 EOQ）
- `abc_xyz_result.json`：来自 category-classifier 的分类结果（可选，用于交叉验证）

## 输出
- `purchase_plan.json`：采购行动计划（含 `budget_estimation`、`eoq_moq_warnings` 字段）
- `final_report.json`：综合分析报告
- `action_history.json`：行动记录（用于下次步骤 0 回顾）

---

## 执行流程

### 步骤 0：环境验证
- 检查 Python 版本是否为 3.11.14，若不匹配则提示用户安装/切换并终止。
- 检查 `uv` 是否可用，若不可用则提示安装并终止。
- 所有脚本调用均使用 `uv run`。

### 步骤 1：前置条件检查
调用 `precondition_checker.py` 检查：
- 必需文件：`alert_list.json`
- 可选文件：`supplier_report.json`、`supply_demand_gap.json`、`inventory_plan.json`、`abc_xyz_result.json`

若 `alert_list.json` 缺失，拒绝执行并告知用户：
"缺少必需文件 alert_list.json。请先运行 inventory-planner 完成库存计划与预警。"

若可选文件缺失，提醒用户：
- 缺 `supplier_report.json` → "将仅输出建议采购量，不包含供应商分配。"
- 缺 `supply_demand_gap.json` → "将基于预警清单中的参数计算采购量。"
- 缺 `inventory_plan.json` → "将无法进行 EOQ 校验。"
- 缺 `abc_xyz_result.json` → "分类信息将从预警清单中提取。"

### 步骤 2：采购计划生成
调用 `purchase_planner.py`：
```bash
uv run scripts/purchase_planner.py \
  --alerts <alert_list.json路径> \
  [--supplier-report <supplier_report.json路径>] \
  [--supply-demand <supply_demand_gap.json路径>] \
  [--inventory-plan <inventory_plan.json路径>] \
  [--moq <最小起订量>] \
  --output <purchase_plan.json路径>
```

输出内容包括：
- 采购优先级排序（A 类缺货 > B 类低库存 > C 类补货）
- 建议采购量 = 需求量 + 安全库存 - 当前结存 - 在途量
- EOQ 校验提示（建议采购量远小于 EOQ 时提示合并采购）
- MOQ 校验（采购量 < MOQ 时自动调整）
- 建议下单日期（基于紧急程度和提前期）
- 多供应商分配（A 类物料按 70:30 分配主/备供应商）
- 采购预算估算（如有单价数据）

### 步骤 3：综合报告生成
调用 `report_generator.py`：
```bash
uv run scripts/report_generator.py \
  --project-dir <项目工作目录路径> \
  --output <final_report.json路径>
```

输出内容包括：
- 执行摘要（关键数字：总库存、预警项数、建议采购金额）
- 各模块分析结果汇总
- 未完成项清单（因数据缺失跳过的分析）
- 下一步行动建议

### 步骤 4：行动闭环记录
- 将本次执行的全部关键决策和行动记录保存为 `action_history.json`
- 包含时间戳、执行的 Skill 列表、关键决策摘要
- 该文件将在下次 data-inspector 的步骤 0 中被读取

### 步骤 5：结果反馈
- `purchase_plan.json` 路径
- `final_report.json` 路径
- 采购优先级摘要（紧急 N 项、本周 M 项、本月 K 项）
- EOQ/MOQ 校验警告
- 采购预算估算
- 下一步行动建议

---

## 注意事项（职责边界）
- 本 Skill 不自行编写任何分析代码，所有处理逻辑由预置脚本确定性执行。
- 采购建议基于预警清单和供需数据，实际下单前需人工审核。
- 供应商分配建议基于历史表现数据，不构成采购承诺。
- MOQ 可通过 `--moq` 参数设置，未设置时默认为 0（不限制）。
- 综合报告中的所有数值均为估算值，实际执行时需考虑市场价格波动。

## 接口约定
- 上游：inventory-planner（`alert_list.json`、`inventory_plan.json`）、supplier-analyzer（`supplier_report.json`）、supply-demand-matcher（`supply_demand_gap.json`）、category-classifier（`abc_xyz_result.json`）
- 下游：`purchase_plan.json` 和 `final_report.json` 供 supply-chain-orchestrator 消费，`action_history.json` 供下次 data-inspector 步骤 0 消费