---
name: supply-demand-matcher
description: >
  触发此 Skill 的场景包括但不限于："供需匹配""供需平衡分析"
  "缺货分析""过剩分析""需求缺口""供应缺口""帮我看看供需平衡"
  "哪些物料供不应求""哪些物料库存过剩""供需匹配报告"。
  当用户需要对已提取的结构化库存数据进行供给端与需求端的对比分析，
  识别供需缺口，评估物料保障能力时使用。
---

# 供需匹配分析 (Supply-Demand Matcher)

## 核心能力
调用内置的确定性 Python 脚本，完成以下操作：
- 供给端数据整合：现有库存 + 在途量 + 供应商产能
- 需求端数据整合：生产计划、销售订单、预测需求量
- 供需缺口计算：供不应求（缺口）/ 供过于求（过剩）
- 缺口分析报告：按物料列出缺口量、缺口占比、紧急程度
- 供应商产能匹配：将缺口分配给可用的供应商

## 输入
- `extracted_summary.parquet`：来自 data-inspector 的汇总数据（**必需**）
- `supplier_report.json`：供应商评估报告（可选，用于产能匹配）
- 需求端数据（用户提供）：
  - 生产计划文件（Excel/CSV）
  - 或销售订单文件
  - 或手动输入的需求数据（JSON 格式）

## 输出
- `supply_demand_gap.json`：供需匹配分析报告

---

## 执行流程

### 步骤 0：环境验证
- 检查 Python 版本是否为 3.11.14，若不匹配则提示用户安装/切换并终止。
- 检查 `uv` 是否可用，若不可用则提示安装并终止。
- 所有脚本调用均使用 `uv run`。

### 步骤 1：前置条件检查
调用 `precondition_checker.py` 检查：
- 必需文件：`extracted_summary.parquet`
- 可选文件：`supplier_report.json`
- 用户输入：需求端数据文件路径

若必需文件缺失，拒绝执行并告知用户获取路径。
若需求端数据缺失，提示用户：
"未提供需求端数据（生产计划或销售订单）。供需匹配分析无法执行。
如需此分析，请提供需求数据文件路径，或在下一步手动输入需求数量。"

### 步骤 2：加载需求端数据
引导用户提供需求数据：
- 若用户提供文件路径，自动检测并读取（Excel/CSV/JSON）。
- 若用户选择手动输入，引导逐物料录入需求数量。
- 若用户跳过，使用历史出库数据作为简单需求估计，并告知用户。

### 步骤 3：供需匹配计算
调用 `supply_demand_matcher.py`：
```bash
uv run scripts/supply_demand_matcher.py --supply <extracted_summary.parquet路径> \
  --demand <需求数据文件路径或JSON> \
  --supplier-report <supplier_report.json路径(可选)> \
  --output <supply_demand_gap.json路径>
```

输出内容包括：
- 各物料供给量（现有库存 + 在途量）
- 各物料需求量
- 缺口量（需求 - 供给）
- 缺口占比（缺口量 / 需求量）
- 供需状态（充足 / 偏紧 / 短缺 / 过剩）

### 步骤 4：供应商产能匹配（可选）
若提供了 `supplier_report.json`，调用产能匹配模块：
- 将短缺物料的缺口量分配给评级最高的可用供应商。
- 输出推荐供应商清单和分配量。

### 步骤 5：结果反馈
- `supply_demand_gap.json` 路径
- 供需平衡概览（总需求、总供给、总缺口）
- 短缺物料清单（TOP-N 最大缺口）
- 过剩物料清单
- 供应商分配建议（如有）

---

## 注意事项（职责边界）
- 本 Skill 不自行编写任何分析代码，所有处理逻辑由预置脚本确定性执行。
- 需求端数据是必需输入，缺失时无法执行核心匹配逻辑。
- 若仅有历史出库数据而无明确需求计划，匹配结果仅供参考。
- 供需匹配基于当前快照，不考虑未来的在途变化。

## 接口约定
- 上游：data-inspector 产出的 `extracted_summary.parquet`，用户提供的需求端数据，supplier-analyzer 产出的 `supplier_report.json`（可选）
- 下游：`supply_demand_gap.json` 供 inventory-planner、purchase-advisor、supply-chain-orchestrator 消费