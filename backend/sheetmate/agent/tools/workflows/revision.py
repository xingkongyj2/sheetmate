"""Resolve explicit workflow edits before matching inputs or asking the learner.

Removing an input changes the definition first: a removed file must never be
required just to revise its former consumers. Source objects remain untouched.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sheetmate.engine.formulas import FormulaLibrary
from sheetmate.recipe.definition.schema import Recipe, RecipeInput, RecipeStep, step_file_refs, step_output_tables, step_table_refs
from sheetmate.recipe.execution.runner import RepairContext, StepRepairer
from sheetmate.recipe.learning.learner import output_delivery_identities, step_execution_signature


def _downstream_step_ids(recipe: Recipe, selected: list[int], library: FormulaLibrary | None) -> set[int]:
    affected = set(selected)
    while True:
        tables = {table for step in recipe.steps if step.id in affected for table in step_output_tables(step)}
        namespaces = {step.output for step in recipe.steps if step.id in affected and step.kind == "python"}
        downstream = set()
        for step in recipe.steps:
            if step.id in affected:
                continue
            files = step_file_refs(step)
            file_tables = step.params.get("file_tables", {}) if step.kind == "python" else {}
            if isinstance(file_tables, dict):
                files += [spec["ref"] for spec in file_tables.values()
                          if isinstance(spec, dict) and isinstance(spec.get("ref"), str)]
            if (tables.intersection(step_table_refs(step, library))
                    or any(ref.startswith(f"{namespace}/") for ref in files for namespace in namespaces)):
                downstream.add(step.id)
        if not downstream:
            return affected
        affected.update(downstream)


class RevisionStepRepairer:
    """Keep runtime step repair within the same scope as structure repair."""

    def __init__(self, repairer: StepRepairer, revision: dict[str, Any]):
        self.repairer = repairer
        self.protected = {step["id"]: step["signature"] for step in revision.get("protected_steps", [])}

    async def repair(self, ctx: RepairContext) -> RecipeStep | None:
        fixed = await self.repairer.repair(ctx)
        expected = self.protected.get(ctx.step.id)
        if fixed is not None and expected is not None and step_execution_signature(fixed) != expected:
            raise ValueError(f"第 {ctx.step.id} 步不在本次修改范围内，自动修复不能修改其执行定义；请保留原流程后重新适配选中步骤及其下游。")
        return fixed


def _selected_output_file(recipe: Recipe, target: dict[str, Any]) -> dict[str, Any] | None:
    # Every file carries sources; individual sheets only carry name/source.
    # Keep that distinction even when a sheet happens to use the file's name.
    if "sources" not in target:
        return None
    return next((item for item in recipe.flow()["output_files"]
                 if (not target.get("name") or item["name"] == target["name"])
                 and (not target.get("source") or item["source"] == target["source"])
                 and set(item["sources"]) == set(target["sources"])), None)


def _selected_output_names(recipe: Recipe, target: dict[str, Any]) -> set[str]:
    """A selected sheet and a selected file have different deletion scopes."""
    if "sources" in target:
        selected_file = _selected_output_file(recipe, target)
        if selected_file and selected_file.get("ref"):
            return set()  # A script file is separate from same-step table output.
        sources = set(target["sources"])
        return {output.name for output in recipe.output_spec if output.source in sources}
    return {output.name for output in recipe.output_spec
            if output.name == target.get("name")
            and (not target.get("source") or output.source == target["source"])}


def revision_request(recipe: Recipe, args: dict[str, Any], edit: dict[str, Any] | None = None,
                     library: FormulaLibrary | None = None) -> dict[str, Any]:
    edit = edit or {}
    if edit and edit.get("recipe_id") != recipe.id:
        raise ValueError("当前选中的是另一条流程，请使用本次对话选中的 recipe_id 修订。")
    version = args.get("base_version", edit.get("version", recipe.version))
    if isinstance(version, bool) or not isinstance(version, int) or version != recipe.version:
        raise ValueError(f"流程版本已变化，当前为 v{recipe.version}；请刷新流程后基于当前版本修改。")
    if edit and edit.get("version") != version:
        raise ValueError("base_version 必须与本次选中的流程版本一致，请刷新后重新选择。")
    target = deepcopy(edit.get("target") or {"kind": "workflow"})
    operation = edit.get("operation", "modify")
    step_ids = args.get("step_ids", [])
    if not isinstance(step_ids, list) or any(isinstance(value, bool) or not isinstance(value, int) for value in step_ids):
        raise ValueError("step_ids 必须使用流程中真实的步骤编号。")
    if target.get("kind") == "step":
        selected = target.get("step_id")
        if step_ids and step_ids != [selected]:
            raise ValueError("只能修改本次选中的步骤；step_ids 必须与选中步骤一致。")
        step_ids = [selected]
    if set(step_ids) - {step.id for step in recipe.steps}:
        raise ValueError("step_ids 含不存在的步骤，请先查看当前流程。")
    request = {"base_version": version, "target": target, "operation": operation,
               "step_ids": list(dict.fromkeys(step_ids)), "input_changes": deepcopy(args.get("input_changes", []))}
    if step_ids:
        affected = _downstream_step_ids(recipe, step_ids, library)
        request["adaptable_step_ids"] = sorted(affected)
        request["protected_steps"] = [{"id": step.id, "signature": step_execution_signature(step)}
                                      for step in recipe.steps if step.id not in affected]
    if target.get("kind") == "output" and operation == "add":
        request["base_output_identities"] = [list(identity) for identity in output_delivery_identities(recipe)]
    selected_file = _selected_output_file(recipe, target) if target.get("kind") == "output" else None
    if selected_file is not None:
        request["target_file_ref"] = selected_file["ref"]
    selected_outputs = (_selected_output_names(recipe, target)
                        if target.get("kind") == "output" and operation != "add" else set())
    if "output_sheets" in args:
        request["output_sheets"] = deepcopy(args["output_sheets"])
    elif target.get("kind") == "output" and operation == "remove":
        if selected_outputs:
            request["output_sheets"] = [output.model_dump(mode="json") for output in recipe.output_spec
                                         if output.name not in selected_outputs]
    if target.get("kind") in {"input", "step", "output"}:
        request["protected_output_names"] = [output.name for output in recipe.output_spec
                                              if output.name not in selected_outputs]
        request["protected_output_files"] = [item["name"] for item in recipe.flow()["output_files"]
                                             if item.get("ref") and (operation == "add" or item != selected_file)]
        if "output_sheets" in request:
            if not isinstance(request["output_sheets"], list) or any(not isinstance(output, dict) for output in request["output_sheets"]):
                raise ValueError("output_sheets 必须提供完整的输出工作表声明列表。")
            names = {output.get("name") or output.get("source") for output in request["output_sheets"]}
            if set(request["protected_output_names"]) - names:
                raise ValueError("output_sheets 必须保留本次未选中修改的其他输出，请提供完整输出声明。")
    return request


def apply_input_changes(recipe: Recipe, changes: Any, explicit: Any,
                        request: dict[str, Any]) -> tuple[Recipe, dict[str, str]]:
    if not isinstance(changes, list):
        raise ValueError("input_changes 必须是输入修改列表。")
    if explicit is not None and not isinstance(explicit, dict):
        raise ValueError("inputs 必须为输入别名到表名或文件 ID 的映射。")
    mapping = dict(explicit or {})
    if any(not isinstance(alias, str) or not isinstance(value, str) or not value.strip() for alias, value in mapping.items()):
        raise ValueError("inputs 必须使用非空输入名和真实表名或文件 ID。")
    revised = recipe.model_copy(deep=True)
    definitions = {item.alias: item for item in revised.inputs}
    changed = set()
    for change in changes:
        if not isinstance(change, dict):
            raise ValueError("每项 input_changes 必须包含 operation 和 alias。")
        alias, operation = change.get("alias"), change.get("operation")
        if not isinstance(alias, str) or not alias.strip() or alias != alias.strip():
            raise ValueError("输入 alias 必须是非空且无首尾空格的完整别名。")
        if alias in changed:
            raise ValueError(f"输入「{alias}」不能在一次修订中重复修改。")
        changed.add(alias)
        if operation not in {"add", "replace", "remove"}:
            raise ValueError("输入修改 operation 只能为 add、replace 或 remove。")
        if operation == "add" and alias in definitions:
            raise ValueError(f"输入「{alias}」已存在，替换来源请使用 replace。")
        if operation != "add" and alias not in definitions:
            raise ValueError(f"流程没有输入「{alias}」，请先查看真实输入别名。")
        if operation == "remove":
            if alias in mapping:
                raise ValueError(f"输入「{alias}」已指定删除，不能同时在 inputs 中绑定。")
            if "source" in change or "kind" in change:
                raise ValueError("删除输入只填写 alias，不再提供 source 或 kind。")
            del definitions[alias]
            continue
        source = change.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ValueError(f"输入「{alias}」的新增或替换必须提供真实 source。")
        kind = change.get("kind", definitions[alias].kind if alias in definitions else "table")
        if kind not in {"table", "file"}:
            raise ValueError("输入 kind 只能为 table 或 file。")
        if alias in mapping and mapping[alias] != source:
            raise ValueError(f"输入「{alias}」在 inputs 和 input_changes 中的来源不一致。")
        # Let the learner derive schema, filename and matching rules from the
        # replacement. Old required columns must not reject the new source.
        definitions[alias] = RecipeInput(alias=alias, kind=kind)
        mapping[alias] = source
    target = request.get("target") or {}
    selected = set(target.get("aliases") or [])
    operation = request.get("operation")
    if target.get("kind") == "input" and operation in {"remove", "replace"}:
        by_alias = {change["alias"]: change for change in changes}
        if not selected or any(by_alias.get(alias, {}).get("operation") != operation for alias in selected):
            raise ValueError(f"请在 input_changes 中为选中的全部输入别名填写 {operation}，不能只修改说明。")
    if target.get("kind") == "input" and operation == "add" and not any(change.get("operation") == "add" for change in changes):
        raise ValueError("新增输入必须在 input_changes 中提供 add、alias、kind 和 source。")
    revised.inputs = list(definitions.values())
    bindings = revised.learned_from.get("input_bindings")
    if isinstance(bindings, dict):
        revised.learned_from["input_bindings"] = {alias: binding for alias, binding in bindings.items()
                                                    if alias in definitions and alias not in changed}
    return revised, mapping
