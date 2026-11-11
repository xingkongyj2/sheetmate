"""Small, deterministic repair hints for observed technical failures.

The catalog is also used by verified experience storage. It intentionally
contains no customer values, generated code, or model-produced conclusions.
"""

from __future__ import annotations

import re


_ADVICE = {
    "missing_column": "先核对当前输入表的真实列名、表头行和别名，再修正字段引用；不要猜测或静默补造缺失字段。",
    "missing_table": "先核对当前可用表和加载状态，使用真实表名；中间表未生成时先完成对应处理。",
    "date_parse": "先核对日期样例和实际格式，兼容需要的日期表示并识别空值、无效值；不要静默丢弃无法转换的记录。",
    "type_mismatch": "先核对参与计算或关联字段的实际类型、空值和样例，再明确转换规则；保留无法转换的记录以便核对。",
    "merged_cell": "先核对合并单元格范围，写入合并区域左上角的可写单元格，并保留用户要求的表格格式。",
}

_NON_TECHNICAL = re.compile(
    r"尚未执行|本次操作未执行|调用参数未通过|工具参数不是合法|"
    r"permission(?:error| denied)|access denied|unauthori[sz]ed|forbidden|"
    r"无权限|没有权限|权限不足|未获授权|需要用户|等待用户|用户取消|已取消|"
    r"cancelled|canceled|cancellation|预算.{0,12}(?:耗尽|用完|不足)|"
    r"(?:执行轮数|工具调用次数|运行时间).{0,12}(?:上限|限制)|"
    r"stream_idle_timeout|tool_protocol|超时",
    re.IGNORECASE,
)

_PATTERNS = (
    ("missing_column", re.compile(
        r"列(?:「[^」]*」|\s*\[[^\]\n]*\])\s*(?:不在表|不存在)|"
        r"(?:referenced\s+column|column\s+[\"`'][^\n]+?[\"`']).{0,160}(?:not found|does not exist)|"
        r"no such column\s*:|none of\s*\[[^\n]+\].{0,80}(?:columns|index)|"
        r"\[[^\n]+\]\s+not in index",
        re.IGNORECASE,
    )),
    ("missing_table", re.compile(
        r"表「[^」]+」不存在|table with name\s+[^\n]+?\s+does not exist|"
        r"no such table\s*:|relation\s+[\"`'][^\n]+?[\"`']\s+does not exist",
        re.IGNORECASE,
    )),
    ("merged_cell", re.compile(
        r"mergedcell.{0,120}(?:read.only|attribute.{0,40}value)|"
        r"合并单元格.{0,50}(?:只读|不可写|不能写)|"
        r"cannot (?:assign|write).{0,50}merged cell",
        re.IGNORECASE,
    )),
    ("date_parse", re.compile(
        r"dateparseerror|time data\s+[^\n]+?(?:doesn.t|does not) match format|"
        r"unknown datetime string format|out of bounds nanosecond timestamp|"
        r"(?:invalid|could not convert|could not parse).{0,100}(?:date|timestamp)|"
        r"(?:日期|时间).{0,30}(?:解析失败|转换失败|格式不匹配)",
        re.IGNORECASE,
    )),
    ("type_mismatch", re.compile(
        r"conversion error|cannot (?:cast|convert).{0,100}(?:type|int|float|numeric|double)|"
        r"could not convert (?:string|value).{0,100}(?:int|float|numeric|double)|"
        r"(?:typeerror:\s*)?(?:unsupported operand type|can only concatenate str)|"
        r"(?:类型不匹配|数据类型不一致)|(?:字段|列).{0,30}类型.{0,12}(?:不一致|不匹配)",
        re.IGNORECASE,
    )),
)


def classify_failure(tool: str, error: str) -> str | None:
    """Classify only specific repairable data-processing errors.

    Execution and replay checks belong to the caller, which has the actual
    tool receipt. Free-form business failure messages are not repair rules.
    """
    if not tool or not isinstance(error, str) or not error or _NON_TECHNICAL.search(error):
        return None
    for kind, pattern in _PATTERNS:
        if pattern.search(error):
            return kind
    return None


def failure_advice(kind: str) -> str:
    """Return a stable, customer-independent technical hint."""
    return _ADVICE.get(kind, "")


def guidance(tool: str, error: str, experience_text: str = "") -> str:
    """Build a hint for the next existing decision, never a new model call."""
    kind = classify_failure(tool, error)
    if kind is None:
        return ""
    message = (
        f"执行失败后的修复提示（{tool}）：{failure_advice(kind)}"
        "\n结合本次实际错误选择修正方式，保留用户的业务规则；修正后实际执行并根据结果核对，不能仅凭说明认定成功。"
    )
    if experience_text:
        message += "\n以下是可参考的历史修复经验，仍需核对是否适合本次输入：\n" + experience_text
    return message
