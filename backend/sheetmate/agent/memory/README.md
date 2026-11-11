# Agent 记忆

`short_term/` 维护当前执行所需的历史窗口、压缩摘要、任务原文、工具上下文和可恢复状态。
`long_term/` 维护跨轮次知识的抽取、原文证据校验、提示词格式与召回策略。
这些模块不直接操作 SQLAlchemy、文件数据库或 HTTP 服务。

长期记忆的边界：

1. 应用服务先调用 `services/memory/repository.py` 按用户、客户、会话和流程过滤候选。
2. `long_term/retrieval/selector.py` 统一处理人工规则、精确去重、已验证经验、MMR 和上下文预算。
3. `long_term/retrieval/protocol.py` 定义评分接口，`lexical.py` 是当前默认词项实现。
4. `long_term/formatting.py` 只投影获选记忆，并保留来源、作用域和版本信息。

以后接入向量检索，在 `long_term/retrieval/` 中实现 `MemoryScorer`，并在应用装配时调用
`provider.set_memory_scorer(adapter)`；单次调用也可通过 `retrieve_memories(..., scorer=adapter)` 注入。
评分器只能对已授权候选评分，不能从其他作用域追加记录。人工规则和预算仍由 selector 保证。
当前没有向量索引或 embedding 服务，默认实现无需模型或网络连接。

数据库写入、并发版本校验、字段映射和持久化回执分别封装在 `services/memory/` 的
`repository.py`、`field_mappings.py`、`serialization.py`。表格原件画像的存储适配器在
`services/memory/table_profiles.py`，表格内存状态实现在 `engine/tables/memory.py`。
