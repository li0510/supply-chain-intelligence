---
name: data-inspector
description: >
  触发此 Skill 的场景包括但不限于："验表""检查库存数据""数据探查""数据结构分析"
  "帮我看看这张表能不能用""库存数据质量检查""进销存平衡校验"。
  当用户需要对任意格式的进销存类原始数据表进行结构分析、字段映射、
  数据提取、质量校验与平衡校验时使用。
---

# 数据验表与探查 (Data Inspector)

## 核心能力
调用内置的确定性 Python 脚本，完成以下操作：
- 上期行动回顾（如有历史记录）
- 原始数据结构分析（列名、类型、行数、合并单元格、多行表头）
- 三级列名匹配（精确 → 中文映射表 → 用户手动指定）
- 字段提取与类型转换
- 自动检测并排除尾部合计行（跨列关键词检测，默认在前七列中搜索"合计"/"总计"/"小计"，支持自定义关键词和列范围）
- 数据质量检查（缺失值、非数字值、异常波动）
- 进销存平衡校验（期末 = 期初 + 入库 - 出库）
- 物料主数据合并（通过 `--material-master` 加载物料主数据文件，包含生命周期字段）
- 多文件流式拼接
- 输出双文件：`extracted_summary.parquet`（汇总数据 + 生命周期字段）+ `extracted_weekly.parquet`（周度明细数据）
- 生成错误报告 `error_report.json`（含 `excluded_rows` 字段记录被排除的尾部合计行）

## 输入
- 用户提供的原始库存明细文件（单个或多个 `.csv` / `.xlsx` 文件）
- CSV 编码为 GBK，逗号分隔，必有表头行
- Excel 仅读取第一个 sheet
- 上期行动记录 `action_history.json`（可选）
- 物料主数据文件（可选，通过 `--material-master` 指定，支持 Excel/CSV/Parquet 格式）
- JSON 生命周期配置（可选，通过 `--lifecycle-config` 指定，适用于少量物料快速测试）

## 输出
- `extracted_summary.parquet`：提取并清洗后的汇总数据（含物料编码、库存量、入库数量、出库数量、结存数量 + 生命周期字段）
- `extracted_weekly.parquet`：物料 × ISO Week 的周度明细数据（含物料编码、ISO_Week、周入库量、周出库量、周结存）
- `raw_data_profile.json`：数据结构分析报告
- `error_report.json`：数据异常项清单（含 `excluded_rows` 字段记录被排除的尾部合计行）

---

## 执行流程

### 步骤 0：环境验证与虚拟环境创建
- 检查 Python 版本是否为 3.11.14，若不匹配则提示用户安装/切换并终止。
- 检查 `uv` 是否可用，若不可用则提示安装并终止。
- 在项目根目录下执行：
  ```bash
  uv venv --python 3.11.14
  uv sync
  ```
- 以上步骤完成后，后续所有脚本调用均使用 `uv run`。

### 步骤 1：收集用户决策（逐项确认，不可跳过）

#### 1.1 输入路径确认
- 询问用户：原始数据文件所在的文件夹路径是什么？
- 若用户提供了具体路径则使用，否则默认使用当前目录 `.`。
- 列出该路径下所有可处理的 `.csv` 和 `.xlsx` 文件，让用户确认是否正确。

#### 1.2 输出目录确认
- 询问用户工作目录的组织方式：
  - **按项目/批次**：用户输入项目名称 → 创建子目录 `./projects/{项目名}/`
  - **按源文件名**：取第一个源文件的 stem 名 → 创建子目录 `./projects/{源文件名}/`
- 若用户未指定，默认使用按源文件名。

#### 1.3 物料主数据文件确认（可选）
- 询问用户是否提供物料主数据文件（包含生命周期字段：生命周期状态、保质期天数、生产日期等）。
- 若提供，验证文件存在且可读。
- 若不提供，所有物料使用默认值（正常在售，无保质期约束）。

#### 1.4 排除规则确认（可选）
- 询问用户是否需要自定义尾部合计行的排除规则。
- 默认使用跨列关键词检测（在前七列中搜索"合计"/"总计"/"小计"）。
- 用户可通过 `--exclude-keywords` 和 `--exclude-columns` 自定义。

### 步骤 2：上期行动回顾（可选）
- 检查输出目录下是否存在 `action_history.json`。
- 若存在，展示上次行动摘要（处理时间、文件数、异常数）。
- 若不存在，告知用户并跳过。

### 步骤 3：数据结构分析
调用 `data_profiler.py`：
```bash
uv run scripts/data_profiler.py --input <输入路径> --output <输出目录>
```
- 输出 `raw_data_profile.json`，包含列名列表、数据类型推断、行数、合并单元格检测结果。
- 向用户展示分析报告摘要。

### 步骤 4：列映射确认
调用 `column_mapper.py` 进行三级匹配：
1. 第一级：精确匹配标准字段名
2. 第二级：预设中文别名表模糊匹配
3. 若前两级失败，向用户展示文件中所有列名，请用户手动指定映射关系。

若用户提供手动映射，传入 `--column-mapping` 参数。

### 步骤 5：字段提取与质量检查
调用 `data_extractor.py` + `data_validator.py`：
```bash
uv run scripts/data_extractor.py --input <输入路径> --output <输出目录> \
  --column-mapping <JSON映射(可选)> --header-row <表头行号(可选)> \
  --data-start-row <数据起始行号(可选)> \
  [--material-master <物料主数据文件路径>] \
  [--lifecycle-config <JSON生命周期配置>] \
  [--exclude-keywords <关键词1,关键词2>] \
  [--exclude-columns <列名1,列名2>]
```
- 自动检测并排除尾部合计行（跨列关键词检测，默认前七列）
- 合并物料主数据（生命周期字段）
- 提取物料编码、库存量、入库数量、出库数量、结存数量
- 类型转换（物料编码→字符串，数值列→Float32）
- 质量检查（缺失值、非数字值标记）
- 输出 `extracted_summary.parquet` + `extracted_weekly.parquet` + `error_report.json`

### 步骤 6：结果反馈
- 成功处理的行数、异常行数、被排除的合计行数
- `extracted_summary.parquet` 路径
- `extracted_weekly.parquet` 路径
- `raw_data_profile.json` 路径
- `error_report.json` 路径与摘要
- 平衡校验结果（如有期初/入库/出库/结存字段）

---

## 注意事项（职责边界）
- 本 Skill 不自行编写任何数据清洗代码，所有处理逻辑由预置脚本确定性执行。
- 若环境不符合要求（Python 3.11.14 + uv），将终止执行并提示用户修正。
- 列映射失败且用户无法提供有效映射时，终止执行并建议用户检查源文件。
- 本 Skill 是其他所有下游 Skill 的前提，输出文件（特别是 `extracted_summary.parquet` 和 `extracted_weekly.parquet`）必须保留在输出目录中。
- 尾部合计行的检测基于**跨列关键词匹配**（默认在前七列中搜索"合计"/"总计"/"小计"）。用户可通过 `--exclude-keywords` 和 `--exclude-columns` 自定义排除规则。不再依赖特定物料编码格式（如 GSN-XXXXX），适配任意 ERP 系统的物料编码体系。

## 接口约定
- 上游：用户提供的 ERP 导出原始库存文件 + 上期行动记录（可选）+ 物料主数据文件（可选）。
- 下游：`extracted_summary.parquet` 和 `extracted_weekly.parquet` 供 inventory-overview、category-classifier、supplier-analyzer、supply-demand-matcher、inventory-planner、purchase-advisor 消费。
- 所有 Skill 共享统一的 error_report.json 格式，便于跨 Skill 异常聚合分析。