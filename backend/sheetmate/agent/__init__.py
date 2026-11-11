"""Agent 四大组件：framework、llm、memory、tools。

framework/planning 在共享 ReAct 执行器上编排计划；LLM、工具宿主和检索评分器均可注入。
本包不加载服务实现或数据库，具体模块按需导入。
"""
