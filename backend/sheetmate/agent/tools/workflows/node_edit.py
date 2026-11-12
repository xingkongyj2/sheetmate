"""流程节点编辑工具：查看节点详情、直接修改输入/输出文件名、步骤、脚本名和脚本内容。

与 revise_recipe 不同，这里是确定性的直接修改（不调用模型、不重跑整条流程），保存为新版本；
会影响执行结果的修改保存后需要用 run_recipe 重跑验证。具体实现由服务层通过 register_backend 注入。
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from sheetmate.agent.tools.contracts import AgentContext, ToolResult, _obj

_STR = {"type": "string", "minLength": 1}
_MAX_TEXT = 30_000

EDIT_OPERATIONS = ["update_workflow", "rename_input_file", "rename_input_alias", "rename_output_file", "rename_output_sheet",
                   "remove_input", "remove_output", "update_step", "rename_script", "edit_script"]

EDIT_ITEM_SCHEMA = _obj({
    "op": {"type": "string", "enum": EDIT_OPERATIONS},
    "name": {"type": "string", "description": "update_workflow：新流程名；rename_output_file / rename_output_sheet / remove_output：当前名字；update_step：新步骤名"},
    "new_name": {**_STR, "description": "rename_output_file / rename_output_sheet 的新名字（文件名可不带扩展名，扩展名不能变）"},
    "ref": {**_STR, "description": "rename_output_file：同名文件有多个时用 ref 指定"},
    "description": {"type": "string", "description": "update_workflow 流程说明 / rename_input_file 输入说明 / update_step 步骤说明"},
    "alias": {**_STR, "description": "rename_input_file / rename_input_alias / remove_input：当前的完整输入别名"},
    "new_alias": {**_STR, "description": "rename_input_alias：新的输入别名（流程图上输入和步骤引用处显示的名字）；会同步修改所有引用，不能含引号和路径分隔符"},
    "file_pattern": {**_STR, "description": "rename_input_file：新的文件名匹配规则，例如 订单表*"},
    "source_file": {**_STR, "description": "rename_input_file：新的来源文件名（仅记录，运行时按 file_pattern 匹配）"},
    "step_id": {"type": "integer", "minimum": 1, "description": "update_step / rename_script / edit_script：真实步骤编号"},
    "title": {**_STR, "description": "rename_script：新脚本名（最多 20 字）"},
    "enabled": {"type": "boolean", "description": "update_step：启用或停用步骤"},
    "explanation": {"type": "string", "description": "update_step：推断依据"},
    "params": {"type": "object", "description": "update_step：要合并进步骤参数的键值，null 表示删除该参数；脚本内容不能放这里"},
    "code": {"type": "string", "description": "edit_script：整段新脚本（与 replacements 二选一）"},
    "replacements": {"type": "array", "minItems": 1, "description": "edit_script：查找替换列表（与 code 二选一）；old 必须在脚本里唯一，重复出现时加 replace_all=true",
                     "items": _obj({"old": _STR, "new": {"type": "string"}, "replace_all": {"type": "boolean"}}, ["old", "new"])},
}, ["op"])


RENAME_ITEM_SCHEMA = _obj({
    "file_id": {**_STR, "description": "list_files 里看到的准确文件 ID"},
    "new_name": {**_STR, "description": "新文件名，可不带扩展名；扩展名不能改"},
}, ["file_id", "new_name"])


class NodeEditBackend(Protocol):
    def inspect(self, host: Any, recipe_id: str, **options: Any) -> dict[str, Any]: ...
    def edit(self, host: Any, recipe_id: str, edits: list[dict[str, Any]], **options: Any) -> dict[str, Any]: ...
    def rename_files(self, host: Any, renames: list[dict[str, Any]], **options: Any) -> dict[str, Any]: ...


_backend: NodeEditBackend | None = None


def register_backend(backend: NodeEditBackend) -> None:
    global _backend
    _backend = backend


def _service() -> NodeEditBackend:
    if _backend is None:
        raise RuntimeError("流程节点编辑服务尚未启用。")
    return _backend


def _recipe_id(args: dict[str, Any], ctx: AgentContext) -> str:
    value = args.get("recipe_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    latest = ctx.host.latest_conversation_recipe()
    if latest is None:
        raise ValueError("请提供 recipe_id；当前会话没有可用的流程（可用 list_recipes 查看）。")
    return latest.id


def _forget_stale_host_state(host: Any, recipe_id: str, version: int) -> None:
    """保存出新版本后，让宿主缓存的“已读版本/最近流程”追上，否则同一轮里再用 revise_recipe 会被当成并发冲突。"""
    versions = getattr(host, "_revision_versions", None)
    if isinstance(versions, dict):
        versions[recipe_id] = version
    last = getattr(host, "_last_recipe", None)
    if last is not None and getattr(last, "id", None) == recipe_id and getattr(last, "version", 0) < version:
        host._last_recipe = None


def _tool_inspect_recipe_nodes(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    options = {key: args[key] for key in ("version", "step_id", "line_start", "line_end") if args.get(key) is not None}
    data = _service().inspect(ctx.host, _recipe_id(args, ctx), **options)
    text = json.dumps(data, ensure_ascii=False)
    if len(text) > _MAX_TEXT and "script" in data:
        data["steps"] = [{key: step[key] for key in ("id", "name", "kind", "enabled", "output")} for step in data["steps"]]
        text = json.dumps(data, ensure_ascii=False)
    summary = f"查看流程「{data['name']}」v{data['version']}的节点" + (f"（第 {args['step_id']} 步脚本）" if args.get("step_id") else "")
    return ToolResult(text[:_MAX_TEXT], summary=summary, public_text=summary)


def _tool_edit_recipe_nodes(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    edits = args.get("edits")
    if not isinstance(edits, list) or not edits:
        raise ValueError("edits 必须是非空的修改列表。")
    recipe_id = _recipe_id(args, ctx)
    options = {"note": str(args.get("note") or "")}
    if args.get("base_version") is not None:
        options["base_version"] = args["base_version"]
    result = _service().edit(ctx.host, recipe_id, edits, **options)
    if not result.get("saved", True):
        text = (f"无需修改：目标值已经生效，流程「{result['name']}」保持 v{result['version']}，没有生成新版本。\n"
                + "\n".join(f"- {item}" for item in result.get("unchanged", [])))
        summary = f"流程「{result['name']}」已是目标状态"
        return ToolResult(text, summary=summary, public_text=summary)
    _forget_stale_host_state(ctx.host, result["recipe_id"], result["version"])
    lines = [f"已保存流程「{result['name']}」v{result['version']}（节点编辑）：", *[f"- {change}" for change in result["changes"]]]
    lines += [f"提醒：{warning}" for warning in result["warnings"]]
    lines.append("这次修改会影响执行行为或产物，已保存但尚未重跑；请用 run_recipe 重跑验证后再告诉用户结果。"
                 if result["needs_rerun"] else "这次只修改了名称/说明类内容，不影响执行结果，无需重跑。")
    if any(isinstance(edit, dict) and str(edit.get("op", "")).startswith("rename_input") for edit in edits):
        lines.append("注意：这里改的是流程定义里记录的输入名称和匹配规则，不会改变已上传的实际文件名；流程图里“输入文件”显示的是实际文件名。"
                     "用户要改的若是文件本身的名字，请用 rename_files（会同时同步流程里记录的名字）。")
    event = {"type": "recipe_view", "recipe_id": result["recipe_id"], "name": result["name"],
             "description": result["description"], "version": result["version"], "flow": result["flow"]}
    summary = f"已修改流程「{result['name']}」（v{result['version']}）"
    return ToolResult("\n".join(lines), summary=summary, public_text=summary, events=[event])


def _tool_rename_files(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    renames = args.get("renames")
    if not isinstance(renames, list) or not renames:
        raise ValueError("renames 必须是非空列表，每项 {file_id, new_name}。")
    sync = args.get("sync_recipes", True)
    if not isinstance(sync, bool):
        raise ValueError("sync_recipes 必须是 true 或 false。")
    result = _service().rename_files(ctx.host, renames, sync_recipes=sync)
    changed = [item for item in result["files"] if item["changed"]]
    lines = [f"已修改 {len(changed)} 个文件名：" if changed else "没有文件名被修改（都已经是目标名字）。"]
    for item in changed:
        suffix = "" if item["new"] == item["requested"] else f"（与已有文件重名，自动改为「{item['new']}」）"
        lines.append(f"- 「{item['old']}」→「{item['new']}」{suffix}")
    for item in result["files"]:
        if not item["changed"] and changed:
            lines.append(f"- 「{item['old']}」已经是目标名字，未改动")
    for recipe in result["recipes"]:
        _forget_stale_host_state(ctx.host, recipe["recipe_id"], recipe["version"])
        lines.append(f"已同步流程「{recipe['name']}」→ v{recipe['version']}：" + "；".join(recipe["changes"]))
    loaded = [name for item in changed for name in item.get("tables", [])]
    if loaded:
        lines.append(f"提醒：工作区里已加载的表名保持不变（{'、'.join(loaded)}），SQL 和流程绑定仍可继续使用；重新加载文件时会按新文件名生成表名。")
    if changed and not result["recipes"]:
        lines.append("没有流程引用这些文件，或流程里记录的名字已经是新名字，未生成新流程版本。")
    summary = f"修改了 {len(changed)} 个文件名"
    events = [{"type": "recipe_view", "recipe_id": r["recipe_id"], "name": r["name"], "description": r["description"],
               "version": r["version"], "flow": r["flow"]} for r in result["recipes"] if r.get("flow")]
    return ToolResult("\n".join(lines), summary=summary, public_text=summary, events=events)
