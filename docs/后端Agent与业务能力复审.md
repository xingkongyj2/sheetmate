# 后端 Agent 与业务能力复审

复审日期：2026-11-10。代码基线：`f235e46`，结合当前工作区实际文件检查。此次新增复审文档和合成复现材料，未修改产品代码。

**结论：现有后端已经建立了较完整的 Agent 与 Excel 处理能力，下一轮收益最高的工作是统一业务验收、贯通输入质量和固定复用依赖。部分单点检查已经实现，但在另一个入口、状态汇总或保存环节失效，仍会出现“执行成功、业务不正确”。**

## 1. 复审范围与实际验证

对照阅读了《后端Agent优化与验收》《表格探测与长期记忆》《思考模式与消耗页验收》、开发任务清单和原技术设计 DOCX；SheetAgent、TabClaw 分析文档用于理解设计来源，不作为当前实现证据。

检查覆盖 `agent`、`recipe`、`engine`、API、service、数据库和现有测试。原设计以本地单机、单表约 3,000 行为主要场景，本报告据此安排优先级。

| 验证 | 本次结果 | 含义 |
| --- | --- | --- |
| `backend/.venv/bin/python -m pytest -q backend/tests` | **178 passed，16.72 秒** | 当前已存在的后端测试通过 |
| `backend/.venv/bin/python backend/evals/run_offline.py` | **13/13，0 次 API 调用** | 原有离线合成验收通过 |
| 本次补充的业务复现 | 10 个合成场景 | 覆盖跨层验收、质量、输入绑定、依赖版本等缺口 |
| 本次补充的 Agent 复现 | 3 个场景 | 并发交错、记忆召回、假模型错误验收 |
| 本次补充的平台复现 | 3 个场景 | 上传阻塞、Replay 异常终态、事务回滚 |
| 真实模型质量/成本评测 | 未执行 | 不据此声称真实准确率、成本或时延提升 |

复现脚本与输出在 [审计附件目录](/Users/xingkong/PycharmProjects/sheetmate/docs/audits/2026-11-10/README.md)。所有样本使用内存或临时目录；假模型仅用于测试防线，不代表真实模型出错概率。并发复现通过 Barrier 固定一种合法交错；耗时数字只是当前机器的一次观测。

**现有测试通过不能替代业务验收。** 例如 [test_recipe.py:182](/Users/xingkong/PycharmProjects/sheetmate/backend/tests/test_recipe.py:182) 明确断言错误级规则找不到列/sheet 时只告警，说明部分测试保留了旧策略，需要先统一业务契约再修改断言。

## 2. 前一轮已经落地、应保留的能力

- 用户要求引用原始消息，支持长会话原文回查；执行证据带身份和版本，能够跨轮查询。
- 公式、SQL、Python 混合执行；原文件保护、输出副本、脚本引用、运行快照、断点重跑已经具备。
- 结构化输出校验、工具参数整批预检、工具按需加载、计划依赖和失败阻断已经实现。
- 表格画像、公式缺缓存/错误检测、查询缓存、长期记忆来源和人工规则保护已有基础。
- 模型调用预算与消耗记录已有实现，预算默认关闭是已有产品选择，本复审不把它当成缺陷。
- 显式保存流程授权、候选文件和正式交付区分已经建立。

需要进一步解决的是这些机制之间的衔接。例如 reader 能识别错误，并不等于 Recipe 入口会保留该错误；verifier 能判断失败，并不等于流程保存入口会拒绝该失败。

## 3. 优先修复的业务正确性问题

### B1. 人工样例比对失败，仍可保存成正式流程【P1，已复现】

**触发：** 学习流程的步骤能运行，但生成结果与人工样例不同；外层修复次数耗尽。

**证据：** [pipeline.py:169](/Users/xingkong/PycharmProjects/sheetmate/backend/sheetmate/recipe/pipeline.py:169) 仅根据 runner 状态计算 `ok`，随后第 185 行按 `ok` 保存。[host.py:558](/Users/xingkong/PycharmProjects/sheetmate/backend/sheetmate/services/host.py:558) 接收 `verification`，但没有使用它阻止保存。

**复现：** 人工结果金额 100，生成步骤输出 99；默认两次修正、共三轮比对均失败，最终仍有 `pipeline_ok=true`、`verification=fail`、`recipe_saved`。使用真实 runner 和真实保存门槛，学习器及最终数据库写入替换为本地假实现。

**改造：** 建立一个供完成状态、正式交付、流程保存共同使用的验收结果。学习模式下，作为验收标准的人工样例比对失败必须阻断正式复用；失败候选可供诊断。修订模式下旧样例可以仅供参考，但新要求和未变的业务约束仍需检查。

**验收：** 连续修复失败后，系统保留诊断记录，不能发出正式流程保存成功事件。

### B2. 硬规则没有执行，却被汇总为“校验通过”【P1，已复现】

**证据：** [validator.py:32](/Users/xingkong/PycharmProjects/sheetmate/backend/sheetmate/recipe/validator.py:32) 将规则执行异常变成 warning；第 172、198 行对缺 sheet/列也只告警。第 38 行汇总先处理 warning，后处理 `not_evaluated`。类似顺序还存在于 [executor.py:823](/Users/xingkong/PycharmProjects/sheetmate/backend/sheetmate/recipe/executor.py:823)。

**复现：**

- `severity=error` 的非空规则指向不存在的 sheet，最终为 `warning`，摘要为“校验通过，有 1 项警告”。
- 未知硬规则单独存在时，最终失败；添加一列全空“备注”产生普通 warning 后，同一未执行硬规则被汇总为 warning，运行得以放行。

**改造：** 分开记录规则是否执行和业务是否通过。必需规则未执行与硬失败均阻断；warning 不能覆盖它们。所有检查点和最终检查共用一个汇总函数。

**验收：** 增加无关警告不能让失败任务变成功；错误级规则缺列、缺 sheet、表达式无效、类型未知均给出明确阻断。

### B3. 输入质量在 Recipe 路径丢失，金额转换还能静默丢值【P1，已复现】

**证据：** [executor.py:336](/Users/xingkong/PycharmProjects/sheetmate/backend/sheetmate/recipe/executor.py:336) 注册输入表时未传入 `SheetData.quality`。公式入口虽检查质量，拿到的却可能已经是空元数据。[builtin.py:45](/Users/xingkong/PycharmProjects/sheetmate/backend/sheetmate/engine/formulas/builtin.py:45) 文本转数字使用 `TRY_CAST`，没有配套转换失败计数。

**复现：**

- 真实合成 XLSX 的金额为 `100`、`#DIV/0!`。reader 正确识别 `B3` 为错误；Recipe 工作区 `read_quality={}`，汇总输出 100，运行和校验均成功，输入及步骤均没有 warning。
- 金额为文本 `100`、`¥900` 时，汇总得到 100.0，没有转换失败提示。
- [validator.py:189](/Users/xingkong/PycharmProjects/sheetmate/backend/sheetmate/recipe/validator.py:189) 的运行合计守恒默认仍允许 1% 相对误差。输入 1,000,000、输出 991,000 的 error 级规则判定通过。人工样例 verifier 中更严格的容差没有统一到此处。

**改造：** 统一文件、DataFrame、工作区表和脚本输入的质量信息；重命名后继续跟踪受影响列。转换失败必须输出数量、样例和定位，关键金额列默认阻断。金额、数量、百分比各自声明类型、单位、舍入阶段和绝对误差；需要相对误差的规则显式声明。

**验收：** 同一坏文件经上传分析、Recipe 执行、断点重跑和脚本路径得到一致的质量结论；不能把错误值当缺失值悄悄聚合。

**关联代码风险，尚未做完整交付复现：** [script_execution.py:52](/Users/xingkong/PycharmProjects/sheetmate/backend/sheetmate/services/script_execution.py:52) 只把 `ws.df()` 交给脚本，未带质量门槛。应区分“从原件完整重读并重算”和“仅持有缺损 DataFrame”，保留前者能力，对后者传播限制。

### B4. 同结构月份文件可能按上传顺序绑定错角色【P1，已复现】

**证据：** [matcher.py:61](/Users/xingkong/PycharmProjects/sheetmate/backend/sheetmate/recipe/matcher.py:61) 的并列保护仅覆盖 `kind=file`；`kind=table` 仍按分数稳定排序后贪心选择。[replay.py:104](/Users/xingkong/PycharmProjects/sheetmate/backend/sheetmate/services/replay.py:104) 使用该结果直接绑定输入。

**复现：** “上月/本月”两个输入面对同列、同 sheet、同文件模式的 9 月和 10 月账单，两候选都得 1.4 分。倒转候选顺序，上月/本月绑定随之交换，没有歧义提示。

这与此前已修复的“文件内 sheet 选择”是两个入口，不能将后者的保护视为已经覆盖前者。

**改造与验收：** 所有输入类型都检查唯一性和分差；将账期、客户、输入角色纳入绑定。候选排列变化不应改变确定的业务绑定；不确定时返回具体候选，让用户只补充缺失角色。

### B5. 同一个流程版本，公式更新后重跑结果会变【P1，已复现】

**证据：** [schema.py:68](/Users/xingkong/PycharmProjects/sheetmate/backend/sheetmate/recipe/schema.py:68) 的 RecipeStep 没有公式版本或定义 hash；[formulas/__init__.py:66](/Users/xingkong/PycharmProjects/sheetmate/backend/sheetmate/engine/formulas/__init__.py:66) 总取最新客户公式。普通重放及断点重跑都获取当前公式库，见 [replay.py:123](/Users/xingkong/PycharmProjects/sheetmate/backend/sheetmate/services/replay.py:123)。

**复现：** Recipe v1 和输入金额 100 不变，客户公式从 `金额*1` 更新为 `金额*2` 后，结果从 100 变为 200。

**改造与验收：** 保存流程时固定公式版本和定义摘要，运行保留依赖清单；升级依赖作为显式流程变更。验证同一流程版本、同一输入、同一参数可重放一致。字段映射、价格表和业务规则版本随后纳入同一清单。步骤里的审计 SQL 不等于依赖已锁定。

### B6. 输出列缺失仍显示成功【P1，已复现】

**证据：** [executor.py:992](/Users/xingkong/PycharmProjects/sheetmate/backend/sheetmate/recipe/executor.py:992) 对 `output_spec.columns` 取存在列的交集，静默跳过缺列；默认验证没有对该输出约定逐列核对。

**复现：** 要求输出“金额、税额”，实际只有“金额”；最终 `success`、`validation=pass`。

**改造与验收：** 输出约定包含必需/可选列、类型、顺序、动态扩展列及允许空结果。写出前核对，缺必需列直接阻断；动态分类新增列按约定保留。格式保留之外，也需要验收交付数据结构。

## 4. Agent 能力的下一步

### A1. 并行分析的结果身份需要真正隔离【P1，已复现】

[multi_agent.py:89](/Users/xingkong/PycharmProjects/sheetmate/backend/sheetmate/agent/multi_agent.py:89) 为分支设置了权限，但分支共用 Workspace。[tools.py:259](/Users/xingkong/PycharmProjects/sheetmate/backend/sheetmate/agent/tools.py:259) 先取唯一名称，再创建表；[workspace.py:202](/Users/xingkong/PycharmProjects/sheetmate/backend/sheetmate/engine/workspace.py:202) 使用 `CREATE OR REPLACE`，两者不是一个原子操作。

固定并发交错后，两个专员都拿到“汇总”，分别计算 30 和 300，最终只剩其中一份；两个分支的权限集合都包含该名字。最终留下哪份取决于调度。

建议用分支 ID 和产物 ID 作为真实身份，展示名另存；分配与创建原子化。验收同时检查数值、血缘、访问权限，不只检查线程是否报错。

### A2. 完成核对应有程序可检查的业务验收条件【P1，结构防线复现】

[completion.py:105](/Users/xingkong/PycharmProjects/sheetmate/backend/sheetmate/agent/completion.py:105) 检查引用是否属于成功工具结果，但没有根据任务目标强制检查数据完整性、执行类型和必需交付。

假模型引用 `table_info` 的成功结果，证据已标记 `full_data=false`，仍声称整份订单统计完成；当前函数返回 `completed=true, verified=true`。这是故障注入，说明程序不能拦住这种模型错误，不表示真实模型必然这样回答。

建议从现有 task_state 增量扩展任务验收对象：输入角色和版本、统计范围、必需产物、业务断言、用户原文来源。模型处理语义和歧义，程序核对可确定的条件；失败时只补做缺失目标。避免单纯增加同类模型自检调用。

### A3. 业务记忆召回与长期映射来源仍需补齐【P2】

**已复现：** [memory.py:151](/Users/xingkong/PycharmProjects/sheetmate/backend/sheetmate/agent/memory.py:151) 将 `domain_knowledge` 无条件放入常驻组，第 168 行取前 30 条。30 条无关规则之后的一条精确命中“退款金额计算规则”仍被丢弃。现有相关性保底主要保护 history_insights，没有覆盖业务规则。

建议按精确键、实体、任务相关性排列业务规则；偏好与业务规则分配不同名额，并提供完整规则回查。可先使用现有文本检索，不必立即引入向量库。

**代码确认：** [host.py:625](/Users/xingkong/PycharmProjects/sheetmate/backend/sheetmate/services/host.py:625) 保存字段映射没有 `remember` 同等的用户来源校验，[services/memory.py:259](/Users/xingkong/PycharmProjects/sheetmate/backend/sheetmate/services/memory.py:259) 对 AI 新建映射直接写 `confirmed=True`。建议区分本轮适配和长期映射，保留来源及验证证据，尤其避免把“ERP 单号”和“订单号”的猜测长期固化。

## 5. 平台运行与性能

| 问题 | 证据与实际影响 | 建议和验收 |
| --- | --- | --- |
| 活跃 Workspace 被 LRU 关闭【P1，已复现】 | [workspace.py:397](/Users/xingkong/PycharmProjects/sheetmate/backend/sheetmate/engine/workspace.py:397) 超容量直接关闭旧连接，没有任务持有计数。`max_open=1`，持有 A 后打开 B，再用 A 得到 `Connection already closed`。运行工作区上限目前是 12 | 活跃任务持有 lease/pin，只淘汰空闲连接；等待模型时浏览其他历史不能中断任务 |
| 上传同步处理阻塞事件循环【P1，已复现】 | [api/conversations.py:162](/Users/xingkong/PycharmProjects/sheetmate/backend/sheetmate/api/conversations.py:162) 在 async 路由内同步解析、画像、加载。附件记录的 3,000×10 合成 XLSX 上传为 0.494 秒，20ms 心跳最大间隔 0.483 秒；大小检查在全量读取后 | 分块读取并提前限额；耗时工作进入有界 worker，在线程内创建数据库 Session；实测上传期间 SSE 和停止请求的响应 |
| Replay 异常没有持久终态【P2，已复现】 | [jobs.py:108](/Users/xingkong/PycharmProjects/sheetmate/backend/sheetmate/services/jobs.py:108) 的持久兜底面向有 message_id 的聊天任务。[replay.py:131](/Users/xingkong/PycharmProjects/sheetmate/backend/sheetmate/services/replay.py:131) 工作区打开失败后，job 已结束，Run 仍为 running、finished_at 为空 | Job 关联 run_id，统一幂等终态更新；故障注入覆盖启动、执行、取消、收尾各阶段 |
| 文件创建与数据库回滚不同步【P2，已复现】 | [files.py:176](/Users/xingkong/PycharmProjects/sheetmate/backend/sheetmate/services/files.py:176) 先写正式文件，再入数据库；下游异常回滚后，记录不存在但原件留在磁盘 | 文件暂存、事务回滚补偿、提交后发布，工作区加载也补偿；增加宽限期内的孤儿回收 |
| 打开会话扫描客户全部历史【P2，代码确认，未压测】 | [conversations.py:147](/Users/xingkong/PycharmProjects/sheetmate/backend/sheetmate/services/conversations.py:147) 汇总客户会话、助手事件和历史运行工作区；成本随历史增长 | 消息/运行/资源目录分页，目录增量存 SQLite；对 100/1,000 次历史运行测接口时延与响应体积 |

这些问题可以先在现有单机架构内解决。队列、数据库或文件存储是否更换，应由实际并发、容量和恢复要求决定。Windows 无操作系统级脚本隔离已经在 README 披露，本报告将其保留为部署边界，不列作新发现。

## 6. 建议分三批实施

| 批次 | 改造范围 | 验收出口 |
| --- | --- | --- |
| 第一批：封住错误放行 | B1、B2、B3、B6；统一验收汇总与保存/交付门槛 | 错误样例、未执行硬规则、读取错误、转换失败、缺输出列均不能成为成功交付或正式流程 |
| 第二批：保证重复执行一致 | B4、B5、A1；工作区 lease；Replay 终态 | 输入顺序无关、依赖可重放、分支产物不覆盖、活跃连接不淘汰、任务终态一致 |
| 第三批：提高任务理解与业务效率 | A2、A3；上传 worker；历史分页；真实业务评测 | 要求可验证、相关规则可找回；上传不拖慢会话；真实任务质量和耗时有可比较基线 |

后续能力建设建议围绕业务契约展开：

1. **输入角色和业务期间。** 将客户、账期、合同/价目表版本、来源表角色作为明确参数，减少凭文件名和上传顺序猜测。
2. **可解释的对账包。** 除最终账单，自动生成未匹配、重复键、无效数值、金额差异明细；每项可以追溯到输入文件、sheet、行和规则。
3. **流程依赖清单和变更验收。** 公式、脚本、字段映射、业务规则发生变化时，展示受影响流程；用保存的脱敏样例验证，再升级对应流程版本。
4. **定向恢复。** 根据数据血缘重跑受影响步骤，在约束或输入改变时重新验证上游；恢复状态包含剩余目标及已确认产物。
5. **批量业务执行。** 在前两批完成后复用确定性流程，对文件批次逐项记录成功、失败、重试与交付，先做有界并发和幂等，再考虑定时调度。

以上为能力建设方向，不是已经完成的实现。优先扩展现有数据模型、工具和运行记录，保留当前主架构。

## 7. 验收文档和评测基线需要同步修订

旧验收文档出现 307、183 等不同阶段测试计数；当前实际收集并执行为 178。README 引用的 `backend/tests/test_agent_budget_regressions.py`、消耗页验收引用的 `backend/tests/test_chat_usage.py` 在当前文件树不存在。仅据当前快照无法判断原因，不能把历史计数直接视为当前覆盖率。

建议给每次验收记录提交号、测试清单、命令、输出文件和运行环境；恢复或重新建立缺失的边界回归。现有 13 项离线样例应扩展到本报告的跨层失败路径，而不是只增加底层函数测试数量。

真实业务评测使用固定、脱敏的任务集，覆盖跨月对账、退款、同结构多文件、错误金额、缺公式缓存、长会话口径修改和跨客户隔离。至少分别记录：错误放行数、完整任务成功率、关键金额差异、澄清次数、首次成功率、重试数、模型调用数/Token，以及端到端 P50/P95。将真实模型评测与本地确定性回归分开报告。

本次没有验证真实模型的业务准确率，也没有修改前端现有未提交变更。
