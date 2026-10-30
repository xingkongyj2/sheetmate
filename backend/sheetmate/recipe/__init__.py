"""Recipe 层：沿用 SheetAgent 的核心逻辑，执行换成 SheetMate 的 DuckDB 引擎闭环。

- schema.py     Recipe 结构（SheetAgent 字段 + SheetMate 的公式/步骤/断言/字段映射）
- differ.py     输入 vs 输出的结构化 diff（工作表 → 列 → 行 → 值，四层）
- executor.py   按 Recipe 逐步执行（每步一个公式），支持单步运行、断点续跑、出错修复回调
- validator.py  每次运行的自动校验 + Recipe 自带的校验规则
- verifier.py   用训练输入重跑 Recipe，与人工结果比对（信任闸门）
- learner.py    LLM 根据 diff + 处理说明 + 公式目录生成 / 修订 Recipe
- pipeline.py   学习 → 执行 → 修复 → 校验 → 比对 → 保存 的完整闭环
"""
