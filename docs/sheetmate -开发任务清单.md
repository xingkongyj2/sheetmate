# SheetMate · 开发任务清单

> 面向 AI 编码助手与开发者的可执行任务清单。每个任务自包含（目标 / 背景 / 要求 / 技术要点 / 产出 / 验收 / 依赖），AI 可独立领取执行。
> 本项目为**全新项目**（前后端均从零搭建），与 `sf-portal-new` 无关，仅复用其 DaisyUI 规范文档。

---

## 0. 全局约定（所有任务必须遵守）

### 0.1 技术栈（全新搭建）

| 端 | 选型 | 说明 |
|---|---|---|
| 前端 | Vue 3.5 + Vite 6 + TypeScript + **daisyUI 5** + **Tailwind CSS 4** + **lucide-vue-next** + pinia + vue-router + axios | 全新搭建 |
| 后端 | Python Flask + SQLAlchemy + pydantic + DuckDB + openpyxl | 全新搭建 |
| 数据库 | SQLite（`data/app.db`） | 单文件、零部署 |
| 文件 | 本地目录（`data/uploads/`、`data/outputs/`） | 按客户隔离 |
| 唯一外部依赖 | LLM API | 其余全部本地 |

- 项目根目录：`sheetmate`（代码仓库名，小写），前后端同仓。
- 将 daisyUI 规范文档 `SKILL.md`（参考 `D:\Work\Project\sf-portal-new\SKILL.md`）复制到本项目根目录，作为 UI 编码依据。

### 0.2 代码质量规范（硬性要求）

- **模块清晰**：前端按 feature-module 分目录（每个 view 目录含 `api.ts / constants.ts / types.ts / validation.ts / *.vue`）；后端按 `api / services / agent / models / store` 分层，单一职责。
- **函数抽象**：单一职责、短函数、纯函数优先；重复逻辑抽公共函数/工具；避免 200 行以上巨型函数。
- **注释清晰**：每个模块、每个公开函数写 docstring/注释，说明「做什么 + 为什么」；关键算法与业务规则（如"匹配不到写新食研"）必须有注释。
- **命名**：语义化命名，无 `a/b/tmp/xxx2`。

### 0.3 UI 规范（daisyUI 5 + lucide-vue-next，硬性要求）

- 所有 UI 组件**必须用 daisyUI 5 class**，不手写等价自定义 CSS。以项目根 `SKILL.md` 为准。
- Tailwind CSS 4 + daisyUI 5：CSS 入口 `@import "tailwindcss"; @plugin "daisyui";`。
- 常用组件映射：按钮 `btn`、表格 `table`、卡片 `card`、弹窗 `modal`、侧栏 `drawer`、导航 `navbar`、标签页 `tab`、步骤 `steps`、进度 `progress`、徽标 `badge`、提示 `alert`、输入 `input`、下拉 `select`、文件选择 `file-input`、加载 `loading`、骨架 `skeleton`、提示条 `toast`、悬浮提示 `tooltip`、步骤指示 `steps`。
- **语义色**：优先 `primary / base-100 / base-content / error / success / warning / info`；**不要**用 Tailwind 固定色名（如 `text-gray-800`）做文字（深色主题下会不可读）。
- **默认 variant**：未指定强调时用默认（如 `btn` 而非 `btn btn-primary`）；`primary` 一页只用一次（最重要动作）。
- 组件选择遵循 SKILL.md 的「组件发现协议」：先识别功能 → 选候选组件 → 读对应指南 → 用精确结构。
- 图标**一律用 lucide-vue-next**，不引入其他图标库、不手写 SVG。
- 布局用 `flex / grid` + 响应式前缀（`sm:` `lg:`）。

### 0.4 仓库结构（Phase 0 建好）

```
sheetmate/                      # 新项目根
├─ frontend/                    # Vue 3 + Vite + TS（新建）
│  └─ src/
│     ├─ views/                 # 页面（按 feature-module）
│     ├─ components/            # 通用组件
│     ├─ stores/                # pinia
│     ├─ api/                   # axios 封装
│     ├─ composables/           # useToast 等
│     └─ style.css              # @import tailwindcss; @plugin daisyui;
├─ backend/                     # Flask（新建）
│  ├─ app.py                    # 入口
│  ├─ config.py
│  ├─ api/                      # 路由：auth / customers / formulas / tasks / files
│  ├─ services/                 # profile / executor / validator / retriever / field_mapping
│  ├─ agent/                    # orchestrator（外层计划）+ repair（内层 ReAct）
│  ├─ models/                   # SQLAlchemy 模型
│  ├─ store/                    # 本地文件读写
│  └─ data/                     # app.db / uploads / outputs / configs(公式配方JSON)
├─ SKILL.md                     # daisyUI 规范（从 sf-portal-new 复制）
└─ README.md
```

### 0.5 任务执行约定

- 按任务 ID 顺序执行；标注了「依赖」的任务需先完成依赖项。
- 每个任务完成后自测通过验收标准再标记完成。
- 涉及接口的前后端任务，以任务内给出的 REST 契约为准（字段、方法、路径）。

---

## 1. Phase 0 — 基础设施

### T0.1 项目脚手架（前后端）
- **目标**：建出可运行的前后端空壳。
- **要求**：
  - 创建项目根 + `frontend/`（Vite + Vue + TS，安装 daisyUI 5 / tailwindcss 4 / lucide-vue-next / pinia / vue-router / axios）与 `backend/`（flask / flask-cors / sqlalchemy / pydantic / duckdb / openpyxl / python-dotenv）。
  - 前端 `style.css` 写入 `@import "tailwindcss"; @plugin "daisyui";`；`main.ts` 挂载 pinia + router。
  - 后端 `app.py` 应用工厂 + 蓝图；`config.py` 用 `.env`；提供 `GET /api/health`；全局异常处理返回 `{ "ok": false, "error": { "type", "message" } }`。
  - 复制 `SKILL.md` 到项目根。
- **技术要点**：前端 `npm create vite`；后端应用工厂 `create_app()`。
- **产出**：前端 `npm run dev` 可跑；后端 `flask run` 可跑；`/api/health` 200。
- **验收**：两端可启动，前端可调通后端 health；目录符合 0.4。
- **依赖**：无。

### T0.2 SQLite 数据模型
- **目标**：建 6 张表 + 初始化。
- **要求**：SQLAlchemy 定义并 `create_all`：
  - `users(id, account, password_hash, role)`
  - `customers(id, name, code)`
  - `user_customers(user_id, customer_id)` 多对多
  - `formulas(id, name, scope, customer_id, type, params_schema, sql_template, example, version, status, created_by)`
  - `recipes(id, name, scope, customer_id, steps, version, status)`
  - `tasks(id, customer_id, user_id, request_text, resolved_plan, status, error, created_at)`
  - `scope ∈ {global, customer}`；`customer_id` 仅 customer 时非空；`status ∈ {draft, active, deprecated}`；`created_by ∈ {ai, human}`。
- **技术要点**：SQLite 单文件；外键启用。
- **验收**：初始化后 6 表存在；能插入/查询 user、customer。
- **依赖**：T0.1。

### T0.3 本地文件存储与配置目录
- **目标**：文件读写与公式/配方 JSON 目录。
- **要求**：`store/` 提供 `save_upload / save_output / load_config`；`data/configs/formulas/{global|customers/{id}}/`、`data/configs/recipes/...` 自动创建；文件名加时间戳，按 customer_id 隔离。
- **验收**：上传保存、下载读取正常；configs 目录结构正确。
- **依赖**：T0.1。

### T0.4 前端基础封装
- **目标**：axios 客户端、toast、通用组件。
- **要求**：`src/api/http.ts`（axios 实例 + 拦截器）；`src/composables/useToast.ts`（对接 daisyUI toast）；`src/components/` 下 FileUploader、ConfirmDialog 等通用组件（全部 daisyUI class + lucide 图标）。
- **技术要点**：类型化 API 封装，每个资源一个 `api.ts`。
- **验收**：前端能调后端 health；toast/确认框/上传组件可用。
- **依赖**：T0.1。

---

## 2. Phase 1 — 引擎闭环（M1，先不接 Web，CLI 验证）

### T1.1 Profile 引擎
- **目标**：读 Excel/CSV 抽取「画像」。
- **要求**：`services/profile.py` 输出每 sheet 的：sheet 名、表头行、列名、dtype、行数、空值率、每列前 5 样本、重复键检测；支持多 sheet、表头不在第一行（探测）。
- **技术要点**：openpyxl 读 + pandas 算；输出 JSON。
- **验收**：样例 Excel 画像正确。
- **依赖**：T0.1。

### T1.2 字段标准化（别名映射）
- **目标**：把各表实际列名统一成标准名。
- **要求**：`services/field_mapping.py` 按客户存映射 `{标准字段: [实际列名...]}`；读入时重命名；未命中列保留原名并告警。映射存 `data/configs/field_mapping/{customer_id}.json`。
- **技术要点**：语义相近不同字段（ERP单号 vs 订单号）不合并；留 AI 提议 + 人工确认接口。
- **验收**：两张不同列名的表读入后统一为标准列名。
- **依赖**：T1.1。

### T1.3 公式/配方 JSON Schema 与校验
- **目标**：定义并校验公式/配方 JSON。
- **要求**：pydantic 定义公式（id/version/scope/customer_id/type/name/params/sql/code/example/status/created_by）、配方（id/version/scope/customer_id/name/steps/status）、步骤（formula/params/output）。`params` open schema：`{name: {type, required, default, desc, values?, item_type?}}`，值类型 `table|str|int|number|bool|list|enum|any`。校验：引用表存在、列存在、枚举合法、必填齐全。
- **技术要点**：params 声明（公式内）与取值（步骤内）两种身份。
- **验收**：合法通过，非法报精确错误。
- **依赖**：无。

### T1.4 DuckDB 执行器
- **目标**：执行 SQL 模板实例化后的语句。
- **要求**：`services/executor.py` 把 `sql`（`{{param}}` 占位）填参后交 DuckDB 执行；支持 `read_xlsx`/`read_csv`；只读执行不写源文件；返回 DataFrame 预览。
- **技术要点**：DuckDB 内存运行；安全参数替换（防注入）。
- **验收**：带占位符的 PIVOT SQL 填参执行正确。
- **依赖**：T1.1、T1.3。

### T1.5 基础公式实现
- **目标**：最小可用公式集。
- **要求**：`read`（读文件→命名表）、`pivot_wide`（长转宽：index/pivot_col/value_col/carry_cols/fill/aggfunc）、`write`（写新文件，多 sheet）。每个公式注册到公式库。
- **技术要点**：pivot_wide = group_aggregate + unstack + carry + fillna；carry_cols 常量列跟随。
- **验收**：`read→pivot_wide→write` 把「每订单3行」转「每订单1行3列」。
- **依赖**：T1.3、T1.4。

### T1.6 CLI 跑通「长转宽」
- **目标**：命令行端到端验证 M1。
- **要求**：`cli.py` 接收「配方 JSON + 输入文件」→ 逐步骤执行 → 输出结果 + 每步中间结果预览。
- **技术要点**：为 agent 预留 orchestrator 接口。
- **验收**：「3费用拆3列」样例跑通。
- **依赖**：T1.5。

---

## 3. Phase 2 — 知识层 + Agent（M2）

### T2.1 公式库/配方库 CRUD + 检索
- **要求**：按 scope 提供 CRUD；检索优先级：客户配方 > 客户历史 > 公共配方 > 公共公式组合 > 从零生成；目录直读起步。同 name 时 customer 覆盖 global；版本化（改 = 升 version，旧版保留）。
- **验收**：能存/查/改公式配方；同名覆盖生效。
- **依赖**：T0.2、T1.3。

### T2.2 Agent 编排器（外层 Plan-and-Execute）
- **要求**：`agent/orchestrator.py` 接收「自然语言需求 + 文件画像」→ 调 LLM 生成配方（命中公式则只填参）→ 逐步骤执行 → 返回结果 + 进度。LLM 调用抽象成独立接口。
- **技术要点**：prompt 注入画像 + 公式目录（id/name/intent/triggers）+ 示例；输出严格 JSON，pydantic 校验，非法带错误重试 1 次。
- **验收**：一句话「3种费用拆3列」自动生成正确配方并执行。
- **依赖**：T1.3、T1.6、T2.1。

### T2.3 内层 ReAct 修复 + 容错
- **要求**：`agent/repair.py` 捕获某步错误 → 打包结构化上下文（哪步/错误类型+信息/输入表结构+样本/attempt/history）→ LLM 生成修复 → 重跑该步（复用中间表）→ 再校验。上限 2~3 次，超限生成人话错误报告。
- **技术要点**：只修技术层，不碰业务规则；单步局部重试。
- **验收**：制造列名错误能自动修复；连错 3 次产出报告。
- **依赖**：T2.2。

### T2.4 校验断言
- **要求**：配方/步骤挂断言 `row_count_range / match_rate_min / no_duplicate_keys / not_empty`；不通过触发 T2.3。
- **验收**：制造匹配率过低，断言触发进入修复/上报。
- **依赖**：T2.2。

### T2.5 记忆（任务流水 + 固化配方）
- **要求**：`tasks` 记录每次处理的请求/计划/结果；成功处理可一键固化为客户配方（draft→人工确认 active）；新任务先检索该客户历史。
- **验收**：同客户二次处理命中历史配方，无需 AI 重新生成。
- **依赖**：T2.1、T2.2。

---

## 4. Phase 3 — Web 化（M3）

### T3.1 认证与权限
- **要求**：`POST /api/auth/login` 返回会话/令牌；中间件按 user_customers 过滤；管理员角色可进后台；密码哈希存储。
- **验收**：不同账号登录后只见各自客户。
- **依赖**：T0.2。

### T3.2 客户管理 API
- **要求**：`GET /api/customers`、`GET /api/customers/{id}`（详情）、`GET /api/formulas/global`、`POST /api/customers/{id}/formulas`、`PUT /api/formulas/{fid}`。
- **验收**：CRUD 正确，权限隔离正确。
- **依赖**：T2.1、T3.1。

### T3.3 文件上传 API
- **要求**：`POST /api/files` 上传到 `data/uploads/{customer_id}/`；`GET /api/tasks/{id}/download` 下载；限制大小、校验扩展名、文件名防注入。
- **验收**：上传/下载正常，按客户隔离。
- **依赖**：T0.3。

### T3.4 任务 API（含单步跑）
- **要求**：`POST /api/tasks`（提交）→ 后台异步跑 agent → `GET /api/tasks/{id}`（状态/进度）→ `POST /api/tasks/{id}/step/{n}`（单步运行）→ `POST /api/tasks/{id}/confirm` → 下载。状态 `pending/running/success/failed/escalated`。
- **技术要点**：本地优先线程/进程内队列；单步跑返回该步输入/输出预览。
- **验收**：提交→执行→单步查看→确认→下载全链路。
- **依赖**：T2.2、T3.3。

### T3.5 前端：登录 + 客户列表 + 客户详情
- **要求**：登录页（`card`+`input`+`btn`）；客户列表（`table`/`card`，`badge` 状态）；客户详情（公共公式只读 + 自定义公式可改两区 + 处理历史，`tab` 切换）。
- **技术要点**：vue-router；useToast；lucide 图标（Users/FileSpreadsheet/History）。
- **验收**：页面正常、数据来自后端、权限隔离生效。
- **依赖**：T3.1、T3.2、T0.4。

### T3.6 前端：上传 + 需求输入 + 任务执行
- **要求**：上传用 FileUploader；需求输入 `textarea`；任务执行页用 `steps` + `progress`，每步可点开看输入/输出预览（`table`）；错误 `alert alert-error` 显示人话报告；单步结果 `modal` 展示。
- **技术要点**：轮询/SSE 拿状态。
- **验收**：上传→提交→分步进度→单步看结果→出错显示报告。
- **依赖**：T3.3、T3.4。

### T3.7 前端：管理后台（公式/配方编辑 + 试运行）
- **要求**：公式列表（`table`）；「新增/编辑」用**表单**（非手写 JSON）：参数按 params schema 动态渲染 `input/select`，类型/必填当场校验；「试运行」调用试跑接口显示预览；高级模式 JSON 编辑器（实时校验定位错误行）。
- **技术要点**：动态表单用 `fieldset`+`label`；JSON 编辑器可先用 `textarea`+前端校验。
- **验收**：管理员填表单生成合法公式、试运行看预览、保存。
- **依赖**：T3.2、T3.4。

### T3.8 配方可视化（流程图）
- **要求**：组件读取配方 steps，横向步骤链（`ul.steps` 起步，逐步增强为图形化），每节点显示公式 name + 人话说明，箭头连接，附「前后对比」卡片，支持单步高亮当前步骤。
- **技术要点**：两层分离——展示用人话不露 SQL。
- **验收**：配方显示为清晰分步流程图，业务能看懂。
- **依赖**：T3.5、T3.6。

---

## 5. Phase 4 — 扩展（M4）

### T4.1 更多公式
- **要求**：`lookup`（匹配加列：duplicates 策略 + missing_fill）、`groupby_aggregate`、`filter`、`select_columns`、`rename`、`drop_duplicates`、`concat`（多表纵向合并）、`split_by_column`（按列值拆多 tab）。
- **技术要点**：lookup 需键标准化 + 重复键检测（多对多拦截）+ 匹配率报告。
- **验收**：用「仓储+运费合并账单」的匹配/汇总/合并需求逐项验证。
- **依赖**：T1.4、T1.5。

### T4.2 模糊匹配兜底
- **要求**：标准化后仍未匹配的行走 rapidfuzz（阈值可配）+ `score` 列；仅对未匹配行执行。
- **验收**：键有细微差异能匹配并标注得分。
- **依赖**：T4.1。

### T4.3 多 tab 输出 + 批量处理
- **要求**：`write` 支持一次多 sheet；批量上传多文件批量跑（复用任务队列）。
- **验收**：一次输出多 tab；批量处理正确。
- **依赖**：T1.5、T3.4。

### T4.4 报表导出
- **要求**：导出 .xlsx（openpyxl 保格式）+ .csv；前端下载（file-saver）。
- **验收**：下载结果与预览一致。
- **依赖**：T3.6、T4.3。

---

## 6. M1 验收样例（真实业务）

用「**仓储+运费合并账单**」作为 M4 完成后的集成验收用例，覆盖：多表 read、字段标准化、ERP单号匹配取主运单号/店铺、按订单号 groupby 汇总、店铺→归属匹配（匹配不到填「新食研」）、多表 concat、多 tab 输出。详细规则以需求原文为准。

---

## 7. 交付顺序建议

```
T0.1 → T0.2 → T0.3 → T0.4        （骨架，可并行）
   ↓
T1.1 → T1.2 → T1.3 → T1.4 → T1.5 → T1.6   （M1 引擎，先 CLI 跑通）
   ↓
T2.1 → T2.2 → T2.3 → T2.4 → T2.5           （M2 知识层 + Agent）
   ↓
T3.1 … T3.8                                 （M3 Web 化，可与 M2 部分并行）
   ↓
T4.1 → T4.2 → T4.3 → T4.4                   （M4 扩展）
```
