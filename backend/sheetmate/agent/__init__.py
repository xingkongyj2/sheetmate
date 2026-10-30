"""Agent 层：沿用 TabClaw 的框架。

- llm.py          OpenAI 兼容的异步客户端（默认 DeepSeek），流式 + 工具调用 + JSON 输出
- prompts.py      所有提示词（中文）
- tools.py        工具注册表：只读分析工具、公式工具、文件工具、Recipe 工具、记忆工具、澄清工具
- executor.py     ReAct 工具循环（直接执行 / 计划执行 + 自检）
- multi_agent.py  多表并行：每张表一个专员 Agent，最后汇总
- planner.py      澄清判断 + 生成计划
- memory.py       记忆抽取与筛选
- recorder.py     工作流记录（每次对话的工具调用序列）
- repair.py       内层 ReAct：Recipe 某一步出错时自动修复
- summarizer.py   会话标题、历史压缩、客户「整体处理逻辑」总结
"""
