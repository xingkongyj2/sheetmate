# TabClaw 整体流程与核心逻辑分析

**重点：TabClaw 如何接收表格、调用 Agent 工具、生成结果，以及如何记录工作流和沉淀技能**

- 项目地址：[ustc-table-mining/TabClaw](https://github.com/ustc-table-mining/TabClaw)
- 本文依据：仓库当前可核对的源码和文档
- 核对版本：`420125a1207a25216b9d1443522a3ff0e3f1a921`
- 整理日期：2026 年 10 月 29 日

> 核心结论：TabClaw 的主线不是“输入 Excel + 输出 Excel → 自动 diff → 生成 Recipe”，而是“用户上传表格 → LLM 选择 pandas 工具 → 程序执行工具 → 把结果返回给 LLM → 继续循环，直到得到答案”。它保存的是工作流日志和 `SKILL.md` 指令技能，不是可以完全确定性重放的 Recipe。

---

## 1. TabClaw 解决什么问题

TabClaw 是一个交互式表格 Agent。用户上传 CSV 或 Excel 后，可以用自然语言提问，例如：

```text
统计每个区域的销售额，并按销售额倒序排列
把订单表和商品表按商品编号合并
找出重复订单号，并分析每个订单的总金额
比较这两个月的账单差异
```

TabClaw 不要求用户先写 SQL 或 pandas 代码。LLM 负责理解问题、选择工具、填写工具参数；程序负责真正执行 pandas 操作。

它还提供：

- 任务澄清：请求不明确时先给选项。
- 计划模式：先生成步骤，用户确认后执行。
- ReAct 循环：边思考、边调用工具、边读取结果。
- 多表并行分析：每张表由一个专门 Agent 分析，再统一汇总。
- 记忆：保存用户偏好和领域知识。
- 工作流记录：保存每次任务使用了哪些工具。
- 技能蒸馏：从重复的工具调用中生成可复用的 `SKILL.md`。

---

## 2. 安装和使用

### 2.1 安装启动

```bash
git clone https://github.com/ustc-table-mining/TabClaw.git
cd TabClaw

cp setting.txt.example setting.txt
# 编辑 setting.txt，填写 API_KEY、BASE_URL、DEFAULT_MODEL

pip install -r requirements.txt
bash run.sh
```

打开：

```text
http://localhost:8000
```

`run.sh` 最终启动的是：

```bash
python3 -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

项目使用 OpenAI 兼容接口，因此可以连接 OpenAI、DeepSeek、SiliconFlow、OpenRouter、Ollama 等服务，只要接口支持聊天和工具调用。

### 2.2 配置

`setting.txt` 大致包含：

```text
API_KEY=your-api-key
BASE_URL=https://api.openai.com/v1
DEFAULT_MODEL=your-model-name
DEFAULT_MODEL_EXTRA_JSON={}
```

当前源码还支持用户在设置页保存自己的 API Key。用户自己的 Key 会加密存储；没有自己的 Key 时，使用服务端配置的 Key，并按账户记录 token 使用量。

### 2.3 基本使用

1. 注册或登录。
2. 上传 CSV、XLSX 或 XLS。
3. 输入自然语言问题。
4. 系统先判断是否需要澄清。
5. 可直接执行，也可以先生成计划。
6. Agent 调用表格工具并实时返回结果。
7. 新生成的表会继续放入当前会话的表集合中，可以被后续步骤再次使用。
8. 用户可以下载结果表、评价结果、查看技能和记忆。

当前下载接口实际导出 CSV；源码没有专门的 XLSX 结果写出流程。

---

## 3. 整体架构

```text
浏览器前端 static/app.js
        │ HTTP + SSE
        ▼
FastAPI app.py
        │
        ├─ 登录 / 配置 / 上传表格
        ├─ /api/clarify       → Planner.check_clarification()
        ├─ /api/generate-plan → Planner.generate()
        ├─ /api/chat          → AgentExecutor.execute()
        │                       └→ MultiAgentExecutor.execute_multi()
        └─ /api/execute-plan  → AgentExecutor.execute_plan()
                                      │
                                      ▼
                              _agent_stream()
                              ReAct 工具循环
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
              pandas 技能       execute_python       结果表保存
              skills/builtin.py  skills/code_skill.py  tables[result_id]
                    │                 │                 │
                    └─────────────────┴─────────────────┘
                                      ▼
                              tool_result 回传 LLM
                                      │
                                      ▼
                              SSE 返回浏览器
                                      │
                    ┌─────────────────┴─────────────────┐
                    ▼                                   ▼
              workflow JSON                       memory.json / SKILL.md
```

核心模块：

| 文件 | 作用 |
|---|---|
| `app.py` | FastAPI 路由、用户会话、上传、SSE、工作流反馈 |
| `agent/executor.py` | 直接执行、计划执行和 ReAct 循环 |
| `agent/planner.py` | 生成计划和判断是否需要澄清 |
| `agent/multi_agent.py` | 多表并行 Agent 和最终聚合 |
| `agent/llm.py` | OpenAI 兼容的异步 LLM 客户端 |
| `agent/workflow_recorder.py` | 工作流 JSON、重复模式和成长统计 |
| `agent/skill_distiller.py` | 技能蒸馏、发现和负反馈改进 |
| `agent/memory.py` | 用户记忆持久化和相关性筛选 |
| `skills/registry.py` | 工具定义、技能包加载和技能版本管理 |
| `skills/builtin.py` | 16 个 pandas 表格原子操作 |
| `skills/code_skill.py` | 受限 Python 执行沙箱 |
| `static/app.js` | 前端上传、聊天、SSE 事件和结果表渲染 |

---

## 4. 一次任务的完整流程

下面以“把订单表和商品表合并，统计每个客户金额”为例。

```text
登录
  ↓
上传 orders.xlsx、products.xlsx
  ↓
pandas 读取为 DataFrame，放入当前用户 tables
  ↓
用户发送自然语言请求
  ↓
clarify：判断是否有歧义
  ↓
可选：generate-plan 生成步骤
  ↓
chat 或 execute-plan
  ↓
ReAct：LLM 选择 table_info、merge_tables、aggregate、sort_table
  ↓
程序执行 pandas，生成中间结果表
  ↓
tool_result 返回 LLM，LLM 决定是否继续
  ↓
结果表通过 SSE 发送前端
  ↓
LLM 输出最终说明
  ↓
保存 workflow JSON，更新 memory，可选生成技能
```

### 4.1 上传阶段：文件变成 DataFrame

入口是 `POST /api/upload`，实现位于 `app.py`。

实际逻辑大致是：

```python
# 伪代码，保留源码的处理方向
content = await file.read()

if filename.endswith(".csv"):
    df = pandas.read_csv(BytesIO(content))
elif filename.endswith((".xlsx", ".xls")):
    df = pandas.read_excel(BytesIO(content))
else:
    raise HTTPException("Only CSV and Excel files are supported")

table_id = uuid.uuid4().hex[:8]
tables[table_id] = {
    "name": filename_without_extension,
    "df": df,
    "source": "uploaded",
}
```

上传接口返回表 ID、文件名、行数、列数、列名、数据类型和前 5 行样例。

关键点：

- 表首先存在内存里的当前用户 session 中。
- `table_id` 是随机的 8 位字符串。
- 后续工具使用 `table_id` 读取表。
- 工具创建的结果表也会写回同一个 `tables` 字典。
- 因此一个任务可以连续引用前一步产生的结果表。

### 4.2 澄清阶段：只判断意图是否有歧义

入口是 `POST /api/clarify`，调用 `Planner.check_clarification()`。

Planner 把以下内容交给 LLM：

- 用户原始问题。
- 当前所有表的名称、行数和部分列名。

LLM 只能返回两种 JSON：

```json
{"needs_clarification": false}
```

或：

```json
{
  "needs_clarification": true,
  "question": "你希望按哪个金额字段统计？",
  "options": ["实付金额", "应付金额", "运费金额"]
}
```

这里没有程序规则去推断业务含义，主要由 LLM 判断是否存在多个合理解释。

### 4.3 计划阶段：生成步骤描述，不是固定执行图

入口是 `POST /api/generate-plan`，调用 `Planner.generate()`。

典型结果：

```json
{
  "title": "订单与商品关联分析",
  "steps": [
    {"id": 1, "description": "查看订单表和商品表的字段结构"},
    {"id": 2, "description": "按照商品编号合并两张表"},
    {"id": 3, "description": "按客户分组汇总订单金额"},
    {"id": 4, "description": "按金额从高到低排序并说明结果"}
  ]
}
```

这一步只生成结构化的步骤描述。步骤还没有被编译成固定函数调用，也没有保存为可以脱离 LLM 独立执行的 Recipe。

用户可以在前端修改步骤，再调用 `POST /api/execute-plan`。

### 4.4 执行阶段：直接聊天或计划执行

有两条路径：

```text
/api/chat
    └─ 普通单轮请求 → AgentExecutor.execute()
    └─ 多表比较请求 → MultiAgentExecutor.execute_multi()

/api/execute-plan
    └─ 用户确认的步骤 → AgentExecutor.execute_plan()
```

两条路径最终都会进入 `_agent_stream()`，区别是计划模式会把每个步骤分开执行，并把上一步的最终文字追加到下一步上下文。

---

## 5. 最核心逻辑：ReAct 工具循环

源码位置：`agent/executor.py::_agent_stream()`。

这是 TabClaw 最重要的执行机制。

### 5.1 ReAct 的实际循环

```text
准备 system prompt、历史消息、用户请求、工具定义
        ↓
调用 LLM 流式接口
        ↓
收集文本增量和 tool_call 增量
        ↓
如果有 tool_call：
    解析工具名称和 JSON 参数
    执行对应 pandas 技能
    返回 tool_result
    把 assistant tool_call 和 tool_result 追加到消息历史
    回到 LLM
        ↓
如果没有 tool_call：
    把当前文本作为 final_text
        ↓
最多循环 12 轮
```

### 5.2 源码逻辑的简化版

```python
async def _agent_stream(messages, tools, tables, result_tables_store):
    msgs = list(messages)

    for iteration in range(12):
        # LLM 以流式方式输出文字和工具调用
        full_content = ""
        tool_calls = collect_streaming_tool_calls(
            llm.stream_chat(msgs, tools=tools)
        )

        if tool_calls:
            # 先把 assistant 的工具调用写回上下文
            msgs.append({
                "role": "assistant",
                "content": full_content,
                "tool_calls": tool_calls,
            })

            for call in tool_calls:
                name = call["function"]["name"]
                params = json.loads(call["function"]["arguments"] or "{}")

                yield {"type": "tool_call", "skill": name, "params": params}

                result = await execute_skill(name, params, tables)

                yield {"type": "tool_result", "skill": name, ...}

                # 工具结果作为 role=tool 回传给 LLM
                msgs.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": result_text,
                })
        else:
            # 没有工具调用，说明 LLM 已经准备好最终回答
            yield {"type": "final_text", "content": full_content}
            return

    yield {"type": "error", "content": "maximum iterations reached"}
```

### 5.3 为什么要累积 tool call 分片

OpenAI 兼容接口在流式返回时，函数参数可能被拆成多个 chunk：

```text
{"table_id": "t1", "cond
ition": "amount > 100"}
```

TabClaw 按 `tc.index` 建立累积字典，把函数名和参数字符串拼接完成后，再执行 `json.loads()`。

因此，程序不会把每个流式片段误认为一个完整工具调用。

### 5.4 DeepSeek 工具标记处理

源码专门处理 DeepSeek V3 偶尔把内部工具标记泄漏到普通文本的情况。它会：

1. 发现 `<｜tool▁call▁begin｜>` 标记。
2. 暂停向前端显示后续内部标记文本。
3. 用正则删除残余工具标记。

这部分是显示层兼容处理，不改变工具调用逻辑。

---

## 6. 工具执行：LLM 决策，程序执行

### 6.1 内置 pandas 工具

工具定义位于 `skills/registry.py`，具体实现位于 `skills/builtin.py`。

| 工具 | 实际操作 |
|---|---|
| `table_info` | 返回表结构、列名、类型、缺失值和样例 |
| `filter_rows` | 使用 pandas query 过滤行 |
| `select_columns` | 选择列 |
| `aggregate` | groupby + 聚合 |
| `sort_table` | 排序 |
| `merge_tables` | inner/left/right/outer 合并 |
| `pivot_table` | 透视表 |
| `add_column` | 用 pandas eval 增加计算列 |
| `describe_stats` | 描述性统计 |
| `find_values` | 精确值或正则匹配 |
| `drop_duplicates` | 删除重复行 |
| `rename_columns` | 重命名列 |
| `sample_rows` | 随机抽样 |
| `value_counts` | 统计唯一值频次 |
| `correlation_matrix` | 计算数值列相关矩阵 |
| `head_rows` | 取前 N 行 |

LLM 只负责从这些工具中选择并生成参数。例如：

```json
{
  "name": "merge_tables",
  "arguments": {
    "left_table_id": "a12bc345",
    "right_table_id": "d67ef890",
    "on": "商品编号",
    "how": "left",
    "result_name": "订单商品明细"
  }
}
```

真正执行的是确定的 pandas 代码：

```python
result = pd.merge(left_df, right_df, on="商品编号", how="left")
```

这意味着 TabClaw 的安全边界是：

```text
自然语言理解由 LLM 完成
表格变换由固定工具完成
```

### 6.2 结果表如何传递到下一步

源码位置：`AgentExecutor._exec_skill()`。

当技能返回 `{"df": result_df, "name": result_name}` 时：

```python
rid = "r_" + uuid.uuid4().hex[:6]
result_tables_store[rid] = {
    "name": result_name,
    "df": result_df,
    "source": "computed",
}
```

程序同时生成最多 200 行的预览，并发送一个 `table` SSE 事件给前端。

所以结果表的链路是：

```text
工具返回 DataFrame
    ↓
生成 r_xxxxxx 结果表 ID
    ↓
写回当前 tables 字典
    ↓
前端收到 table 事件并显示
    ↓
后续工具可以继续使用这个结果表 ID
```

### 6.3 可选 Python 沙箱

打开 Code Tool 后，LLM 可以调用 `execute_python`。

安全处理分三层：

1. AST 检查：阻止危险 import、危险函数和不安全的双下划线访问。
2. 受限命名空间：移除 `open`、`exec`、`eval`、文件系统和网络能力。
3. 30 秒线程超时：防止代码无限运行。

上传的表会被注入为：

```python
df_id = uploaded_table_dataframe
table_name = uploaded_table_dataframe
tables = {
    "table_id": {"name": "...", "df": dataframe}
}
```

只有当脚本把 DataFrame 赋给变量 `result` 时，才会生成新的结果表：

```python
result = orders.groupby("客户编号", as_index=False)["金额"].sum()
```

源码注释明确说明：这是适用于本地单用户应用的沙箱，不是强隔离的多租户安全沙箱。

---

## 7. 计划模式的执行方式

源码位置：`agent/executor.py::execute_plan()`。

计划模式不是把全部步骤一次性发给一个函数，而是：

```text
步骤 1 → 一次 ReAct 循环 → 得到最终文本
步骤 2 → 带着前面上下文，再启动一次 ReAct 循环
步骤 3 → 继续带着上下文执行
...
全部完成 → 额外做一次 reflection 自检
```

简化逻辑：

```python
conversation = list(base_messages)

for step in steps:
    step_messages = conversation + [
        {"role": "user", "content": f"Execute step: {step['description']}"}
    ]

    final_text = await run_agent_stream(step_messages)

    # 将本步骤输入和输出放进下一步上下文
    conversation.append({"role": "user", "content": step_message})
    conversation.append({"role": "assistant", "content": final_text})

# 最后再问一次是否漏掉了原始要求
await run_agent_stream(conversation + [reflection_prompt])
```

自检提示词要求 Agent 检查：

- 原始问题是否已经完整处理。
- 是否有明显错误。
- 是否有遗漏的结果。

如果发现遗漏，Agent 可以在自检轮继续调用工具。

注意：这个“自检”是 LLM 反思，不是独立的数值校验器。

---

## 8. 多表比较的完整逻辑

### 8.1 什么时候开启多 Agent

`MultiAgentExecutor.should_activate()` 只有在以下条件同时满足时才开启：

```text
上传表数量 ≥ 2
且用户问题包含比较类关键词
```

关键词包括：

- 中文：`对比`、`比较`、`分别`、`各表`、`差异`、`相同`、`不同`、`综合`。
- 英文：`compare`、`contrast`、`across`、`differences`、`similarities`。

### 8.2 每张表一个专门 Agent

系统为每张表启动一个异步任务：

```text
表 A → Agent A，只看到表 A
表 B → Agent B，只看到表 B
表 C → Agent C，只看到表 C
```

每个 Agent 的 system prompt 只列出它负责的那张表，目的是避免表 A 的分析误引用表 B 的列。

每个 Agent 内部仍然使用同一个 `_agent_stream()` ReAct 循环。

### 8.3 asyncio.Queue 交错返回结果

各 Agent 的事件写入共享队列：

```python
async def run_agent(table_id, table):
    async for event in self._agent_stream(...):
        await queue.put({**event, "agent_id": table_id})

tasks = [asyncio.create_task(run_agent(...)) for ...]

while done_count < number_of_tables:
    event = await queue.get()
    yield event
```

浏览器看到的是多个 Agent 交错产生的实时输出，而不是等所有表都完成后才一次性显示。

### 8.4 Aggregator 汇总

所有 specialist Agent 完成后，TabClaw 把它们的结论交给另一个 LLM：

```text
原始用户问题
+ 每张表的分析结论
        ↓
Aggregator LLM
        ↓
跨表比较、共同点、冲突和不确定性
```

Aggregator 被要求使用两个标记：

- `[CONSENSUS]`：多个 Agent 基本一致的结论。
- `[UNCERTAIN]`：结论冲突、证据不足或依赖额外条件。

这里的“比较”是基于各 Agent 文本结论的 LLM 汇总，不是 SheetAgent 那种程序级输入输出结构 diff。

源码阅读时还发现：当前 `multi_agent.py` 在调用 `_agent_stream()` 时，第一参数使用了 `result_tables_store`，而不是每个 Agent 的 `tables` 参数。共享结果表的设计意图是明确的，但该调用值得在生产使用前复核。

---

## 9. TabClaw 是否有 Recipe

严格来说，没有 SheetAgent 意义上的 Recipe。

TabClaw 有两个容易被误认为 Recipe 的东西。

### 9.1 WorkflowRecord：工作流执行日志

源码位置：`agent/workflow_recorder.py`。

保存位置：

```text
data/workflows/<session_id>.json
```

字段大致包括：

```json
{
  "session_id": "20261029_abc123",
  "timestamp": "2026-10-29T10:00:00+00:00",
  "user_message": "按客户汇总订单金额",
  "tables_involved": ["orders"],
  "plan": [
    {"id": 1, "description": "查看表结构"},
    {"id": 2, "description": "按客户汇总金额"}
  ],
  "steps": [
    {
      "tool_name": "table_info",
      "params": {"table_id": "a12bc345"},
      "result_summary": "...",
      "produced_table": null,
      "duration_ms": 38
    },
    {
      "tool_name": "aggregate",
      "params": {
        "table_id": "a12bc345",
        "group_by": ["客户编号"],
        "agg_config": {"金额": "sum"},
        "result_name": "客户账单汇总"
      },
      "result_summary": "Created table ...",
      "produced_table": "Created table",
      "duration_ms": 126
    }
  ],
  "final_conclusion": "...",
  "tool_sequence": "table_info → aggregate",
  "skills_used": [],
  "skill_distilled": "",
  "user_feedback": null,
  "feedback_detail": "",
  "duration_ms": 190
}
```

它可以用于：

- 展示最近做过的任务。
- 统计工具使用频率。
- 从重复工具序列中发现模式。
- 根据用户好评或差评改进技能。

它不能直接保证下次重放成功，原因是：

- 只保存结果摘要，不保存完整 DataFrame。
- `table_id` 是当前会话随机生成的，下一次上传后会变化。
- 只有工具名称和参数日志，没有固定输入字段映射。
- 结果是否正确没有独立的 expected output 校验。

因此 WorkflowRecord 是“执行记录”，不是“可验证执行配方”。

### 9.2 `SKILL.md`：可复用的指令技能

技能包保存位置：

```text
data/skills/<slug>/
├── SKILL.md
├── _meta.json
├── _state.json
└── SKILL.vN.md       # 升级时保留旧版本
```

一个技能的 `SKILL.md` 类似：

```markdown
---
name: customer_bill_summary
description: "按客户汇总账单并输出排名"
---

## Pattern: Customer Bill Summary

1. Call `table_info` to identify customer and amount columns.
2. Use `aggregate` grouped by the customer column.
3. Use `sort_table` to rank total amount.
4. Explain missing values and duplicate customer keys.
```

`_meta.json` 大致保存：

```json
{
  "slug": "customer_bill_summary",
  "source": "distilled",
  "version": 1,
  "created_at": "2026-10-29T10:00:00+00:00",
  "usage_count": 0,
  "success_count": 0,
  "failure_count": 0,
  "last_used_at": null,
  "derived_from_workflow": "20261029_abc123"
}
```

`_state.json` 保存是否启用：

```json
{"enabled": true}
```

### 9.3 技能是怎么被使用的

启动时 `SkillRegistry` 扫描 `data/skills/*/SKILL.md`。

启用技能的正文被拼接到 system prompt：

```text
## Package Skills
### Skill: customer_bill_summary
...SKILL.md body...
```

然后仍由 LLM 自主决定是否调用 `aggregate`、`sort_table` 等原子工具。

也就是说：

```text
SKILL.md = 给 LLM 的方法论和调用顺序提示
```

它不是：

```text
SKILL.md = 一个传入文件后就能完全确定执行的函数
```

### 9.4 自动复用判断

`AgentExecutor._check_skill_reuse()` 会把当前已经调用过的工具名集合，与已蒸馏技能正文中反引号标记的工具名集合做重叠判断。

满足以下条件时，界面提示正在复用技能：

```text
当前工具和技能工具至少重叠 2 个
重叠工具占技能工具集合至少 50%
```

它只是提示和记录 usage，后续实际执行仍然由 LLM 选择工具。

---

## 10. 技能自进化的三条路径

### 10.1 任务完成后蒸馏

触发条件：

```text
开启 Skill Learning
且本次任务至少调用 3 次工具
```

`SkillDistiller.try_distill()` 把以下内容交给 LLM：

- 用户请求。
- 按顺序排列的工具调用日志。
- 每次工具的参数和结果摘要。
- 已有内置技能名称。
- 已有技能包名称。

LLM 判断是否是可泛化的模式。如果是，返回 `name`、`description` 和 Markdown `body`，然后 `SkillRegistry.create_package()` 创建 `SKILL.md`。

### 10.2 从历史发现重复模式

用户点击 Discover 后：

1. 扫描 `data/workflows/*.json`。
2. 按 `tool_sequence` 完全相同分组。
3. 同一序列至少出现 2 次才进入候选。
4. 排除已被技能工具集合覆盖的模式。
5. 将候选模式和示例问题交给 LLM。
6. LLM 返回技能建议。
7. 用户接受后才真正写入 `data/skills/<slug>/`。

注意：这里的“重复”首先是工具序列完全相同，不是复杂的语义相似检索。

### 10.3 负反馈改进技能

用户给出差评后：

```text
workflow.user_feedback = "bad"
        ↓
相关技能 failure_count + 1
        ↓
failure_count ≥ 2
        ↓
读取使用过该技能的差评工作流
        ↓
LLM 生成新的 SKILL.md body
        ↓
备份 SKILL.vN.md，version + 1
```

这里的改进是“让 LLM 重写技能说明”，不是重新训练模型参数。

项目文档也明确说明：没有独立的梯度训练环节，技能进化主要由 LLM 提示词和文件版本管理完成。

---

## 11. Memory 的处理方式

源码位置：`agent/memory.py`。

保存位置：

```text
data/memory.json
```

分为四类：

```json
{
  "preferences": {},
  "domain_knowledge": {},
  "user_context": {},
  "history_insights": {}
}
```

每次任务完成后，Agent 会调用一个轻量 LLM 请求，从用户话语中最多提取 3 条：

```json
[
  {
    "category": "preferences",
    "key": "output_language",
    "value": "使用中文回答"
  }
]
```

下一次请求时：

- `preferences` 总是注入。
- 其他类别按照用户问题中的关键词做筛选。

因此 Memory 是上下文提示，不是数据库里的强约束业务规则。

---

## 12. SSE 事件：前端如何看到执行过程

`/api/chat` 和 `/api/execute-plan` 使用 Server-Sent Events 流式返回。

常见事件：

| 事件 | 含义 |
|---|---|
| `text_chunk` | LLM 文本增量 |
| `tool_call` | 准备调用某个技能 |
| `tool_result` | 技能执行结果 |
| `table` | 新生成结果表 |
| `step_start` | 计划步骤开始 |
| `step_done` | 计划步骤完成 |
| `reflect_start` | 自检开始 |
| `reflect_done` | 自检完成 |
| `agent_pool_start` | 多 Agent 开始 |
| `agent_start` / `agent_done` | 某张表的 Agent 生命周期 |
| `aggregate_start` | 聚合 Agent 开始 |
| `skill_reused` | 检测到类似技能模式 |
| `skill_learned` | 生成新技能 |
| `workflow_id` | 返回本次工作流 ID |
| `error` | 执行错误 |
| `[DONE]` | 本次 SSE 完成 |

前端因此可以实时展示：

```text
正在分析 → 调用工具 → 生成中间表 → 继续分析 → 最终结论
```

---

## 13. 源码大致处理逻辑

### 13.1 `app.py`：接口编排层

```python
@app.post("/api/chat")
async def chat(request, user):
    state = get_user_state(user)
    tables = state["tables"]

    # 只有多表并且问题含比较关键词时才走多 Agent
    if multi_executor.should_activate(request.message, tables):
        generator = multi_executor.execute_multi(...)
    else:
        generator = executor.execute(...)

    # 把 Agent 事件包装成 SSE 返回浏览器
    async for event in generator:
        yield sse(event)

    # 当前聊天历史只保存用户消息；过长时先让 LLM 压缩
    chat_history.append({"role": "user", "content": request.message})
```

`app.py` 还负责：

- 注册、登录和 JWT session cookie。
- 用户 API Key 加密保存。
- 上传、创建、编辑、删除和下载表格。
- 触发计划、澄清、聊天和计划执行。
- 反馈、技能、记忆和 Growth Dashboard API。

### 13.2 `executor.py`：执行层

```python
async def execute(message, tables, history, result_tables_store, ...):
    workflow = WorkflowRecord(message, table_names)
    messages = build_messages(message, tables, history)
    tools = skill_registry.get_tool_definitions(...)

    async for event in _agent_stream(messages, tools, tables, result_tables_store):
        # tool_call 和 tool_result 被记录为 StepRecord
        # final_text 被记录为最终结论
        yield event

    workflow.finish(final_text)
    workflow.save()

    if auto_learn:
        skill_distiller.try_distill(message, tool_calls_log)
```

### 13.3 `registry.py`：工具注册层

```python
def get_tool_definitions(code_tool=False):
    tools = BUILTIN_TOOL_DEFS
    if code_tool:
        tools = [table_info, execute_python]
    if has_enabled_package_skills():
        tools += WORKSPACE_TOOL_DEFS
    return tools

def execute_sync(skill_name, params, tables):
    if skill_name == "execute_python":
        return execute_python(params, tables)
    return BUILTIN_SKILLS[skill_name](params, tables)
```

这里的关键是：工具定义以 OpenAI function calling 格式提供给 LLM，但函数本身由本地 Python 执行。

### 13.4 `builtin.py`：数据操作层

```python
def aggregate(params, tables):
    df = _get_table(tables, params["table_id"])
    result = (
        df.groupby(params["group_by"])
          .agg(params["agg_config"])
          .reset_index()
    )
    return {"df": result, "name": params.get("result_name", "aggregate")}
```

其他技能也是相同模式：

```text
根据 table_id 取 DataFrame
读取参数
调用 pandas
返回 DataFrame 或文字
```

### 13.5 `planner.py`：意图层

Planner 本身不执行数据操作。它只做两件事：

1. 用 LLM 判断是否需要澄清。
2. 用 LLM 生成 `{title, steps}` JSON。

计划中的每个 `description` 后续仍要交给 Agent，由 Agent 重新选择工具。

---

## 14. TabClaw 和 SheetAgent 的关键区别

| 对比点 | SheetAgent | TabClaw |
|---|---|---|
| 核心入口 | 输入 Excel + 期望输出 Excel | 用户上传表格 + 自然语言请求 |
| 程序比对 | 有输入输出结构 diff | 没有输入输出 diff 主流程 |
| AI 作用 | 根据 diff 推断变换代码 | 选择工具、生成参数、解释结果 |
| 执行方式 | Recipe 步骤可按顺序重放 | LLM 每次自主决定下一工具 |
| 持久化对象 | Recipe JSON | Workflow JSON + `SKILL.md` |
| 结果校验 | 有输出结构和数值验证规则 | 主要靠 Agent 自检和用户反馈 |
| 多表比较 | 可通过显式步骤做 join/lookup | 多表时按表并行 Agent，再由 LLM 汇总 |
| 适合场景 | 固定、重复、强一致性的 Excel 交付流程 | 交互式分析、探索、临时问题 |

最关键的一句话：

```text
SheetAgent 保存“怎么稳定地做完这件事”。
TabClaw 保存“上次 Agent 是怎么分析的，以及以后可以给 Agent 什么提示”。
```

---

## 15. 对快递行业月结账单平台的启发

TabClaw 的交互方式适合做前端体验：用户上传账单，输入“按客户汇总”“比较两个月差异”“找出异常订单”，Agent 负责理解需求并展示处理过程。

如果要做真正的生产账单交付，建议把 TabClaw 的 Agent 层和确定性执行层组合：

```text
TabClaw 风格 Agent
    ├─ 识别客户需求
    ├─ 识别字段和表之间的关系
    ├─ 生成候选处理步骤
    └─ 解释异常和给出预览

确定性账单引擎
    ├─ 字段映射和模板版本
    ├─ 订单号去重与多行合并
    ├─ 运费、重量、件数、附加费计算
    ├─ 客户专属规则
    ├─ 输出 Excel 模板
    └─ 行数、金额、订单数和总额校验
```

其中：

- TabClaw 的 `table_info`、`merge_tables`、`aggregate`、`add_column` 可以作为基础工具层。
- TabClaw 的 `WorkflowRecord` 可以作为任务审计日志。
- `SKILL.md` 可以保存客户的业务处理说明和字段解释。
- 生产系统仍需要额外的 Recipe/规则 JSON、字段别名映射、输入输出校验和失败重试。

一个更适合月结客户的持久化对象可以是：

```json
{
  "customer_id": "customer_001",
  "template_version": "2026-10",
  "inputs": [
    {"role": "order_detail", "file_pattern": "订单明细*.xlsx"},
    {"role": "price_table", "file_pattern": "价格表*.xlsx"}
  ],
  "steps": [
    {"action": "normalize_columns", "params": {"aliases": {"运单号": "waybill_no"}}},
    {"action": "merge", "params": {"left_on": "customer_id", "right_on": "customer_id"}},
    {"action": "group", "params": {"keys": ["customer_id", "waybill_no"]}},
    {"action": "calculate", "params": {"amount": "weight * price + surcharge"}}
  ],
  "validation": {
    "required_columns": ["customer_id", "waybill_no", "amount"],
    "amount_tolerance": 0.001,
    "row_count_tolerance": 0.05
  }
}
```

这部分是面向业务平台的设计建议，不是 TabClaw 当前源码中的数据结构。

---

## 16. 源码阅读地图

如果要继续深入，建议按下面顺序读：

1. `app.py`：先看 API 如何把请求接到 Agent。
2. `agent/executor.py`：重点看 `_agent_stream()`、`execute()` 和 `execute_plan()`。
3. `skills/registry.py`：看工具定义如何暴露给 LLM。
4. `skills/builtin.py`：看 pandas 操作的真实执行方式。
5. `agent/multi_agent.py`：看多表并行和汇总。
6. `agent/workflow_recorder.py`：看工作流日志和模式统计。
7. `agent/skill_distiller.py`：看技能如何生成、发现和升级。
8. `skills/code_skill.py`：看 Python 沙箱限制。
9. `agent/memory.py`：看记忆如何保存和筛选。
10. `static/app.js`：看 SSE 事件如何显示在页面。

---

## 17. 最终结论

TabClaw 的核心流程可以压缩成一句代码流程：

```text
上传表格
→ pandas DataFrame
→ LLM 选择工具
→ 执行 pandas
→ 结果表回写 tables
→ tool_result 回 LLM
→ 继续 ReAct
→ SSE 返回前端
→ 记录 Workflow
→ 可选生成 SKILL.md
```

它的核心价值是交互式 Agent 和工具编排，不是固定的 Excel 配方执行器。

对于你的快递行业月结账单平台，最值得借鉴的是：

- 用自然语言收集客户的定制要求。
- 用 `table_info` 和字段样例识别输入表结构。
- 用原子工具组合合并、聚合、计算和分析。
- 用 Workflow JSON 留下每次处理的审计记录。
- 用技能包沉淀客户的重复处理习惯。

但最终交付金额必须落到确定性规则和校验体系上：字段映射、订单号合并、金额汇总、模板输出和总额校验都应该有可以重复执行的规则对象。

