# SheetAgent 核心逻辑分析

**重点：输入输出比对与 Recipe 保存机制**

- 项目地址：[hvithalani/sheetagent](https://github.com/hvithalani/sheetagent)
- 整理依据：仓库当前 `main` 分支的 README 和源码
- 整理日期：2026 年 10 月 29 日

> 核心结论：SheetAgent 的流程是“程序先做结构化 diff，AI 根据 diff 生成代码，程序重新执行并验证结果”。Recipe 不是一段自然语言，而是一个保存了输入定义、步骤代码、Power Query 代码、输出定义和校验规则的 JSON。

---

## 1. 项目解决什么问题

SheetAgent 面向重复性的 Excel 工作，例如月末报表、季度汇总和每月账单。

它的基本思路是：

```text
第一次：用一个真实样例学习处理过程
以后：换新的 Excel，重复运行已保存的处理过程
```

一次学习通常需要：

1. **输入 Excel**：处理开始时拿到的原始文件。
2. **输出 Excel**：人工已经做好的正确结果，相当于训练阶段的标准答案。
3. **process.md**：处理说明，可选，但能帮助模型理解业务规则。

例如：

```text
输入：订单明细.xlsx
输出：人工确认的订单汇总.xlsx
说明：删除取消订单，按订单号汇总数量和金额
```

学习完成后，SheetAgent 保存一个 Recipe。下个月只需要给新的输入文件，直接运行这个 Recipe。

---

## 2. 官方地址和基本使用

项目地址：

<https://github.com/hvithalani/sheetagent>

### Windows 快速开始

```bat
setup.bat

:: 在 .env 中填写 Gemini、OpenRouter、Anthropic 或 OpenAI API Key
run_ui.bat
```

### 跨平台启动

```bash
pip install -e ".[all]"
cp .env.example .env
# 编辑 .env，填写一个模型服务商的 API Key
py -m streamlit run ui.py
```

### CLI 命令

```bash
# 学习一次 Recipe
py -m backend.cli learn -i in.xlsx -o out.xlsx -p process.md

# 验证已保存的 Recipe
py -m backend.cli test -r recipes/my_recipe.json

# 生成交付文件
py -m backend.cli deliver -r recipes/my_recipe.json
```

项目 README 给出的样例：

```bash
py -m backend.cli learn \
  -i samples/wide_to_long_easy/input_wide_flat.xlsx \
  -o samples/wide_to_long_easy/output_long_flat.xlsx \
  -p samples/wide_to_long_easy/process_doc_flat.md
```

---

## 3. 整体流程

### 3.1 训练阶段

训练阶段会调用 LLM：

```text
输入 Excel + process.md + 人工输出 Excel
        ↓
读取工作表、列、类型、样例值
        ↓
程序计算输入和输出的 diff
        ↓
把文件结构、diff、说明交给 LLM
        ↓
LLM 返回步骤、pandas 代码、Power Query M 代码
        ↓
组装 Recipe
        ↓
用训练输入重新执行 Recipe
        ↓
与人工输出比较
        ↓
验证通过后保存 Recipe JSON
```

### 3.2 运行阶段

正常运行时，默认不需要再次调用 LLM：

```text
新的 Excel
    ↓
读取 Recipe JSON
    ↓
按 Recipe.inputs 映射输入文件
    ↓
按 id 顺序执行每个 step 的 pandas_code
    ↓
按 output_spec 写出 Excel
    ↓
运行自动校验和 validation_rules
```

如果步骤执行失败，异步执行路径可以把错误发回 LLM，请模型修复某一个步骤的 pandas 代码，再重新执行。

---

# 4. 核心逻辑一 输入输出比对

源码位置：

- [`backend/core/differ.py`](https://github.com/hvithalani/sheetagent/blob/main/backend/core/differ.py)
- [`backend/core/inferrer.py`](https://github.com/hvithalani/sheetagent/blob/main/backend/core/inferrer.py)

这里的比对不是把两个完整 Excel 直接交给 AI，让 AI 凭感觉判断。程序先把 Excel 转成 pandas DataFrame，然后提取结构化差异，最后把差异摘要交给 LLM。

## 4.1 比对对象

对每个工作表，程序读取一个 DataFrame：

```python
input_sheets = {
    "Detail": input_df,
}

output_sheets = {
    "Detail": output_df,
    "Summary": summary_df,
}
```

单文件场景比较：

```text
input.xlsx → output.xlsx
```

多文件场景会：

1. 分别比较每个输入文件和输出文件。
2. 额外检查不同输入文件之间是否可能存在字段关联。

## 4.2 四层比对顺序

| 层级 | 程序检查什么 | 得到的线索 |
|---|---|---|
| 工作表层 | 输入和输出有哪些 Sheet，哪些新增或删除 | 是否新增汇总表，是否删除原始表 |
| 列层 | 共同列、新增列、删除列、可能重命名、类型变化 | 新增列可能是计算结果，列名变化可能是重命名 |
| 行层 | 行数变化、可能过滤、可能排序、可能聚合 | 输出少了哪些状态，是否按某字段排序或汇总 |
| 值和计算层 | 样例单元格变化、简单算术关系、数字总和 | 某列是否由数量乘单价得到，值是否被改写 |

这四层是确定性程序逻辑，不是四个 AI Agent。它们产生的是证据和候选解释，最终的处理代码由 LLM 结合 `process.md` 生成。

## 4.3 工作表层比较

程序先比较输入和输出的 Sheet 名称：

```python
common = input_names ∩ output_names
added = output_names - input_names
removed = input_names - output_names
```

例如：

```text
输入：Detail
输出：Detail、Summary

结果：
- Detail：共同存在，需要继续比较
- Summary：输出新增
```

如果输出新增 Sheet，程序还会尝试判断这个 Sheet 是否可能是某个输入 Sheet 的聚合结果。

## 4.4 列层比较

核心逻辑可以简化成：

```python
input_cols = list(input_df.columns)
output_cols = list(output_df.columns)

common = [c for c in input_cols if c in output_cols]
added = [c for c in output_cols if c not in input_cols]
removed = [c for c in input_cols if c not in output_cols]

# 对 added 和 removed 做 SequenceMatcher 模糊匹配
# 例如 Customer ID 与 Customer_ID 可能被标记为 renamed
```

每个列差异会保存：

```text
列名
状态：unchanged / added / removed / renamed / type_changed
旧列名（如果疑似重命名）
旧数据类型
新数据类型
样例值
```

数据类型只做粗粒度判断：

```text
integer
float
datetime
boolean
text
```

注意：这是结构识别，不是完整的数据质量检查。

## 4.5 行数变化和过滤检测

程序先看输入和输出行数：

```python
if len(output_df) < len(input_df):
    rows_removed = len(input_df) - len(output_df)
```

然后对两类列做启发式判断。

### 分类列

它会优先检查：

- `object` 类型的列
- 唯一值数量少于 20 的列

然后找出：

```text
只在输入中出现、在输出中消失的值
```

如果这些值对应的输入行数正好等于减少的行数，就给出高置信度候选。

例如：

```text
输入 100 行
输出 80 行
状态 = 取消 的输入行正好有 20 行
```

程序会给出类似线索：

```text
可能过滤：status 不等于 取消
置信度：0.95
```

简化伪代码：

```python
for col in categorical_columns:
    input_values = set(input_df[col].dropna().unique())
    output_values = set(output_df[col].dropna().unique())
    removed_values = input_values - output_values

    removed_rows = input_df[input_df[col].isin(removed_values)]

    if len(removed_rows) == rows_removed:
        possible_filters.append({
            "column": col,
            "type": "exclude_values",
            "values": list(removed_values),
            "confidence": 0.95,
        })
```

### 数字阈值

程序还会尝试把输出最小值或最大值当成过滤阈值：

```python
# 如果这个筛选后的行数接近输出行数
filtered = input_df[input_df["amount"] >= output_min]
```

如果接近，就可能给出：

```text
amount >= output_min
```

这只是候选规则，不能单独证明业务规则。

## 4.6 排序检测

只有输入和输出行数相同，并且有共同列时，程序才尝试检测排序。

大致规则：

```text
数字列或日期列：看是否单调递增或递减
文本列：看是否变成字典序
```

例如：

```text
输入：日期顺序混乱
输出：日期从早到晚
```

结果会记录：

```json
{
  "column": "date",
  "order": "ascending",
  "confidence": 0.9
}
```

## 4.7 聚合检测

当输出行数明显少于输入时，程序会尝试判断是否发生了分组汇总。

它主要看两点：

1. 输出文本列的唯一值是否来自输入文本列。
2. 按这个输入列分组后，组数是否接近输出行数。

然后对数字列检查输入总和和输出总和是否基本一致。

例如：

```text
输入 100 行明细
输出 20 行客户汇总
输入 customer_id 有 20 个唯一值
输出 customer_id 也有 20 个值
金额总和基本一致
```

程序可能给出：

```text
可能按 customer_id 分组
金额可能使用 sum
```

简化伪代码：

```python
if len(output_df) < len(input_df):
    for output_text_col in output_text_columns:
        for input_text_col in input_text_columns:
            if output_values ⊆ input_values:
                group_count = input_df[input_text_col].nunique()
                if abs(group_count - len(output_df)) <= 2:
                    possible_group_cols.append(input_text_col)

    # 数字列：输入总和和输出总和相差小于约 1% 时，
    # 记录可能的 sum 聚合
```

## 4.8 值变化检测

当输入和输出行数相同，程序只抽取有限样例：

- 前 5 个共同列。
- 前 10 行。
- 最多记录 20 个变化。

```python
for col in common_cols[:5]:
    for row in range(min(10, len(input_df))):
        if str(input_df[col].iloc[row]) != str(output_df[col].iloc[row]):
            value_changes.append({
                "row": row,
                "column": col,
                "input_value": str(input_df[col].iloc[row]),
                "output_value": str(output_df[col].iloc[row]),
            })

            if len(value_changes) >= 20:
                break
```

这说明它没有把完整的单元格差异矩阵发送给模型，而是发送少量变化样例。

## 4.9 计算列检测

对于输出新增的数字列，程序尝试测试简单算术关系：

```text
A + B
A - B
A * B
A / B
A * 固定常数
```

例如：

```text
qty = 2
unit_price = 10
total_price = 20
```

可能得到候选：

```text
total_price = qty * unit_price
```

它还会检查输入中已有的数字列是否在输出中被重新计算。

这不是通用公式反推器。复杂条件、查表、分段价格、多个规则叠加，需要依赖 `process.md` 和 LLM。

## 4.10 多输入文件的跨文件关系

`diff_multiple_files()` 会先对每个输入文件做单文件 diff，然后检查可能的跨文件字段关系。

它的方式比较简单：

```text
取输出列的非空唯一值集合
取输入文件每个列的非空唯一值集合
计算两个集合的重合比例
```

如果重合比例超过约 50%，就记录为可能关系：

```text
输出账单.customer_id
← 客户表.customer_id
```

列名相同通常记为：

```text
direct
```

列名不同但值重合，通常记为：

```text
lookup
```

这一步只是给 LLM 的线索，不是严格的主键、外键或 Join 基数验证。它不会自动证明一对一、一对多或多对多关系。

## 4.11 diff 怎样传给 AI

程序把差异对象格式化成文本，类似：

```text
Input: orders.xlsx
Output: result.xlsx

Sheet: 'Detail' (status: modified)
  Rows: 100 → 80 (delta: -20)
  Cols: 3 → 4
  Added columns: ['total_price']
  Possible filter: status excludes ['取消']
  Computed column: total_price = qty * unit_price
```

这段 diff 文本会和以下信息一起给 LLM：

```text
输入文件结构
输入列和类型
输入列样例值
输出文件结构
process.md 内容
输入输出差异摘要
```

所以 diff 的角色是：

> 把两个 Excel 的变化压缩成结构化证据，帮助 LLM 推断处理过程。

---

# 5. 核心逻辑二 Recipe

源码位置：

- [`backend/models/schemas.py`](https://github.com/hvithalani/sheetagent/blob/main/backend/models/schemas.py)
- [`backend/core/inferrer.py`](https://github.com/hvithalani/sheetagent/blob/main/backend/core/inferrer.py)
- [`backend/core/executor.py`](https://github.com/hvithalani/sheetagent/blob/main/backend/core/executor.py)

Recipe 是 SheetAgent 学习结果的持久化对象。

它不是：

```text
“按订单号合并，然后汇总”这句自然语言
```

它更接近：

```text
输入定义
+ 一组按顺序执行的代码步骤
+ Power Query M 代码
+ 输出 Sheet 来源
+ 校验规则
+ 训练来源和元数据
```

## 5.1 Recipe 的实际数据结构

`schemas.py` 中的核心数据类包括：

```text
Recipe
├── RecipeInput[]
├── RecipeStep[]
├── OutputSheet[]
├── ValidationRule[]
├── m_query
├── learned_from
└── metadata
```

## 5.2 Recipe JSON 示例

下面是按照源码字段整理的完整示例。示例值是演示值，字段名和嵌套结构对应项目的数据类。

```json
{
  "name": "月度订单整理",
  "description": "过滤取消订单并按订单号汇总",
  "version": "1.0",
  "learned_from": {
    "input_files": ["orders_2026_09.xlsx"],
    "output_file": "result_2026_09.xlsx",
    "process_doc": "process.md"
  },
  "inputs": [
    {
      "alias": "orders_2026_09",
      "description": "From orders_2026_09.xlsx, sheet 'Detail'",
      "expected_columns": ["order_no", "status", "qty", "unit_price"],
      "required_columns": ["order_no", "status"],
      "column_matching": "fuzzy",
      "expected_row_range": [50, 200],
      "header_info": null
    }
  ],
  "steps": [
    {
      "id": 1,
      "name": "过滤取消订单",
      "action": "filter",
      "description": "删除状态为取消的行",
      "params": {
        "column": "status",
        "exclude": ["取消"]
      },
      "pandas_code": "df = df[df['status'] != '取消']",
      "m_step_name": "Filtered Rows",
      "m_expression": "Table.SelectRows(Source, each [status] <> \"取消\")",
      "confidence": 0.95,
      "explanation": "输出少了取消订单对应的行",
      "source": "orders_2026_09"
    },
    {
      "id": 2,
      "name": "计算总价",
      "action": "calculate",
      "description": "数量乘单价",
      "params": {
        "formula": "qty * unit_price"
      },
      "pandas_code": "df['total_price'] = df['qty'] * df['unit_price']",
      "m_step_name": "Added Total Price",
      "m_expression": "Table.AddColumn(...)",
      "confidence": 0.90,
      "explanation": "输出新增 total_price 列",
      "source": "step_1_output"
    }
  ],
  "m_query": "let Source = ..., FilteredRows = ..., AddedTotalPrice = ... in AddedTotalPrice",
  "output_spec": [
    {
      "name": "结果",
      "source": "step_2_output",
      "description": "最终订单结果",
      "formatting": {}
    }
  ],
  "validation_rules": [
    {
      "type": "unique",
      "description": "订单号不重复",
      "params": {
        "columns": ["order_no"]
      },
      "severity": "error",
      "requires_history": false,
      "id": "v_demo"
    }
  ],
  "metadata": {
    "confidence_overall": 0.92,
    "notes": "训练样例验证通过",
    "tags": [],
    "has_process_doc": true
  },
  "id": "rec_demo",
  "created_at": "2026-10-29T00:00:00",
  "updated_at": ""
}
```

> 注意：上面 JSON 中 `m_expression` 的内容只是占位示例。真实项目会保存 LLM 生成的 Power Query M 表达式。

## 5.3 顶层字段含义

| 字段 | 实际作用 |
|---|---|
| `name` / `description` / `version` | Recipe 名称、说明和版本号，`version` 默认是 `1.0`。 |
| `learned_from` | 记录学习时使用的输入文件、输出文件和处理说明来源。 |
| `inputs` | 定义运行时怎样识别输入：别名、预期列、必需列、模糊匹配方式、预期行数范围和多行表头信息。 |
| `steps` | 按 `id` 排序执行的步骤列表。每步保存 pandas 代码、Power Query M 表达式、来源和解释。 |
| `m_query` | 完整的 Power Query M 查询。 |
| `output_spec` | 指定哪个中间结果写成哪个输出 Sheet。 |
| `validation_rules` | Recipe 自己声明的检查规则，例如唯一性、非空、类型或历史范围。 |
| `metadata` | 保存置信度、备注、是否有 process.md、对齐报告等附加信息。 |
| `id` / `created_at` / `updated_at` | Recipe 标识和时间信息。 |

## 5.4 RecipeInput 怎样生成

`inferrer._build_recipe()` 不完全依赖 LLM 来填写输入定义，而是重新读取真实输入文件，逐个工作表创建 `RecipeInput`。

大致逻辑：

```python
for file in input_paths:
    sheets, header_infos = read_excel(file)

    for sheet_name, df in sheets.items():
        alias = file.stem.lower().replace(" ", "_")

        if len(sheets) > 1:
            alias += "_" + sheet_name.lower().replace(" ", "_")

        input_spec = RecipeInput(
            alias=alias,
            expected_columns=list(df.columns),
            required_columns=前 10 个全非空列,
            column_matching="fuzzy",
            expected_row_range=[约 50%, 约 200%],
            header_info=多行表头时才保存,
        )
```

这里有一个容易忽略的实现细节：

```text
required_columns 不是模型理解出来的业务主键
```

当前实现是从训练文件中找“没有空值”的列，取前 10 个。因此在快递账单平台中，运单号、客户编码、账期等业务必需字段应单独配置，不能完全依赖这个自动规则。

## 5.5 RecipeStep 保存什么

`RecipeStep` 是 Recipe 的核心执行单元：

| 字段 | 含义 |
|---|---|
| `id` | 执行顺序。执行器按 `id` 排序。 |
| `name` / `action` / `description` | 步骤名称、动作类型和自然语言解释。 |
| `params` | 动作参数，便于展示或后续逻辑使用。 |
| `pandas_code` | 当前 Python 执行器实际运行的代码，主变量通常是 `df`。 |
| `m_step_name` / `m_expression` | Power Query Applied Step 名称和 M 表达式。 |
| `confidence` / `explanation` | 模型置信度和推断理由。 |
| `source` | 从哪个输入或前一步结果读取数据。 |

## 5.6 Recipe 怎样被读取和执行

`executor.py` 的核心循环可以压缩成：

```python
recipe = Recipe.from_file("recipes/monthly_bill.json")

# 1. 按 recipe.inputs 把 Excel 映射成 alias -> DataFrame
ctx.inputs = map_input_files(recipe, input_paths)

# 2. 初始输入也放进 step_outputs，供后续 source 查找
for alias, df in ctx.inputs.items():
    ctx.step_outputs[alias] = df

# 3. 按 id 顺序执行每一步的 pandas_code
for step in sorted(recipe.steps, key=lambda s: s.id):
    result_df = execute_step(step, ctx)
    ctx.step_outputs[f"step_{step.id}_output"] = result_df

# 4. 根据 output_spec 取中间结果，写入最终 Excel
```

当前执行器还做了这些事：

1. 输入文件按 `RecipeInput` 做简单的模糊列名匹配。
2. 每个 step 执行前做 AST 安全检查。
3. 每个 step 的输出保存到 `step_1_output`、`step_2_output` 等名称。
4. `output_spec` 指定写出哪些 Sheet。
5. 如果 `output_spec` 没有成功写出任何 Sheet，就回退到最后一个 step 输出。
6. 每次执行都会记录错误和运行日志。

---

# 6. diff 怎样进入 Recipe

`inferrer.py` 的主线可以简化成：

```python
def learn(input_paths, output_path=None, process_doc_path=None):
    # 1. 读取 process.md（如果有）
    process_doc = parse_process_doc(process_doc_path)

    # 2. 如果有人工输出，先做 input vs output diff
    diff = diff_files(input_paths[0], output_path)
    diff_context = generate_diff_context(diff)

    # 3. 读取输入输出的 Sheet、列、类型、样例值
    input_context = build_file_context(input_paths)
    output_context = build_file_context([output_path])

    # 4. 将说明、文件结构和 diff 发给 LLM
    result = complete_json(prompt=prompt)

    # 5. 把 LLM JSON 解析成 RecipeStep，并补齐真实输入信息
    recipe = _build_recipe(result, input_paths, output_path, process_doc)

    # 6. 后续由 verifier 验证 recipe 是否复现人工结果
```

关系可以总结成：

```text
diff 不是 Recipe

diff：学习阶段的证据
Recipe：根据证据生成、再经过验证的可执行结果
```

---

# 7. Recipe 生成后的验证

源码位置：

- [`backend/core/verifier.py`](https://github.com/hvithalani/sheetagent/blob/main/backend/core/verifier.py)
- [`backend/core/validator.py`](https://github.com/hvithalani/sheetagent/blob/main/backend/core/validator.py)

SheetAgent 会用训练输入重新运行 Recipe，生成 `generated.xlsx`，再和训练时的人工输出 `output.xlsx` 比较。

## 7.1 结果验证项目

| 验证项 | 源码中的大致规则 |
|---|---|
| Sheet 结构 | 检查输出 Sheet 是否缺失或多出。缺失 Sheet 会影响失败。 |
| 列结构 | 检查人工输出中的列是否在生成结果中存在；缺失列失败。 |
| 行数 | 默认允许约 5% 的差异；差异较大时警告或失败。 |
| 数值列总和 | 默认使用约 0.1% 的相对误差容忍度。 |
| 单元格匹配率 | 按重叠行和共同列逐个比较；默认 90% 以上通过，75% 到 90% 警告。 |
| 单元格比较 | 空值对空值相等；数字允许约 0.1% 误差；文本去首尾空格并忽略大小写。 |

简化逻辑：

```python
generated = run_recipe(recipe, training_input)
report = verify_recipe_output(generated, expected_output)

if report.status == "fail":
    # 可选：让 LLM 根据错误修复某一步代码
    fixed_step = self_heal(step, error_info)
    # 再运行，再验证
```

验证通过后，Recipe 才具备“可以拿去处理下一批数据”的可信度。

---

# 8. 源码阅读地图

| 文件 | 阅读重点 |
|---|---|
| [`backend/core/differ.py`](https://github.com/hvithalani/sheetagent/blob/main/backend/core/differ.py) | 输入输出结构 diff、列重命名、过滤、排序、聚合、计算列和跨文件关系。 |
| [`backend/core/inferrer.py`](https://github.com/hvithalani/sheetagent/blob/main/backend/core/inferrer.py) | `learn()` 如何组装上下文并调用 LLM；`_build_recipe()` 如何生成 Recipe。 |
| [`backend/models/schemas.py`](https://github.com/hvithalani/sheetagent/blob/main/backend/models/schemas.py) | Recipe、RecipeInput、RecipeStep、OutputSheet、ValidationRule 等数据结构。 |
| [`backend/core/executor.py`](https://github.com/hvithalani/sheetagent/blob/main/backend/core/executor.py) | 如何映射输入文件、按 step 执行 pandas_code、保存中间结果并导出 Excel。 |
| [`backend/core/verifier.py`](https://github.com/hvithalani/sheetagent/blob/main/backend/core/verifier.py) | 生成结果与人工输出的结构、行数、数值总和和单元格匹配。 |
| [`backend/core/validator.py`](https://github.com/hvithalani/sheetagent/blob/main/backend/core/validator.py) | 每次运行的自动校验和 Recipe 自定义 `validation_rules`。 |
| `../../README.md` | 安装、CLI 命令和“训练一次、重复运行”的产品流程说明。 |

---

# 9. 对你的周期性 Excel 平台的启发

SheetAgent 最值得借鉴的是：

```text
一次学习 → 保存可执行流程 → 周期性重复运行
```

可以直接借鉴：

- 输入文件 + 人工结果文件 + 说明文档的学习方式。
- `diff → LLM → Recipe → 验证` 的链路。
- Recipe 保存输入、步骤、输出和校验的结构。
- 正常运行阶段尽量不再调用 LLM，只有异常时才调用。

需要补强：

- 按运单号、订单号、客户编码做主键对齐，不能只按行位置比较。
- 检查重复键、空键、未匹配键。
- 检查一对多和多对多连接，防止金额被重复展开。
- 把账期、客户、合同版本、价格表版本加入运行上下文。
- 财务场景应做票数、重量、金额和税额对账。
- 生产环境可以把任意 `pandas_code` 换成白名单 DSL 或固定算子，降低执行风险。

一句话总结：

> SheetAgent 的核心不是“让 AI 每月重新处理 Excel”，而是“让 AI 根据一次输入输出差异生成可执行代码，再把代码保存成 Recipe，后续按 Recipe 重复运行”。

---

## 10. 资料来源

- 项目主页：[https://github.com/hvithalani/sheetagent](https://github.com/hvithalani/sheetagent)
- README：[README.md](https://github.com/hvithalani/sheetagent/blob/main/README.md)
- Diff：[backend/core/differ.py](https://github.com/hvithalani/sheetagent/blob/main/backend/core/differ.py)
- Recipe 数据结构：[backend/models/schemas.py](https://github.com/hvithalani/sheetagent/blob/main/backend/models/schemas.py)
- 学习和组装：[backend/core/inferrer.py](https://github.com/hvithalani/sheetagent/blob/main/backend/core/inferrer.py)
- 执行器：[backend/core/executor.py](https://github.com/hvithalani/sheetagent/blob/main/backend/core/executor.py)
- 验证器：[backend/core/verifier.py](https://github.com/hvithalani/sheetagent/blob/main/backend/core/verifier.py)
- 运行校验：[backend/core/validator.py](https://github.com/hvithalani/sheetagent/blob/main/backend/core/validator.py)

> 说明：仓库主分支会继续变化。本文描述的是整理时看到的源码逻辑，未来升级时应重新核对字段和阈值。
