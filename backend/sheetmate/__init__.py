"""SheetMate —— 通用 Excel Agent（分析 + 修改 + 可重复执行的 Recipe）。

代码分层（自上而下）：

- api/       HTTP 接口层（FastAPI 路由，SSE 流式返回 Agent 事件）
- services/  业务服务层（客户、会话、文件、Recipe、记忆、对话编排）
- agent/     Agent 层，沿用 TabClaw 的框架：ReAct 工具循环、澄清、多表并行、记忆、工作流记录
- recipe/    Recipe 层，沿用 SheetAgent 的核心逻辑：输入输出 diff、Recipe 结构、执行、校验、比对验证
- engine/    引擎层，即 SheetMate 的引擎闭环：Excel 读取与画像、字段标准化、公式库、DuckDB 执行、写回
- db/        本地存储（SQLite），文件统一放在 data/ 目录（本地优先）
"""

__version__ = "1.0.0"
