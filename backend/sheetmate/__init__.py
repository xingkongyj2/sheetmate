"""SheetMate：按请求流程组织的 Excel Agent 后端。

api → services → agent（framework / llm / memory / tools）。
engine 提供确定性数据处理，recipe 提供可重复工作流，db 管理持久化，core 提供基础能力。
模块职责、扩展接口和依赖约束见 backend/README.md。
"""

__version__ = "1.0.0"
