"""流程节点编辑服务：查看节点详情、按节点直接修改并保存为新版本。

纯修改逻辑在 sheetmate.recipe.editing；这里负责权限、会话范围、版本冲突、新脚本版本的归属绑定和保存。
HTTP 接口直接调用本模块；聊天工具通过 agent.tools.workflows.node_edit 的后端接口调用（agent 层不能依赖 services）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from sheetmate.agent.tools.workflows import node_edit as chat_tools
from sheetmate.core.errors import Conflict, Forbidden, ServiceError
from sheetmate.db.models import RecipeRecord, User
from sheetmate.db.session import session_scope
from sheetmate.engine.scripts.store import bind_script, resolve_script_code
from sheetmate.recipe.definition.schema import Recipe
from sheetmate.recipe.editing import NodeEditError, apply_node_edits, declared_files
from sheetmate.services.files import service as file_service
from sheetmate.services.workflows import file_rename
from sheetmate.services.workflows import service as recipe_service

_SCRIPT_KEYS = {"code", "script_id", "script_title", "script_filename"}
_CODE_CHARS = 20_000  # 单次返回的脚本字符上限；更长的按行区间分段读取


def _load(db: Session, user: User, recipe_id: str, *, version: int | None = None,
          conversation_id: str | None = None, customer_id: str | None = None) -> tuple[Recipe, RecipeRecord]:
    record = recipe_service.get_record(db, recipe_id, version)
    recipe = recipe_service.record_to_recipe(record)
    recipe_service.require_recipe_access(db, user, recipe)
    if conversation_id is not None:
        # 聊天里只能改本客户或本会话的流程，与 host.get_recipe 的范围一致。
        in_customer = bool(record.customer_id and record.customer_id == customer_id)
        if not in_customer and record.conversation_id != conversation_id:
            raise Forbidden("这个流程不属于当前客户或会话。")
    return recipe, record


def inspect_nodes(db: Session, user: User, recipe_id: str, *, version: int | None = None, step_id: int | None = None,
                  line_start: int | None = None, line_end: int | None = None,
                  conversation_id: str | None = None, customer_id: str | None = None) -> dict[str, Any]:
    """列出流程各节点的可修改内容；指定 step_id 的脚本步骤时附带脚本内容（可按行区间读取）。"""
    recipe, _ = _load(db, user, recipe_id, version=version, conversation_id=conversation_id, customer_id=customer_id)
    steps = []
    for step in sorted(recipe.steps, key=lambda item: item.id):
        item: dict[str, Any] = {
            "id": step.id, "name": step.name, "kind": step.kind, "enabled": step.enabled, "output": step.output,
            "description": step.description,
            "params": {key: value for key, value in step.params.items() if key not in _SCRIPT_KEYS},
        }
        if step.kind == "python":
            try:
                lines = resolve_script_code(step.params).count("\n") + 1
            except (OSError, ValueError):
                lines = None
            item["script"] = {"script_id": step.params.get("script_id"), "script_title": step.params.get("script_title"),
                              "line_count": lines}
        steps.append(item)
    data: dict[str, Any] = {
        "recipe_id": recipe.id, "version": recipe.version, "name": recipe.name, "description": recipe.description,
        "inputs": [{"alias": i.alias, "kind": i.kind, "file_pattern": i.file_pattern, "source_file": i.source_file,
                    "sheet": i.sheet, "description": i.description} for i in recipe.inputs],
        "steps": steps,
        "script_files": [{"name": f["name"], "ref": f["ref"], "step_id": f["step"].id} for f in declared_files(recipe)],
        "output_files": [{"name": f["name"], "ref": f["ref"]} for f in recipe.flow()["output_files"]],
        "output_sheets": [{"name": o.name, "source": o.source} for o in recipe.output_spec],
    }
    if step_id is not None:
        step = next((item for item in recipe.steps if item.id == step_id), None)
        if step is None:
            raise ServiceError(f"流程没有第 {step_id} 步。")
        if step.kind != "python":
            raise ServiceError(f"第 {step_id} 步不是 Python 脚本步骤，没有脚本内容。")
        try:
            code = resolve_script_code(step.params)
        except (OSError, ValueError) as exc:
            raise ServiceError(f"读取脚本失败：{exc}") from exc
        all_lines = code.split("\n")
        start = max(1, line_start or 1)
        end = min(len(all_lines), line_end or len(all_lines))
        if line_start is None and line_end is None and len(code) > _CODE_CHARS:
            # 太长时先给开头；模型据此按行区间继续读取，而不是被静默截断。
            end, shown = 0, 0
            for number, text in enumerate(all_lines, start=1):
                if shown + len(text) + 1 > _CODE_CHARS:
                    break
                end, shown = number, shown + len(text) + 1
        if start > end:
            raise ServiceError("line_start 不能大于 line_end。")
        data["script"] = {"step_id": step.id, "script_id": step.params.get("script_id"),
                          "script_title": step.params.get("script_title"), "line_count": len(all_lines),
                          "line_start": start, "line_end": end, "complete": start == 1 and end == len(all_lines),
                          "code": "\n".join(all_lines[start - 1:end])}
    return data


def edit_nodes(db: Session, user: User, recipe_id: str, edits: list[dict[str, Any]], *, base_version: int | None = None,
               note: str = "", conversation_id: str | None = None, customer_id: str | None = None) -> dict[str, Any]:
    """应用节点修改并保存为新版本；修改不合法时不保存任何内容。"""
    recipe, latest = _load(db, user, recipe_id, conversation_id=conversation_id, customer_id=customer_id)
    if base_version is not None and base_version != latest.version:
        raise Conflict(f"流程已更新到 v{latest.version}，请基于最新版本重新修改。")
    try:
        result = apply_node_edits(recipe, edits, recipe_service.library_for(db, recipe.customer_id))
    except NodeEditError as exc:
        raise ServiceError(str(exc)) from exc
    if not result.changed:
        return {"recipe_id": latest.recipe_id, "version": latest.version, "name": latest.name, "saved": False,
                "customer_id": latest.customer_id, "changes": [], "unchanged": result.unchanged, "warnings": [],
                "needs_rerun": False, "description": recipe.description, "flow": recipe.flow()}
    for script_id in result.new_script_ids:
        # 新脚本版本在保存前没有归属；先绑定，保存时的脚本权限检查才能通过。
        bind_script(script_id, user_id=user.id, customer_id=recipe.customer_id, conversation_id=latest.conversation_id)
    saved = file_rename.save_edited_version(db, user, latest, result.recipe.to_dict(),
                                            note.strip() or "节点编辑：" + "；".join(result.changes))
    return {"recipe_id": saved.recipe_id, "version": saved.version, "name": saved.name,
            "customer_id": saved.customer_id, "saved": True, "changes": result.changes, "unchanged": result.unchanged,
            "warnings": result.warnings, "needs_rerun": result.needs_rerun,
            "description": result.recipe.description, "flow": result.recipe.flow()}


class _ChatBackend:
    """把聊天工具的调用接到上面的服务函数；作用范围取自当前对话宿主。"""

    @staticmethod
    def _scope(db: Session, host: Any) -> tuple[User, dict[str, Any]]:
        user = db.get(User, host.user_id)
        if user is None:
            raise ServiceError("当前用户不存在。")
        return user, {"conversation_id": host.conversation_id, "customer_id": host.customer_id}

    def inspect(self, host: Any, recipe_id: str, **options: Any) -> dict[str, Any]:
        with session_scope() as db:
            user, scope = self._scope(db, host)
            return inspect_nodes(db, user, recipe_id, **options, **scope)

    def edit(self, host: Any, recipe_id: str, edits: list[dict[str, Any]], **options: Any) -> dict[str, Any]:
        with session_scope() as db:
            user, scope = self._scope(db, host)
            return edit_nodes(db, user, recipe_id, edits, **options, **scope)

    def rename_files(self, host: Any, renames: list[dict[str, Any]], **options: Any) -> dict[str, Any]:
        with session_scope() as db:
            user, scope = self._scope(db, host)
            result = file_rename.rename_files(
                db, user, renames, accessible=lambda record: file_service.can_access_in_conversation(
                    db, record, conversation_id=scope["conversation_id"], customer_id=scope["customer_id"]),
                **scope, **options)
        # 工作区里已加载的表保留表名（SQL 和流程绑定在用），只更新它们显示的来源文件名。
        # 本轮用的是分析副本，持久化的会话工作区是它的 source；两边都要改，否则下一轮又变回旧名字。
        changed = {item["file_id"]: item for item in result["files"] if item["changed"]}
        for item in changed.values():
            item["tables"] = []
        analysis = getattr(host, "analysis", None)
        for workspace in (host.ws, getattr(analysis, "source", None)):
            for table in workspace.list_tables() if workspace is not None else []:
                item = changed.get(table.file_id or "")
                if item is None:
                    continue
                label = table.label.replace(item["old"], item["new"]) if table.label else table.label
                workspace.update_meta(table.name, filename=item["new"], label=label)
                if table.name not in item["tables"]:
                    item["tables"].append(table.name)
        return result


chat_tools.register_backend(_ChatBackend())
