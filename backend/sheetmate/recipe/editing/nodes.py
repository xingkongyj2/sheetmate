"""流程节点的确定性修改（不调用模型、不读写数据库）。

输入一份 Recipe 和一组修改，返回修改后的新 Recipe、人话变更说明和提醒。支持的节点：

- 流程本身：名称、说明
- 输入：文件名匹配规则、来源文件名、说明文字，以及输入别名（流程图上显示的名字，会同步改所有引用）
- 输出：交付文件名（脚本声明的输出文件）、输出工作表名
- 步骤：名称、说明、启停、参数（含 SQL）
- 脚本：脚本名、脚本内容（整段替换，或精确的查找替换）

脚本源码是不可变版本文件：改动总是生成新版本并改引用，旧版本与历史运行记录保持原样。
修改后的流程必须不比修改前多出结构问题，否则整体拒绝，不会留下半成品。
"""

from __future__ import annotations

import ast
import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sheetmate.engine.formulas import FormulaLibrary
from sheetmate.engine.scripts.store import MAX_SCRIPT_BYTES, normalize_script_title, resolve_script_code, script_params
from sheetmate.recipe.definition.schema import MAX_RECIPE_NAME, REMOVED_OUTPUT_REFS, Recipe, RecipeStep, check_recipe
from sheetmate.recipe.editing.references import describe, find_filename, replace_filename

MAX_EDITS = 30
_FORBIDDEN_NAME_CHARS = frozenset('/\\')
_SHEET_FORBIDDEN = frozenset('[]:*?/\\')
_PYTHON_PARAM_KEYS = {"code", "script_id", "script_title", "script_filename"}


class NodeEditError(ValueError):
    """修改请求本身不合法（找不到节点、字段不对、结果会破坏流程）。"""


@dataclass
class NodeEditResult:
    recipe: Recipe
    changes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    new_script_ids: list[str] = field(default_factory=list)
    needs_rerun: bool = False  # 会改变执行行为或产物时为 True，调用方应提醒用户重跑验证
    unchanged: list[str] = field(default_factory=list)  # 已经是目标值的项；changes 为空时表示无需保存

    @property
    def changed(self) -> bool:
        return bool(self.changes)


# ---------------------------------------------------------------------------
# 参数校验
# ---------------------------------------------------------------------------


def _fields(edit: dict[str, Any], required: set[str], optional: set[str]) -> None:
    op = edit.get("op")
    unknown = set(edit) - {"op"} - required - optional
    if unknown:
        raise NodeEditError(f"{op} 不支持字段 {sorted(unknown)}；可用字段：{sorted(required | optional)}。")
    missing = [key for key in sorted(required) if edit.get(key) in (None, "")]
    if missing:
        raise NodeEditError(f"{op} 缺少必填字段 {missing}。")


def _text(edit: dict[str, Any], key: str, *, limit: int = 120, allow_empty: bool = False) -> str | None:
    if key not in edit:
        return None
    value = edit[key]
    if not isinstance(value, str):
        raise NodeEditError(f"{key} 必须是文字。")
    text = value.strip()
    if not text and not allow_empty:
        raise NodeEditError(f"{key} 不能为空。")
    if len(text) > limit:
        raise NodeEditError(f"{key} 不能超过 {limit} 个字符。")
    return text


def _filename(value: str, label: str) -> str:
    if value in {".", ".."} or any(char in _FORBIDDEN_NAME_CHARS for char in value):
        raise NodeEditError(f"{label}「{value}」必须是单纯的文件名，不能含路径分隔符。")
    return value


def _step(recipe: Recipe, step_id: Any) -> RecipeStep:
    if isinstance(step_id, bool) or not isinstance(step_id, int):
        raise NodeEditError("step_id 必须是流程中真实的步骤编号（整数）。")
    try:
        return recipe.step(step_id)
    except KeyError:
        ids = "、".join(str(step.id) for step in recipe.steps)
        raise NodeEditError(f"流程没有第 {step_id} 步；现有步骤编号：{ids or '无'}。") from None


def _python_step(recipe: Recipe, step_id: Any) -> RecipeStep:
    step = _step(recipe, step_id)
    if step.kind != "python":
        raise NodeEditError(f"第 {step.id} 步「{step.name}」不是 Python 脚本步骤，没有脚本可改。")
    return step


class _Collector:
    def __init__(self, library: FormulaLibrary) -> None:
        self.library = library
        self.changes: list[str] = []
        self.warnings: list[str] = []
        self.unchanged: list[str] = []  # 目标值已经生效的项，全部如此时据此说明原因
        self.new_script_ids: list[str] = []
        self.needs_rerun = False

    def script_updated(self, before: str | None, after: str | None) -> None:
        if after and after != before and after not in self.new_script_ids:
            self.new_script_ids.append(after)


# ---------------------------------------------------------------------------
# 各节点的修改
# ---------------------------------------------------------------------------


def _update_workflow(recipe: Recipe, edit: dict[str, Any], out: _Collector) -> None:
    _fields(edit, set(), {"name", "description"})
    name, description = _text(edit, "name", limit=MAX_RECIPE_NAME), _text(edit, "description", limit=2000, allow_empty=True)
    if name is None and description is None:
        raise NodeEditError("update_workflow 至少要修改 name 或 description。")
    if name is not None and name != recipe.name:
        out.changes.append(f"流程名：「{recipe.name}」→「{name}」")
        recipe.name = name
    elif name is not None:
        out.unchanged.append(f"流程名已经是「{name}」")
    if description is not None and description != recipe.description:
        out.changes.append("流程说明已更新")
        recipe.description = description
    elif description is not None:
        out.unchanged.append("流程说明已经是目标内容")


def _rename_input_file(recipe: Recipe, edit: dict[str, Any], out: _Collector) -> None:
    _fields(edit, {"alias"}, {"file_pattern", "source_file", "description"})
    alias = str(edit["alias"])
    inp = next((item for item in recipe.inputs if item.alias == alias), None)
    if inp is None:
        names = "、".join(item.alias for item in recipe.inputs)
        raise NodeEditError(f"流程没有输入「{alias}」；现有输入：{names or '无'}。")
    pattern, source = _text(edit, "file_pattern", limit=200), _text(edit, "source_file", limit=255)
    description = _text(edit, "description", limit=500)
    if pattern is None and source is None and description is None:
        raise NodeEditError("rename_input_file 至少要修改 file_pattern、source_file 或 description。")
    if pattern is not None:
        _filename(pattern, "file_pattern")
        if pattern != inp.file_pattern:
            out.changes.append(f"输入「{alias}」文件规则：{inp.file_pattern or '无'} → {pattern}")
            inp.file_pattern = pattern
        else:
            out.unchanged.append(f"输入「{alias}」文件规则已经是 {pattern}")
    if source is not None:
        _filename(source, "source_file")
        if source != inp.source_file:
            out.changes.append(f"输入「{alias}」来源文件名：{inp.source_file or '无'} → {source}")
            previous, inp.source_file = inp.source_file, source
            listed = recipe.learned_from.get("input_files")
            if isinstance(listed, list):
                recipe.learned_from["input_files"] = [source if item == previous else item for item in listed]
            if previous and previous != source:
                # 旧文件名还可能出现在脚本、SQL、说明里：整条流程一起改，再扫一遍确认没有遗漏
                hits = replace_filename(recipe, previous, source, on_script=out.script_updated)
                if hits:
                    out.changes.append("已同步引用：" + describe(hits))
                leftovers = find_filename(recipe, previous, ignore=source)
                if leftovers:
                    out.warnings.append(f"改完后仍有地方出现「{previous}」：{describe(leftovers)}。请检查是否还需要修改。")
            out.warnings.append(f"输入「{alias}」的来源文件名只是记录；运行时按文件规则 {inp.file_pattern or '（空）'} 匹配新文件。")
        else:
            out.unchanged.append(f"输入「{alias}」来源文件名已经是 {source}")
    if description is not None and description != inp.description:
        out.changes.append(f"输入「{alias}」说明已更新")
        inp.description = description
    elif description is not None:
        out.unchanged.append(f"输入「{alias}」说明已经是目标内容")


def _replace_name(value: Any, old: str, new: str) -> tuple[Any, int]:
    """把等于 old 的文字（或列表里的元素）换成 new，返回新值和替换个数。"""
    if value == old:
        return new, 1
    if isinstance(value, list):
        replaced = [new if item == old else item for item in value]
        return replaced, sum(1 for item in value if item == old)
    return value, 0


def _rename_input_alias(recipe: Recipe, edit: dict[str, Any], out: _Collector) -> None:
    """改输入别名（流程图里输入和步骤上显示的名字），并同步所有引用它的地方。"""
    _fields(edit, {"alias", "new_alias"}, set())
    old = str(edit["alias"])
    new = _text(edit, "new_alias", limit=100) or ""
    inp = next((item for item in recipe.inputs if item.alias == old), None)
    if inp is None:
        names = "、".join(item.alias for item in recipe.inputs)
        raise NodeEditError(f"流程没有输入「{old}」；现有输入：{names or '无'}。")
    if new == old:
        out.unchanged.append(f"输入别名已经是「{new}」")
        return
    if any(char in new for char in '"/\\') or new != edit["new_alias"].strip():
        raise NodeEditError("new_alias 不能含引号、路径分隔符或首尾空格。")
    taken = {item.alias for item in recipe.inputs} | {step.output for step in recipe.steps}
    if new in taken:
        raise NodeEditError(f"「{new}」已被其他输入或步骤结果表使用，请换一个名字。")
    inp.alias = new
    bindings = recipe.learned_from.get("input_bindings")
    if isinstance(bindings, dict) and old in bindings:
        recipe.learned_from["input_bindings"] = {(new if key == old else key): value for key, value in bindings.items()}
    pattern = re.compile(r"(?<!\w)" + re.escape(old) + r"(?!\w)")
    references = 0
    for step in recipe.steps:
        params = step.params
        if step.kind == "python":
            if "input_tables" in params:
                params["input_tables"], count = _replace_name(params["input_tables"], old, new)
                references += count
            bindings_map = params.get("table_bindings")
            for alias_key, target in (bindings_map.items() if isinstance(bindings_map, dict) else []):
                if target == old:
                    bindings_map[alias_key] = new
                    references += 1
            files = params.get("input_files")
            for alias_key, target in (files.items() if isinstance(files, dict) else []):
                if target == old:
                    files[alias_key] = new
                    references += 1
            if "workbook_export" not in params:
                # 脚本若直接用别名取表（没有 table_bindings 间接引用），代码里的名字也要同步。
                code = resolve_script_code(params)
                replaced, count = pattern.subn(new, code)
                if count:
                    before = params.get("script_id")
                    step.params = script_params({**params, "code": replaced})
                    out.script_updated(before, step.params.get("script_id"))
                    out.changes.append(f"第 {step.id} 步脚本里 {count} 处「{old}」已同步改为「{new}」")
            continue
        if step.formula == "custom_sql" and isinstance(params.get("sql"), str):
            params["sql"], count = pattern.subn(new, params["sql"])
            references += count
            continue
        try:
            spec = out.library.resolve(step.formula).spec
        except ValueError:
            continue
        for name, param in spec.params.items():
            if param.type in {"table", "tables"} and name in params:
                params[name], count = _replace_name(params[name], old, new)
                references += count
    for sheet in recipe.output_spec:
        if sheet.source == old:
            sheet.source = new
            references += 1
    out.changes.append(f"输入别名：「{old}」→「{new}」（同步修改了 {references} 处引用）")
    out.needs_rerun = True


def _input_users(recipe: Recipe, alias: str, library: FormulaLibrary) -> list[str]:
    """哪些步骤/输出在用这个输入（与 rename_input_alias 同步的位置一致）。"""
    pattern = re.compile(r"(?<!\w)" + re.escape(alias) + r"(?!\w)")
    users: list[str] = []
    for step in recipe.steps:
        params, used = step.params, False
        if step.kind == "python":
            tables = params.get("input_tables")
            bindings, files = params.get("table_bindings"), params.get("input_files")
            used = (isinstance(tables, list) and alias in tables
                    or isinstance(bindings, dict) and alias in bindings.values()
                    or isinstance(files, dict) and alias in files.values())
            if not used and "workbook_export" not in params:
                try:
                    used = bool(pattern.search(resolve_script_code(params)))
                except (OSError, ValueError):
                    used = False
        elif step.formula == "custom_sql":
            used = bool(pattern.search(str(params.get("sql", ""))))
        else:
            try:
                spec = library.resolve(step.formula).spec
            except ValueError:
                spec = None
            for name, param in (spec.params.items() if spec else []):
                value = params.get(name)
                if param.type in {"table", "tables"} and (value == alias or isinstance(value, list) and alias in value):
                    used = True
        if used:
            users.append(f"第 {step.id} 步「{step.name}」")
    users += [f"输出工作表「{sheet.name}」" for sheet in recipe.output_spec if sheet.source == alias]
    return users


def _remove_input(recipe: Recipe, edit: dict[str, Any], out: _Collector) -> None:
    """把输入从流程里移除；原文件和历史记录不动。只能移除没有步骤在用的输入。"""
    _fields(edit, {"alias"}, set())
    alias = str(edit["alias"])
    inp = next((item for item in recipe.inputs if item.alias == alias), None)
    if inp is None:
        names = "、".join(item.alias for item in recipe.inputs)
        raise NodeEditError(f"流程没有输入「{alias}」；现有输入：{names or '无'}。")
    users = _input_users(recipe, alias, out.library)
    if users:
        raise NodeEditError(f"输入「{inp.source_file or alias}」正被{'、'.join(users)}使用，直接移除会让流程运行失败。"
                            "需要连同脚本/步骤一起调整时，请用 revise_recipe 的 input_changes（operation=remove）移除，它会适配步骤并完整重跑。")
    recipe.inputs = [item for item in recipe.inputs if item.alias != alias]
    bindings = recipe.learned_from.get("input_bindings")
    if isinstance(bindings, dict):
        bindings.pop(alias, None)
    listed = recipe.learned_from.get("input_files")
    if isinstance(listed, list) and inp.source_file and all(item.source_file != inp.source_file for item in recipe.inputs):
        recipe.learned_from["input_files"] = [item for item in listed if item != inp.source_file]
    out.changes.append(f"已从流程移除输入「{inp.source_file or alias}」（原文件保留）")
    out.needs_rerun = True


def _remove_output(recipe: Recipe, edit: dict[str, Any], out: _Collector) -> None:
    """把一个输出文件从流程交付里移除；已生成的历史文件不动。"""
    _fields(edit, {"name"}, {"ref"})
    name = str(edit["name"]).strip()
    files = recipe.flow()["output_files"]
    matches = [item for item in files if item["name"] == name and (not edit.get("ref") or item["ref"] == edit["ref"])]
    if not matches:
        names = "、".join(item["name"] for item in files)
        raise NodeEditError(f"流程没有输出文件「{name}」；现有输出文件：{names or '无'}。")
    if len(matches) > 1:
        raise NodeEditError(f"有多个输出文件叫「{name}」，请用 ref 指定：{[item['ref'] for item in matches]}。")
    if len(files) == 1:
        raise NodeEditError(f"「{name}」是流程唯一的输出文件，不能全部移除；没有输出的流程无法交付结果。")
    target = matches[0]
    if not target["ref"]:
        # 系统按流程名导出的工作簿：清空输出工作表声明，并明确不再兜底输出最后一步。
        recipe.output_spec = []
        recipe.metadata["explicit_outputs"] = True
        for step in recipe.steps:
            if isinstance(step.params.get("workbook_export"), dict) and step is recipe.ordered_steps()[-1]:
                raise NodeEditError(f"「{name}」由最后一步的工作簿导出生成，请改用 update_step 调整或停用那一步。")
    else:
        removed = list(recipe.metadata.get(REMOVED_OUTPUT_REFS) or [])
        recipe.metadata[REMOVED_OUTPUT_REFS] = [*removed, target["ref"]] if target["ref"] not in removed else removed
        for step in recipe.steps:
            declared = step.params.get("output_files")
            if isinstance(declared, list):
                step.params["output_files"] = [item for item in declared
                                               if not (isinstance(item, dict) and item.get("ref") == target["ref"])]
    out.changes.append(f"已从流程移除输出文件「{name}」（已生成的历史文件保留）")
    out.needs_rerun = True


def _with_suffix(old: str, new: str) -> str:
    """新文件名没写扩展名就沿用原来的；写了不同的扩展名则拒绝（改扩展名会让文件打不开）。"""
    old_suffix, new_suffix = Path(old).suffix, Path(new).suffix
    if old_suffix and new_suffix.lower() != old_suffix.lower():
        if new_suffix.lower() in {".xlsx", ".xlsm", ".xls", ".csv"}:
            raise NodeEditError(f"不能修改输出文件的扩展名：「{old}」只能保持 {old_suffix}。")
        return new + old_suffix
    return new


def declared_files(recipe: Recipe) -> list[dict[str, Any]]:
    """脚本步骤声明的所有文件（最终交付的和供后续步骤读取的中间文件）。"""
    files: dict[str, dict[str, Any]] = {}
    for step in recipe.steps:
        if step.kind != "python":
            continue
        declared = step.params.get("output_files")
        for item in declared if isinstance(declared, list) else []:
            if isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("ref"), str):
                files.setdefault(item["ref"], {"name": item["name"], "ref": item["ref"], "step": step})
        export = step.params.get("workbook_export")
        if isinstance(export, dict) and isinstance(export.get("filename"), str):
            ref = f"{step.output}/{export['filename']}"
            files.setdefault(ref, {"name": export["filename"], "ref": ref, "step": step})
    return list(files.values())


def _rename_output_file(recipe: Recipe, edit: dict[str, Any], out: _Collector) -> None:
    _fields(edit, {"name", "new_name"}, {"ref"})
    old = str(edit["name"]).strip()
    files = declared_files(recipe)
    matches = [item for item in files if item["name"] == old and (not edit.get("ref") or item["ref"] == edit["ref"])]
    if not matches:
        if any(item["name"] == old and not item["ref"] for item in recipe.flow()["output_files"]):
            raise NodeEditError(f"「{old}」是系统按流程名自动生成的导出文件（流程名_结果.xlsx），请用 update_workflow 修改流程名。")
        names = "、".join(item["name"] for item in files)
        raise NodeEditError(f"流程没有脚本生成的文件「{old}」；现有文件：{names or '无'}。")
    if len(matches) > 1:
        raise NodeEditError(f"有多个文件叫「{old}」，请用 ref 指定：{[item['ref'] for item in matches]}。")
    target = matches[0]
    new = _with_suffix(old, _filename(_text(edit, "new_name", limit=120) or "", "new_name"))
    if new == old:
        raise NodeEditError("新文件名与原文件名相同。")
    if any(item["name"] == new and item["step"] is target["step"] for item in files):
        raise NodeEditError(f"这一步已有文件叫「{new}」，请换一个名字。")
    namespace = target["ref"].split("/", 1)[0]
    old_ref, new_ref = target["ref"], f"{namespace}/{new}"
    producer = target["step"]
    touched = False
    declared = producer.params.get("output_files")
    if isinstance(declared, list):
        for item in declared:
            if isinstance(item, dict) and item.get("name") == old:
                item["name"], item["ref"] = new, new_ref
                touched = True
    export = producer.params.get("workbook_export")
    if isinstance(export, dict) and export.get("filename") == old:
        export["filename"] = new
        touched = True
    # 后续步骤可能读取这个文件：文件表来源和脚本输入文件都按旧引用查找。
    for step in recipe.steps:
        file_tables = step.params.get("file_tables")
        for spec in file_tables.values() if isinstance(file_tables, dict) else []:
            if isinstance(spec, dict) and spec.get("ref") == old_ref:
                spec["ref"] = new_ref
                touched = True
        input_files = step.params.get("input_files")
        if isinstance(input_files, dict):
            for alias, ref in input_files.items():
                if ref == old_ref:
                    input_files[alias] = new_ref
                    touched = True
    if not touched:
        raise NodeEditError(f"在流程里没有找到「{old}」的声明，无法改名。")
    out.changes.append(f"输出文件名：「{old}」→「{new}」")
    # 只是改名：把整条流程里引用旧文件名的地方（所有步骤的脚本、SQL、参数、说明，流程说明，校验规则等）一起改掉，
    # 然后再扫一遍确认没有遗漏。处理逻辑没变，不需要重跑；新名字在下一次运行生成文件时生效。
    hits = replace_filename(recipe, old, new, on_script=out.script_updated)
    if hits:
        out.changes.append("已同步引用：" + describe(hits))
    leftovers = find_filename(recipe, old, ignore=new)
    if leftovers:
        out.warnings.append(f"改完后仍有地方出现「{old}」：{describe(leftovers)}。请检查这些位置是否还需要修改（可用 edit_script / update_step）。")
    elif "workbook_export" not in producer.params and not any(h.where == f"第 {producer.id} 步脚本" for h in hits):
        out.warnings.append(f"第 {producer.id} 步脚本代码里没有出现「{old}」原文，请用 edit_script 确认脚本实际写出的文件名已改成「{new}」，"
                            "否则下次运行会报“没有生成声明的输出文件”。")


def _rename_output_sheet(recipe: Recipe, edit: dict[str, Any], out: _Collector) -> None:
    _fields(edit, {"name", "new_name"}, set())
    old, new = str(edit["name"]).strip(), _text(edit, "new_name", limit=31) or ""
    if any(char in _SHEET_FORBIDDEN for char in new):
        raise NodeEditError("工作表名不能包含 [ ] : * ? / \\ 这些字符。")
    if new == old:
        raise NodeEditError("新工作表名与原名称相同。")
    sheets = [sheet for sheet in recipe.output_spec if sheet.name == old]
    exports = [spec for step in recipe.steps if isinstance(step.params.get("workbook_export"), dict)
               for spec in step.params["workbook_export"].get("output_spec", [])
               if isinstance(spec, dict) and spec.get("name") == old]
    if not sheets and not exports:
        names = "、".join(sheet.name for sheet in recipe.output_spec)
        raise NodeEditError(f"流程没有输出工作表「{old}」；现有输出工作表：{names or '无'}。")
    if any(sheet.name == new for sheet in recipe.output_spec):
        raise NodeEditError(f"已有输出工作表叫「{new}」，请换一个名字。")
    for sheet in sheets:
        sheet.name = new
    for spec in exports:
        spec["name"] = new
    out.changes.append(f"输出工作表名：「{old}」→「{new}」")
    out.needs_rerun = True
    if any(step.kind == "python" and "workbook_export" not in step.params for step in recipe.steps):
        out.warnings.append("脚本自己写出的工作簿里的 sheet 名由脚本代码决定，需要的话请再用 edit_script 修改。")


def _update_step(recipe: Recipe, edit: dict[str, Any], out: _Collector) -> None:
    _fields(edit, {"step_id"}, {"name", "description", "explanation", "enabled", "params"})
    step = _step(recipe, edit["step_id"])
    name = _text(edit, "name", limit=60)
    description = _text(edit, "description", limit=1000, allow_empty=True)
    explanation = _text(edit, "explanation", limit=2000, allow_empty=True)
    if not any(key in edit for key in ("name", "description", "explanation", "enabled", "params")):
        raise NodeEditError("update_step 至少要修改一项：name、description、explanation、enabled 或 params。")
    label = f"第 {step.id} 步"
    if name is not None and name != step.name:
        out.changes.append(f"{label}名称：「{step.name}」→「{name}」")
        step.name = name
    if description is not None and description != step.description:
        out.changes.append(f"{label}说明已更新")
        step.description = description
    if explanation is not None and explanation != step.explanation:
        out.changes.append(f"{label}推断依据已更新")
        step.explanation = explanation
    if "enabled" in edit:
        if not isinstance(edit["enabled"], bool):
            raise NodeEditError("enabled 必须是 true 或 false。")
        if edit["enabled"] != step.enabled:
            out.changes.append(f"{label}已{'启用' if edit['enabled'] else '停用'}")
            step.enabled = edit["enabled"]
            out.needs_rerun = True
    if "params" in edit:
        params = edit["params"]
        if not isinstance(params, dict) or not params:
            raise NodeEditError("params 必须是非空对象；值为 null 表示删除该参数。")
        if step.kind == "python" and (blocked := sorted(_PYTHON_PARAM_KEYS & set(params))):
            raise NodeEditError(f"脚本相关参数 {blocked} 不能通过 params 修改；脚本名用 rename_script，脚本内容用 edit_script。")
        for key, value in params.items():
            if value is None:
                if step.params.pop(key, None) is not None:
                    out.changes.append(f"{label}参数「{key}」已删除")
            elif step.params.get(key) != value:
                out.changes.append(f"{label}参数「{key}」已修改")
                step.params[key] = value
        out.needs_rerun = True


def _rename_script(recipe: Recipe, edit: dict[str, Any], out: _Collector) -> None:
    _fields(edit, {"step_id", "title"}, set())
    step = _python_step(recipe, edit["step_id"])
    title = normalize_script_title(str(edit["title"]))
    old = step.params.get("script_title") or ""
    if title == old:
        raise NodeEditError(f"第 {step.id} 步脚本名已经是「{title}」。")
    before = step.params.get("script_id")
    # 标题只随新版本保存，所以连同原代码一起生成新的脚本版本。
    step.params = script_params({**step.params, "code": resolve_script_code(step.params), "script_title": title})
    out.script_updated(before, step.params.get("script_id"))
    out.changes.append(f"第 {step.id} 步脚本名：「{old or '未命名'}」→「{title}」")


def _edit_script(recipe: Recipe, edit: dict[str, Any], out: _Collector) -> None:
    _fields(edit, {"step_id"}, {"code", "replacements"})
    step = _python_step(recipe, edit["step_id"])
    if ("code" in edit) == ("replacements" in edit):
        raise NodeEditError("edit_script 需要二选一：code（整段新脚本）或 replacements（查找替换列表）。")
    old_code = resolve_script_code(step.params)
    if "code" in edit:
        if not isinstance(edit["code"], str):
            raise NodeEditError("code 必须是文字。")
        new_code = edit["code"]
    else:
        replacements = edit["replacements"]
        if not isinstance(replacements, list) or not replacements:
            raise NodeEditError("replacements 必须是非空列表，每项 {old, new, replace_all?}。")
        new_code = old_code
        for index, item in enumerate(replacements, start=1):
            if not isinstance(item, dict) or set(item) - {"old", "new", "replace_all"}:
                raise NodeEditError(f"replacements 第 {index} 项只能包含 old、new、replace_all。")
            find, replace = item.get("old"), item.get("new")
            if not isinstance(find, str) or not find or not isinstance(replace, str):
                raise NodeEditError(f"replacements 第 {index} 项的 old 必须是非空文字，new 必须是文字。")
            count = new_code.count(find)
            if count == 0:
                raise NodeEditError(f"replacements 第 {index} 项：脚本里找不到要替换的文字「{find[:60]}」。")
            if count > 1 and item.get("replace_all") is not True:
                raise NodeEditError(f"replacements 第 {index} 项：「{find[:60]}」出现了 {count} 次；"
                                    "请带上更多上下文使其唯一，或明确 replace_all=true。")
            new_code = new_code.replace(find, replace)
    if new_code == old_code:
        raise NodeEditError("脚本内容没有变化。")
    if not new_code.strip() or len(new_code.encode("utf-8")) > MAX_SCRIPT_BYTES:
        raise NodeEditError("Python 脚本不能为空，且不能超过 100 KB。")
    try:
        ast.parse(new_code)
    except SyntaxError as exc:
        raise NodeEditError(f"修改后的脚本有语法错误（第 {exc.lineno} 行）：{exc.msg}") from None
    before = step.params.get("script_id")
    step.params = script_params({**step.params, "code": new_code})
    out.script_updated(before, step.params.get("script_id"))
    diff = list(difflib.unified_diff(old_code.splitlines(), new_code.splitlines(), lineterm="", n=0))
    added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
    out.changes.append(f"第 {step.id} 步脚本「{step.params.get('script_title', '')}」内容已更新：+{added} 行 −{removed} 行")
    out.needs_rerun = True


_OPERATIONS = {
    "update_workflow": _update_workflow,
    "rename_input_file": _rename_input_file,
    "rename_input_alias": _rename_input_alias,
    "rename_output_file": _rename_output_file,
    "rename_output_sheet": _rename_output_sheet,
    "remove_input": _remove_input,
    "remove_output": _remove_output,
    "update_step": _update_step,
    "rename_script": _rename_script,
    "edit_script": _edit_script,
}


def apply_node_edits(recipe: Recipe, edits: list[dict[str, Any]], library: FormulaLibrary | None = None) -> NodeEditResult:
    """按顺序应用修改；任何一项不合法或结果破坏流程结构时整体拒绝，原流程不变。"""
    if not isinstance(edits, list) or not edits:
        raise NodeEditError("edits 必须是非空的修改列表。")
    if len(edits) > MAX_EDITS:
        raise NodeEditError(f"一次最多修改 {MAX_EDITS} 个节点。")
    library = library or FormulaLibrary()
    revised, out = recipe.model_copy(deep=True), _Collector(library)
    for index, edit in enumerate(edits, start=1):
        if not isinstance(edit, dict) or edit.get("op") not in _OPERATIONS:
            raise NodeEditError(f"第 {index} 项修改的 op 必须是：{'、'.join(_OPERATIONS)}。")
        try:
            _OPERATIONS[edit["op"]](revised, edit, out)
        except NodeEditError as exc:
            raise NodeEditError(f"第 {index} 项修改（{edit['op']}）：{exc}") from None
    if not out.changes:
        if out.unchanged:
            # 目标已经生效是成功结果（例如重复提交、恢复后重试），不能报错，否则会被当成失败的执行。
            return NodeEditResult(recipe, unchanged=out.unchanged)
        raise NodeEditError("这些修改没有改变流程。")
    before = set(check_recipe(recipe, library))
    introduced = [problem for problem in check_recipe(revised, library) if problem not in before]
    if introduced:
        raise NodeEditError("修改后的流程没有通过结构检查：" + "；".join(introduced))
    return NodeEditResult(revised, out.changes, out.warnings, out.new_script_ids, out.needs_rerun, out.unchanged)
