"""改输入 / 输出文件名时，把整条流程里引用这个名字的地方一起改掉，并核对没有遗漏。

名字出现的地方很多：脚本代码、SQL、步骤参数里的文字、步骤名称和说明、流程说明、输出声明、校验规则、检查点、
输入的说明和来源文件名。只改声明、不改脚本，运行时就会对不上；改完又不核对，遗漏的引用要等到下次运行才暴露。

这里做两件事：
- replace_filename：把旧文件名换成新文件名，一处不漏；只换「完整的文件名」，不碰比它更长的名字里的一部分。
- find_filename：列出还引用某个文件名的地方，改完用它核对。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sheetmate.engine.scripts.store import resolve_script_code, script_params
from sheetmate.recipe.definition.schema import Recipe

# 文件名两侧不能紧挨着别的「名字字符」，否则它只是更长名字的一部分（改「订单.xlsx」不能误伤「汇总订单.xlsx」）。
_NAME_CHAR = r"[\w一-鿿（）()+\-·]"


_EXTENSION = re.compile(r"\.(xlsx|xlsm|xls|csv)$", re.IGNORECASE)


def _pattern(name: str, *, strict: bool = True) -> re.Pattern[str]:
    """strict：改动用，避免误伤别的文件名；宽松：核对用，只要出现旧名字原文就算（宁可多报也不能漏）。

    带扩展名的文件名（订单表.xlsx）：扩展名就是明确的结尾，右边不用检查；只看左边是不是更长名字的一部分
    （改「订单.xlsx」不能误伤「汇总订单.xlsx」）。右边紧跟全角括号这类句子里的标点是常态，不能当成名字的延续。
    不带扩展名的名字（表名风格的文件名）两边都要检查。"""
    if not strict:
        return re.compile(re.escape(name))
    right = "" if _EXTENSION.search(name) else rf"(?!{_NAME_CHAR})"
    return re.compile(rf"(?<!{_NAME_CHAR}){re.escape(name)}{right}")


@dataclass
class Hit:
    where: str   # 例如「第 3 步脚本」「流程说明」
    count: int


def _walk(value: Any, pattern: re.Pattern[str], replacement: str | None, mask: str = "") -> tuple[Any, int]:
    """递归处理 JSON 结构里所有字符串；replacement 为 None 时只统计、不改。

    mask：只统计时，先把这个名字（新名字）整体抹掉再数——「订单表.xlsx」改成「销售订单表.xlsx」后，
    新名字本身就包含旧名字，不抹掉会把改对了的地方也算成遗漏。"""
    if isinstance(value, str):
        if replacement is None:
            return value, len(pattern.findall(value.replace(mask, "\0") if mask else value))
        found = len(pattern.findall(value))
        return (pattern.sub(lambda _m: replacement, value) if found else value), found
    if isinstance(value, list):
        items = [_walk(item, pattern, replacement, mask) for item in value]
        return [item for item, _ in items], sum(count for _, count in items)
    if isinstance(value, dict):
        # 键也可能是文件名（如 file_tables 的键是表名而不是文件名，不动；只处理值）
        items = {key: _walk(item, pattern, replacement, mask) for key, item in value.items()}
        return {key: item for key, (item, _) in items.items()}, sum(count for _, count in items.values())
    return value, 0


def _scan(recipe: Recipe, name: str, replacement: str | None, *, skip_steps: frozenset[int] = frozenset(), strict: bool = True,
          mask: str = "", on_script: Callable[[str | None, str | None], None] | None = None) -> list[Hit]:
    """遍历流程的所有位置；replacement 不为 None 时就地替换。"""
    pattern = _pattern(name, strict=strict)
    hits: list[Hit] = []

    def note(where: str, count: int) -> None:
        if count:
            hits.append(Hit(where, count))

    def text(value: str, where: str) -> str:
        new, count = _walk(value, pattern, replacement, mask)
        note(where, count)
        return new

    recipe.name = text(recipe.name, "流程名")
    recipe.description = text(recipe.description, "流程说明")
    for inp in recipe.inputs:
        inp.description = text(inp.description, f"输入「{inp.alias}」的说明")
    for step in recipe.steps:
        label = f"第 {step.id} 步"
        step.name = text(step.name, f"{label}名称")
        step.description = text(step.description, f"{label}说明")
        step.explanation = text(step.explanation, f"{label}推断依据")
        if step.id in skip_steps:
            continue
        if step.kind == "python":
            code = resolve_script_code(step.params)
            new_code, count = _walk(code, pattern, replacement, mask)
            note(f"{label}脚本", count)
            params = {key: value for key, value in step.params.items() if key not in {"code", "script_id", "script_title", "script_filename"}}
            new_params, param_count = _walk(params, pattern, replacement, mask)
            note(f"{label}参数", param_count)
            if replacement is not None and (count or param_count):
                before = step.params.get("script_id")
                step.params = script_params({**step.params, **new_params, "code": new_code})
                if on_script is not None:
                    on_script(before, step.params.get("script_id"))  # 内容变了就是新的脚本版本，调用方要登记（绑定归属）
        else:
            new_params, count = _walk(step.params, pattern, replacement, mask)
            note(f"{label}参数/SQL", count)
            if replacement is not None and count:
                step.params = new_params
            step.sql = text(step.sql, f"{label}SQL记录")
    for sheet in recipe.output_spec:
        sheet.name = text(sheet.name, f"输出「{sheet.name}」的名称")
        sheet.description = text(sheet.description, f"输出「{sheet.name}」的说明")
    for rule in recipe.validation_rules:
        rule.description = text(rule.description, f"校验规则「{rule.id}」的说明")
        new_params, count = _walk(rule.params, pattern, replacement, mask)
        note(f"校验规则「{rule.id}」的参数", count)
        if replacement is not None and count:
            rule.params = new_params
    metadata, count = _walk(recipe.metadata, pattern, replacement, mask)
    if replacement is not None:
        # 流程记录（当初的需求原话、检查点等）：替换时一起改，保证检查点里引用的名字有效；
        # 核对时不报——那是历史记录，也没有工具能让 AI 去改它，报了只会让它反复尝试。
        note("流程记录（需求、检查点等）", count)
        if count:
            recipe.metadata = metadata
    return hits


def replace_filename(recipe: Recipe, old: str, new: str, *, skip_steps: frozenset[int] = frozenset(),
                     on_script: Callable[[str | None, str | None], None] | None = None) -> list[Hit]:
    """把整条流程里所有引用 old 的地方改成 new，返回每处的改动。

    on_script(旧脚本ID, 新脚本ID)：脚本内容变了会产生新的脚本版本，保存流程前必须先给它登记归属，否则保存时会
    因为「没有权限查看这个脚本」失败（cv_71e32132e6 之后加的改名同步一开始漏了这一步）。"""
    return _scan(recipe, old, new, skip_steps=skip_steps, on_script=on_script)


def find_filename(recipe: Recipe, name: str, *, ignore: str = "") -> list[Hit]:
    """列出还出现这个文件名原文的地方（不改动）。用宽松匹配：核对时宁可多报，由调用方判断要不要改。

    ignore：改名之后传新名字，新名字里恰好包含旧名字的部分不算遗漏。"""
    return _scan(recipe.model_copy(deep=True), name, None, strict=False, mask=ignore)


def describe(hits: list[Hit]) -> str:
    return "；".join(f"{hit.where} {hit.count} 处" for hit in hits)
