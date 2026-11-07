"""思考模式里写给用户看的文字的整理：去掉模型偶尔写出来的「第 N 步…」编号标题。

提示词已经要求自然汇报、不写编号标题，这里是兜底：模型仍然写出「## 第 1 步执行情况」这类标题时，
不让它们出现在用户看到的汇报里。

只删标签，不改任何数字、名称和结论，所以经过回答核对的内容不会因此变化：
- 只有标签（可带「执行情况」「小结」之类的词）的行整行去掉；
- 标签后面还有内容的行，只去掉标签，保留内容和列表符号；
- 标签后面不是标点、空白或行尾的不动（「第一步是检查表头」是正常的句子）；
- 代码块里的内容不动；只匹配行首，句子中间提到「第 3 步」不动。
"""

from __future__ import annotations

import re

_NUM = r"[0-9０-９一二三四五六七八九十百零]+"
# 「第 N 步」「第 N/M 步」「步骤 N」；「第 N 步骤」不算标签
_LABEL = rf"(?:第[ \t]*{_NUM}[ \t]*(?:[/／][ \t]*{_NUM}[ \t]*)?步(?!骤)|步骤[ \t]*{_NUM})"
_WORDS = r"(?:的?(?:执行情况|执行结果|执行过程|完成情况|处理情况|处理结果|小结|总结|汇报|进展|情况|结果))?"
_BOLD = r"(?:\*\*|__)"
_PUNCT = r"[:：、，,.。\-—–]"
_PREFIX = re.compile(
    rf"^(?P<lead>[ \t]*(?P<heading>#{{1,6}}[ \t]*)?(?P<quote>>[ \t]*)?(?P<bullet>(?:[-*+][ \t]+|\d+[.、)][ \t]*))?)"
    rf"(?P<open>{_BOLD}[ \t]*)?"
    rf"{_LABEL}{_WORDS}"
    rf"(?:[ \t]*{_BOLD})?"
    rf"(?=[ \t]|{_PUNCT}|[)）]|$)"  # 标签后面必须是空白、标点或行尾，避免吃掉「第一步是…」
    rf"[ \t]*{_PUNCT}*[ \t]*"
)
_TRAILING_BOLD = re.compile(rf"[ \t]*{_BOLD}[ \t]*$")


def _drop_dangling_bold(text: str) -> str:
    """去掉标签时顺带去掉了开头的加粗符号，行尾那个孤零零的收尾符号也要去掉。"""
    for marker in ("**", "__"):
        if text.count(marker) % 2 == 1 and text.rstrip().endswith(marker):
            return text.rstrip()[: -len(marker)].rstrip()
    return text


def strip_step_label(text: str) -> str:
    """单行文字（计划里的步骤描述、开场白）：去掉开头的「第 N 步：」。"""
    value = (text or "").strip()
    match = _PREFIX.match(value)
    if not match:
        return value
    rest = value[match.end():]
    return _drop_dangling_bold(rest).strip() if match.group("open") else rest.strip()


def tidy_report(text: str) -> str:
    """多行汇报：去掉「第 N 步…」编号标题，保留其余内容。"""
    lines: list[str] = []
    fenced = False
    for line in (text or "").splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            lines.append(line)
            continue
        match = None if fenced else _PREFIX.match(line)
        if not match:
            lines.append(line)
            continue
        rest = line[match.end():]
        if match.group("open"):
            rest = _drop_dangling_bold(rest)
        rest = rest.strip()
        if not rest or _TRAILING_BOLD.fullmatch(rest):
            continue  # 只有标签（或只剩加粗符号）：整行去掉
        # 保留引用和列表符号，标题符号去掉（这行现在是正文，不再是标题）
        keep = (match.group("quote") or "") + (match.group("bullet") or "")
        indent = line[: len(line) - len(line.lstrip())]
        lines.append(f"{indent}{keep}{rest}")
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return cleaned


# ---------------------------------------------------------------------------
# 核对结果写给用户看的那一行
# ---------------------------------------------------------------------------

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
# 工具名、SQL、代码标记：不该出现在写给财务同事看的文字里
_TECHNICAL = re.compile(r"`|\b(?:SELECT|FROM|WHERE|GROUP BY|run_sql|run_python_script|table_info|preview_table|column_stats|value_counts|"
                        r"build_recipe|ask_user|call_\w+|tool_call_id)\b", re.IGNORECASE)


def numbers_in(text: str) -> set[str]:
    return {item.replace(",", "") for item in _NUMBER.findall(text or "")}


def grounded(claim: str, evidence: str) -> bool:
    """说法里的每个数字都能在依据里原样找到。没有数字的说法不需要依据。
    用于核对结果里的「具体数字」：依据只能是工具的真实返回，不能是模型自己的汇报，否则编出来的数字也能互相印证。"""
    return numbers_in(claim) <= numbers_in(evidence)


# 核对意见里「第 2 步缺少…」的「第 2 步」只是在指代某一项，标签后面直接接正文。
# 只处理阿拉伯数字写法：「第一步是检查表头」这样的正常句子不动。
_LOOSE_LABEL = re.compile(rf"^\s*(?:第[ \t]*[0-9０-９]+[ \t]*(?:[/／][ \t]*[0-9０-９]+[ \t]*)?步(?!骤)|步骤[ \t]*[0-9０-９]+)[ \t]*{_PUNCT}*[ \t]*")


def public_sentence(text: str, limit: int = 160) -> str:
    """整理成一句话给用户看：去掉换行、结尾标点和「第 N 步」前缀；含工具名 / SQL 等技术内容时返回空串。"""
    value = " ".join((text or "").split())
    value = _LOOSE_LABEL.sub("", strip_step_label(value), count=1).rstrip("。；;，,. ")
    if not value or _TECHNICAL.search(value):
        return ""
    return value if len(value) <= limit else value[: limit - 1] + "…"


def quote(text: str) -> str:
    """整段放进引用块：界面里是带竖线的弱化文字，和正文区分开。"""
    return "\n".join(f"> {line}" if line.strip() else ">" for line in text.splitlines())


def step_check_note(*, passed: bool | None, checked: str = "", issues: list[str] | tuple[str, ...] = (),
                    retry: bool = False, reason: str = "") -> str:
    """一项做完之后的核对结果，一行引用文字。没有可说的内容（例如这一项没有调用工具、不需要核对）时返回空串。

    passed=None 表示该核对而没核对成：写明原因，不能让用户以为核对过了。"""
    if passed is True:
        detail = public_sentence(checked)
        return quote(f"✓ 核对通过：{detail}" if detail else "✓ 核对通过")
    if passed is False:
        shown = [item for item in (public_sentence(text, 120) for text in issues[:3]) if item] or ["结果与预期不符"]
        problems = "；".join(shown)
        return quote(f"核对发现问题：{problems}。正在补充处理。" if retry else f"核对仍有问题：{problems}。已如实标注，请留意。")
    why = public_sentence(reason, 100)
    return quote(f"核对没有完成（{why}）") if why else ""
