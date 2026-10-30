"""业务服务层：客户、会话、文件、Recipe、运行记录、记忆、对话编排。

约定：服务函数接收 SQLAlchemy Session（由接口层或 session_scope 提供），不自己提交事务；
供 Agent 工具调用的宿主（services/host.py）在每个操作里开短事务，保证线程安全。
"""
