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
from sheetmate.recipe.editing.references import find_filename
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


def _only_renames_workflow(before: Recipe, after: Recipe) -> bool:
    """除了流程名，改完的流程和原来完全一致（说明、输入、步骤、输出、脚本引用都没动）。"""
    if before.name == after.name:
        return False
    return after.model_copy(update={"name": before.name}).model_dump(mode="json") == before.model_dump(mode="json")


def _is_rename_only_request(edits: Any) -> bool:
    """请求本身只是改流程名（每项都是 update_workflow 且只带 name）；版本落后时也允许，因为名字和版本内容无关。"""
    return (isinstance(edits, list) and bool(edits)
            and all(isinstance(item, dict) and item.get("op") == "update_workflow" and set(item) <= {"op", "name"} for item in edits))


def edit_nodes(db: Session, user: User, recipe_id: str, edits: list[dict[str, Any]], *, base_version: int | None = None,
               note: str = "", conversation_id: str | None = None, customer_id: str | None = None) -> dict[str, Any]:
    """应用节点修改并保存为新版本；修改不合法时不保存任何内容。"""
    recipe, latest = _load(db, user, recipe_id, conversation_id=conversation_id, customer_id=customer_id)
    stale = base_version is not None and base_version != latest.version
    if stale and not _is_rename_only_request(edits):
        raise Conflict(f"流程已更新到 v{latest.version}，请基于最新版本重新修改。")
    try:
        result = apply_node_edits(recipe, edits, recipe_service.library_for(db, recipe.customer_id))
    except NodeEditError as exc:
        raise ServiceError(str(exc)) from exc
    if stale and not _only_renames_workflow(recipe, result.recipe):
        raise Conflict(f"流程已更新到 v{latest.version}，请基于最新版本重新修改。")  # 声称只改名、实际不止
    if not result.changed:
        return {"recipe_id": latest.recipe_id, "version": latest.version, "name": latest.name, "saved": False,
                "customer_id": latest.customer_id, "changes": [], "unchanged": result.unchanged, "warnings": [],
                "needs_rerun": False, "description": recipe.description, "flow": recipe.flow()}
    if _only_renames_workflow(recipe, result.recipe):
        # 只改了流程名：名字不影响处理逻辑，原地改名，不生成新版本
        file_rename.rename_in_place(db, latest.recipe_id, result.recipe.name)
        return {"recipe_id": latest.recipe_id, "version": latest.version, "name": result.recipe.name,
                "customer_id": latest.customer_id, "saved": True, "changes": result.changes, "unchanged": result.unchanged,
                "warnings": result.warnings, "needs_rerun": False,
                "description": result.recipe.description, "flow": result.recipe.flow()}
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


    def rename_flow_files(self, host: Any, recipe_id: str, renames: list[dict[str, Any]]) -> dict[str, Any]:
        """对话里改流程的输入 / 输出文件名，一次调用完成：自己判断每个旧名是输入还是输出，走对的路径，不重跑。

        输出文件：改流程里声明的名字（rename_output_file，同步脚本等所有引用）；
        输入文件：改已上传文件的真实名字，并同步流程里的来源文件名、匹配规则和所有引用。"""
        pairs = _clean_pairs(renames)
        with session_scope() as db:
            user, scope = self._scope(db, host)
            recipe, _latest = _load(db, user, recipe_id, **scope)
            outputs = {item["name"] for item in declared_files(recipe)}
            files_by_name = {record.filename: record for record in file_service.list_files(
                db, customer_id=scope["customer_id"]) if record.kind in {"input", "expected"}}
            bound_ids = {(recipe.learned_from.get("input_bindings") or {}).get(i.alias, {}).get("file_id") for i in recipe.inputs}
            input_names = {i.source_file for i in recipe.inputs if i.source_file}
            output_edits, input_renames, unknown = [], [], []
            for old, new in pairs:
                if old in outputs:
                    output_edits.append({"op": "rename_output_file", "name": old, "new_name": new})
                    continue
                record = files_by_name.get(old)
                if record is not None and (record.id in bound_ids or old in input_names):
                    input_renames.append({"file_id": record.id, "new_name": new})
                else:
                    unknown.append(old)
            if unknown:
                raise ServiceError(
                    f"流程里没有叫「{'」「'.join(unknown)}」的输入或输出文件。"
                    f"输入文件：{'、'.join(sorted(input_names)) or '无'}；输出文件：{'、'.join(sorted(outputs)) or '无'}。")
        result: dict[str, Any] = {"recipe_id": recipe_id, "outputs": None, "inputs": None, "leftovers": []}
        if input_renames:
            result["inputs"] = self.rename_files(host, input_renames)
        if output_edits:
            result["outputs"] = self.edit(host, recipe_id, output_edits)
        # 不管走的是哪条路径，最后都按最新保存的流程再核对一遍：还有哪些地方出现旧文件名，原样告诉 AI（它再决定改不改）
        with session_scope() as db:
            latest = recipe_service.record_to_recipe(recipe_service.get_record(db, recipe_id))
        new_names = {old: new for old, new in pairs}
        for old, new in new_names.items():
            final = result_new_name(result, old) or new
            for hit in find_filename(latest, old, ignore=final):
                result["leftovers"].append({"old": old, "where": hit.where, "count": hit.count})
        return result


def result_new_name(result: dict[str, Any], old: str) -> str | None:
    """重名时系统会自动追加（1）：核对要用真正生效的新名字。"""
    for item in (result.get("inputs") or {}).get("files", []):
        if item["old"] == old and item["changed"]:
            return item["new"]
    return None


def _clean_pairs(renames: list[dict[str, Any]]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for item in renames if isinstance(renames, list) else []:
        old, new = str((item or {}).get("old_name") or "").strip(), str((item or {}).get("new_name") or "").strip()
        if not old or not new:
            raise ServiceError("每项都要写 old_name（现在的文件名）和 new_name（新文件名）。")
        pairs.append((old, new))
    if not pairs:
        raise ServiceError("renames 至少要有一项：{old_name, new_name}。")
    if len({old for old, _ in pairs}) != len(pairs):
        raise ServiceError("同一个文件不能在一次调用里改两次。")
    return pairs


chat_tools.register_backend(_ChatBackend())
