"""调用 AI、运行 AI 任务的上限：全部来自环境变量（见 .env.example），默认值都放得很大，保证长任务能一直跑完。

这里只放「AI 调用等多久、AI 任务跑几轮、重试几次」这类上限；脚本沙箱、文件解析等本地处理的限制、
以及提示词里的展示行数、字符数等上下文裁剪不在此处，调大后者会撑爆模型的上下文窗口。修改环境变量后需要重启后端。

每个字段上方的注释说明它的含义；对应的环境变量名写在 _env_* 的第一个参数里。
"""

from __future__ import annotations

from sheetmate.core.config.settings import _env_float, _env_int

# ---- 辅助模型调用 ----

# 计划起草 / 计划审查 / 步骤核对 / 完成核对 / 交付核对 / 进度审视这些辅助调用的单次等待下限（秒）。
# 实际等待还会取不小于模型请求超时，这里不会成为瓶颈。
AUX_CALL_TIMEOUT = _env_float("AUX_CALL_TIMEOUT_SECONDS", 1800)

# 自动生成对话标题最多等多久（秒）；超时就用默认标题，不影响对话。
TITLE_TIMEOUT = _env_float("TITLE_TIMEOUT_SECONDS", 600)

# 后台提取长期记忆最多等多久（秒）；超时就跳过这一轮提取，不影响对话。
MEMORY_EXTRACT_TIMEOUT = _env_float("MEMORY_EXTRACT_TIMEOUT_SECONDS", 1800)

# ---- Agent 循环：补做、修复、换方法 ----

# 计划里某一步核对不通过时，最多补做几次。
MAX_STEP_FIXES = _env_int("MAX_STEP_FIXES", 10)

# 计划执行完仍有未完成项时，最多修复几轮。
MAX_PLAN_REPAIRS = _env_int("MAX_PLAN_REPAIRS", 10)

# 模型过早收尾（任务其实没完成）时，最多催促它继续几次。
MAX_COMPLETION_RETRIES = _env_int("MAX_COMPLETION_RETRIES", 50)

# 同一种解决方法最多允许失败几次，之后强制换方法。
METHOD_FAILURE_LIMIT = _env_int("METHOD_FAILURE_LIMIT", 20)

# 一个任务最多尝试几种不同的方法。
MAX_METHODS = _env_int("MAX_METHODS", 30)

# 每次运行 Python 脚本后，额外追加的推理轮数（在 AGENT_MAX_ITERATIONS 之外）。
SCRIPT_EXTRA_ITERATIONS = _env_int("SCRIPT_EXTRA_ITERATIONS", 300)

# 一次任务里工具调用的总次数上限。
TOOL_CALL_BUDGET = _env_int("TOOL_CALL_BUDGET", 5000)

# ---- AI 生成公式 ----

# AI 生成公式失败后，最多重新生成几次。
FORMULA_MAX_ATTEMPTS = _env_int("FORMULA_MAX_ATTEMPTS", 10)
