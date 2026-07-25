---
name: supply-chain-orchestrator
description: >
  触发此 Skill 的场景包括但不限于："供应链分析""库存分析全流程"
  "帮我全面分析库存""供应链数据诊断""从头开始分析库存数据"
  "完整走一遍分析流程""供应链全景分析"。
  当用户需要进行完整的供应链数据分析，从数据验表到采购建议的全流程，
  或需要选择性地执行多个分析模块时使用。
  本 Skill 是供应链智能分析平台的顶层入口，负责引导用户选择分析模块、
  按依赖顺序编排子 Skill、汇总最终报告。
---

# 供应链分析编排器 (Supply Chain Orchestrator)

## 核心能力
调用内置的确定性 Python 脚本，完成以下操作：
- 引导式模块选择：展示全部可用分析模块及其状态（已完成/可执行/不可执行）
- 依赖解析：自动判断各模块的前置条件是否满足
- 按序编排执行：根据依赖关系顺序调度子 Skill
- 进度追踪：实时反馈当前执行进度
- 综合报告验证：验证 `purchase-advisor` 已自动生成的最终报告文件

## 可用子 Skill 模块

| # | 模块名称 | 功能 | 前置条件 |
|---|---------|------|---------|
| 1 | data-inspector | 数据验表与探查 | 原始数据文件（用户提供） |
| 2 | inventory-overview | 库存全景分析 | `extracted_summary.parquet` |
| 3 | category-classifier | 分类与策略 | `extracted_summary.parquet` + `extracted_weekly.parquet` |
| 4 | supplier-analyzer | 供应商分析 | `extracted_summary.parquet`（需供应商字段） |
| 5 | supply-demand-matcher | 供需匹配 | `extracted_summary.parquet` + 需求端数据 |
| 6 | inventory-planner | 库存计划与预警 | `extracted_weekly.parquet` + `extracted_summary.parquet` + `abc_xyz_result.json` |
| 7 | purchase-advisor | 采购决策建议 | `alert_list.json` |

## 输入
- 原始数据文件路径（用户提供，首次使用时必需）
- 项目工作目录路径（用于存放所有中间产出和最终报告）
- 可选：需求端数据文件路径（用于供需匹配）
- 可选：历史行动记录（自动从项目目录读取）

## 输出
- 各子 Skill 的中间产出文件（存放在项目目录中）
- `final_report.json`：综合分析报告
- `action_history.json`：行动闭环记录

---

## 执行流程

### 步骤 0：环境验证
- 检查 Python 版本是否为 3.11.14，若不匹配则提示用户安装/切换并终止。
- 检查 `uv` 是否可用，若不可用则提示安装并终止。
- 所有脚本调用均使用 `uv run`。

### 步骤 1：收集基础信息
- 询问用户原始数据文件路径（若首次使用）。
- 询问用户项目工作目录命名方式（按项目名 / 按源文件名）。
- 创建项目工作目录（若不存在）。

### 步骤 2：模块状态扫描
调用 `orchestrator.py` 的扫描功能：
- 扫描项目目录中已有的中间文件（`extracted_summary.parquet`、`extracted_weekly.parquet`、`abc_xyz_result.json`、`alert_list.json` 等）。
- 判断每个子 Skill 的可用状态：
  - ✅ 已完成：该模块的产出文件已存在
  - 🔄 可执行：前置条件已满足，可以运行
  - ❌ 不可执行：前置条件缺失
  - ⚠️ 部分可执行：前置条件部分满足

### 步骤 3：模块选择引导
向用户展示模块状态菜单，引导选择需要执行的分析模块。

菜单示例：
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 供应链分析模块状态

1. ✅ 数据验表与探查 (data-inspector)
2. 🔄 库存全景分析 (inventory-overview)
3. 🔄 分类与策略 (category-classifier)
4. ❌ 供应商分析 (supplier-analyzer) — 缺供应商字段
5. ❌ 供需匹配 (supply-demand-matcher) — 缺需求数据
6. 🔄 库存计划与预警 (inventory-planner)
7. 🔄 采购决策建议 (purchase-advisor)

请选择需要执行的模块（输入编号，多个用逗号分隔）：
或输入 "all" 执行全部可执行模块。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

对于 ❌ 状态的模块，主动告知缺失内容和获取路径。

### 步骤 4：依赖解析与排序
调用 `orchestrator.py` 的依赖解析功能：
- 根据用户选择的模块，自动解析依赖链。
- 按依赖顺序排列执行顺序。
- 检查是否有循环依赖（当前架构不存在此问题）。
- 生成执行计划并展示给用户确认。

### 步骤 5：按序执行
按执行计划依次调用各子 Skill：
- 每步执行前，由**各子 Skill 自行调用 `precondition_checker.py`** 检查前置条件（防止中间步骤失败导致后续异常）。
- supply-chain-orchestrator 不直接调用 `precondition_checker.py`——它使用自己的 `scan_module_status` 函数进行文件存在性检查。
- 每步执行完成后更新模块状态。
- 若某步骤失败，询问用户是否跳过继续或终止。
- 实时输出执行进度。

### 步骤 6：综合报告验证
`purchase-advisor` 子 Skill 在执行过程中已自动调用其内部脚本 `report_generator.py` 生成综合报告和行动闭环记录。
supply-chain-orchestrator 在此步骤中验证以下文件是否存在于项目工作目录中：
- `final_report.json`
- `action_history.json`

### 步骤 7：结果反馈
- 执行摘要（哪些模块成功、哪些跳过、哪些失败）
- `final_report.json` 路径
- 关键指标摘要
- 下一步行动建议

---

## 注意事项（职责边界）
- 本 Skill 只负责编排调度，不执行具体的数据分析逻辑。
- 编排器不处理数据，所有数据处理由各子 Skill 的预置脚本确定性执行。
- supply-chain-orchestrator 不直接调用子 Skill 的内部脚本（如 `report_generator.py`），综合报告由 `purchase-advisor` 在自身流程中自动生成。
- supply-chain-orchestrator 使用自己的 `scan_module_status` 进行状态扫描，各子 Skill 在执行时自行调用 `precondition_checker.py`。
- 若用户选择跳过某些模块，后续模块的前置条件可能不满足，编排器会明确提示。
- 编排器支持断点续跑：若某模块已有产出文件，默认跳过重复执行。

## 接口约定
- 上游：用户提供的原始数据文件 + 需求端数据（可选）。
- 下游：`final_report.json` 和 `action_history.json` 为最终交付物。
- 所有子 Skill 的中间文件统一存放在项目工作目录中。