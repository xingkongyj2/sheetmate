"""把旧版成功脚本审计记录补成执行快照；不执行脚本，也不创建永久流程。"""

from __future__ import annotations

import ast
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from sheetmate.db.database import session_scope
from sheetmate.db.models import FileRecord
from sheetmate.engine.sqlutil import sanitize_table_name
from sheetmate.engine.script_store import resolve_script_code, script_params
from sheetmate.engine.workspace import Workspace
from sheetmate.recipe.compiler import compile_script
from sheetmate.recipe.schema import Recipe, check_recipe
from sheetmate.services import files as file_service
from sheetmate.services import recipes as recipe_service
from sheetmate.services import runs as run_service
from sheetmate.services.errors import ServiceError


_CAPTURING: ContextVar[tuple[str, ...]] = ContextVar("legacy_workflow_capture", default=())


def _scoped(db, host, record) -> bool:
    if isinstance(record, FileRecord):
        return file_service.can_access_in_conversation(
            db, record, conversation_id=host.conversation_id, customer_id=host.customer_id,
        )
    return record.conversation_id == host.conversation_id or bool(host.customer_id and record.customer_id == host.customer_id)


def _code_names(code: str) -> tuple[set[str], set[str]]:
    """只读语法树恢复字面文件名和结果键，不评估任何脚本表达式。"""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise ServiceError("历史脚本代码不完整，不能保存可重复执行流程") from exc
    filenames, labels = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            name = Path(node.value).name
            if Path(name).suffix.lower() in {".xlsx", ".xlsm", ".csv"}:
                filenames.add(name)
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store) and isinstance(node.value, ast.Name) and node.value.id == "result_tables":
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                labels.add(node.slice.value)
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "result_tables" for target in node.targets) and isinstance(node.value, ast.Dict):
            labels.update(key.value for key in node.value.keys if isinstance(key, ast.Constant) and isinstance(key.value, str))
    return filenames, labels


def _original_filename(item: dict[str, Any], record: FileRecord, candidates: set[str]) -> str:
    # 展示名可能因重名自动编号；脚本内部仍按登记时的原名生成文件。
    recorded_name = record.filename
    original = (record.profile or {}).get("original_filename")
    if isinstance(original, str) and original and Path(original).name == original:
        recorded_name = file_service.safe_filename(original)
    ref = item.get("ref")
    if isinstance(ref, str) and "/" in ref:
        name = ref.split("/", 1)[1]
        if Path(name).name == name and file_service.safe_filename(name) == recorded_name:
            return name
    matches = {name for name in candidates if file_service.safe_filename(name) == recorded_name}
    if len(matches) == 1:
        return matches.pop()
    if len(matches) > 1:
        raise ServiceError(f"历史输出「{record.filename}」的原始文件名不明确，无法可靠保存")
    # 普通输出沿用审计文件名；出现规范化痕迹且代码没有可核对字面量时拒绝猜测。
    if file_service.safe_filename(recorded_name) != recorded_name or "_" in recorded_name:
        raise ServiceError(f"历史输出「{record.filename}」缺少可核对的原始文件名，无法可靠保存")
    return recorded_name


def _restore_table(ws: Workspace, name: str, originals: list[FileRecord]) -> None:
    candidates = []
    for record in originals:
        probe = Workspace(None)
        try:
            for table in file_service.load_into_workspace(probe, record):
                if table.name == name:
                    candidates.append((table, probe.df(table.name)))
        except (ValueError, OSError):
            continue
        finally:
            probe.close()
    if len(candidates) != 1:
        raise ServiceError(f"历史脚本输入表「{name}」已不在工作区，无法从原始输入可靠恢复；不能将历史输出当作原件保存")
    info, frame = candidates[0]
    metadata = info.to_dict()
    metadata.pop("name", None)
    ws.put_dataframe(name, frame, **metadata)


def capture_legacy_script(host, run_id: str) -> Recipe:
    """从原代码和绑定延迟编译旧脚本，成功后仅更新 Run 的执行快照。"""
    active = _CAPTURING.get()
    if run_id in active:
        raise ServiceError("历史脚本文件存在循环依赖，不能保存流程")
    token = _CAPTURING.set((*active, run_id))
    ws: Workspace | None = None
    try:
        with session_scope() as db:
            run = run_service.get_run(db, run_id)
            if not _scoped(db, host, run):
                raise ServiceError("这次脚本运行不属于当前客户或会话")
            if run.kind != "script" or run.status != "success":
                raise ServiceError("只能从成功的 Python 脚本运行保存流程")
            validation = dict(run.validation or {})
            if validation.get("recipe_deleted"):
                raise ServiceError("这个流程已删除，不能从历史脚本重新保存")
            if any(item.get("ok") is False for item in validation.get("checks", []) if isinstance(item, dict)):
                raise ServiceError("这次历史脚本的业务校验未通过，不能保存流程")
            snapshot = validation.get("recipe_snapshot")
            if isinstance(snapshot, dict):
                return Recipe.model_validate(snapshot)
            steps = [step for step in run.steps or [] if step.get("tool") == "run_python_script"]
            if len(steps) != 1 or not isinstance(steps[0].get("params"), dict):
                raise ServiceError("历史脚本缺少完整的代码和输入绑定，不能保存流程")
            params = dict(steps[0]["params"])
            code = resolve_script_code(params)
            params = script_params(params, title=run.request_text)
            tables = params.get("input_tables") or []
            files = params.get("input_files") or {}
            if not isinstance(tables, list) or any(not isinstance(name, str) or not name for name in tables) or not isinstance(files, dict) or any(not isinstance(alias, str) or not isinstance(fid, str) for alias, fid in files.items()):
                raise ServiceError("历史脚本的输入绑定不完整，不能保存流程")
            original_ids = set(run.input_file_ids or [])
            if not set(files.values()) <= original_ids:
                raise ServiceError("历史脚本文件绑定与原始运行记录不一致，不能保存流程")
            originals = []
            for file_id in original_ids:
                record = file_service.get_file(db, file_id)
                if not _scoped(db, host, record):
                    raise ServiceError("历史脚本的输入文件不属于当前客户或会话")
                if record.kind == "input":
                    originals.append(record)
            outputs = []
            filename_literals, result_labels = _code_names(code)
            for item in validation.get("outputs") or []:
                if not isinstance(item, dict) or not item.get("file_id"):
                    raise ServiceError("历史脚本输出文件记录不完整，不能保存流程")
                record = file_service.get_file(db, item["file_id"])
                if not _scoped(db, host, record) or record.run_id != run_id or record.kind != "output":
                    raise ServiceError("历史脚本输出文件来源不一致，不能保存流程")
                outputs.append((dict(item), record, _original_filename(item, record, filename_literals)))
            if run.output_file_id and run.output_file_id not in {record.id for _, record, _ in outputs}:
                raise ServiceError("历史脚本缺少正式输出文件记录，不能保存流程")
            library = recipe_service.library_for(db, host.customer_id)
            description = str(params.get("description") or run.request_text or "历史脚本流程")

        ws = Workspace(None)
        for info in host.ws.list_tables():
            metadata = info.to_dict()
            metadata.pop("name", None)
            if info.source == "uploaded" and info.file_id and not (info.step or {}).get("recipe_snapshot"):
                with session_scope() as db:
                    origin = file_service.get_file(db, info.file_id)
                    if not _scoped(db, host, origin) or origin.kind != "input":
                        # 旧版输出导入曾标成 uploaded/input；缺少生成链时不可编为原始输入。
                        metadata["source"] = "computed"
            ws.put_dataframe(info.name, host.ws.df(info.name), **metadata)
        for name in tables:
            if not ws.exists(name):
                _restore_table(ws, name, originals)
            info = ws.require(name)
            if info.source == "uploaded" and info.file_id not in original_ids:
                raise ServiceError(f"历史脚本输入表「{name}」已被其他文件替换，不能保存错误的流程")

        namespace = sanitize_table_name("脚本_" + run_id)
        file_refs = {record.id: f"{namespace}/{name}" for _, record, name in outputs}
        file_tables, output_tables = {}, {}
        reported = [*validation.get("tables", []), *validation.get("internal_tables", [])]
        for item in reported:
            name = item.get("name") if isinstance(item, dict) else None
            info = ws.info(name) if isinstance(name, str) else None
            if info is not None and info.file_id in file_refs:
                file_tables[name] = {"ref": file_refs[info.file_id], "sheet": info.sheet, "header": info.header}
        for label in result_labels:
            matches = [item["name"] for item in reported if isinstance(item, dict) and isinstance(item.get("name"), str)
                       and item["name"] not in file_tables and
                       (item["name"] == sanitize_table_name(label) or (ws.info(item["name"]) is not None and ws.require(item["name"]).label == label))]
            if len(matches) != 1:
                raise ServiceError(f"历史脚本结果表「{label}」缺少可靠的输出映射，不能保存流程")
            output_tables[label] = matches[0]
        if not outputs and not output_tables:
            raise ServiceError("历史脚本没有可重复交付的文件或结果表，不能保存流程")

        def resolve_file(file_id: str):
            with session_scope() as db:
                record = file_service.get_file(db, file_id)
                if not _scoped(db, host, record):
                    raise ServiceError("历史脚本引用的文件不属于当前客户或会话")
                generated = record.kind == "output"
            dependency = host.file_dependency(file_id)
            if generated and not dependency.replaceable_source and (dependency.recipe is None or not dependency.ref):
                raise ServiceError("历史脚本引用的结果文件缺少生成步骤，不能将该输出当作原始输入保存")
            return dependency

        recipe = compile_script(ws, library, run_id=run_id, code=code, description=description,
                                script_id=params["script_id"], script_title=params["script_title"],
                                input_tables=tables, input_files=files, output_tables=output_tables,
                                file_tables=file_tables, file_resolver=resolve_file,
                                output_files=[{"name": record.filename, "ref": file_refs[record.id]} for _, record, _ in outputs],
                                display_tables=[label for label, name in output_tables.items()
                                                if any(item.get("name") == name for item in validation.get("tables", []) if isinstance(item, dict))],
                                customer_id=host.customer_id, conversation_id=host.conversation_id,
                                field_mapping=host.field_mapping())
        problems = check_recipe(recipe, library)
        if problems:
            raise ServiceError("历史脚本依赖无法可靠保存：" + "；".join(problems))
        with session_scope() as db:
            run = run_service.get_run(db, run_id)
            if not _scoped(db, host, run) or run.status != "success" or (run.validation or {}).get("recipe_deleted"):
                raise ServiceError("历史脚本状态已变化，不能保存流程")
            current = dict(run.validation or {})
            current["recipe_snapshot"] = recipe.to_dict()
            current["outputs"] = [{**item, "ref": file_refs[record.id]} for item, record, _ in outputs]
            current.pop("workflow_save_error", None)
            run.validation = current
            run.recipe_id, run.recipe_version = recipe.id, recipe.version
        return recipe
    except (ValueError, KeyError, OSError) as exc:
        raise ServiceError(f"历史脚本缺少可靠的执行依赖，无法保存流程：{exc}") from exc
    finally:
        if ws is not None:
            ws.close()
        _CAPTURING.reset(token)
