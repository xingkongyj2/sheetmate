"""Agent 工具宿主：把数据库、文件、Recipe 存储这些能力提供给 agent/tools.py 和 recipe/pipeline.py。

每个方法都开自己的短事务（session_scope），因为工具可能在后台线程里执行。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from sheetmate.db.database import session_scope
from sheetmate.db.models import FileRecord, Run, User
from sheetmate.engine.file_copies import FileCopies
from sheetmate.engine.reader import ReadError
from sheetmate.engine.workbook_builder import write_created_workbook
from sheetmate.engine.workspace import TableInfo, Workspace
from sheetmate.recipe.matcher import Candidate, match_inputs
from sheetmate.recipe.compiler import FileDependency, compile_from_workspace, with_workbook_export
from sheetmate.recipe.executor import has_reviewable_output
from sheetmate.recipe.pipeline import RunHandle
from sheetmate.recipe.schema import Recipe
from sheetmate.services import files as file_service
from sheetmate.services import memory as memory_service
from sheetmate.services import recipes as recipe_service
from sheetmate.services import runs as run_service
from sheetmate.services.analysis import AnalysisWorkspace
from sheetmate.services.errors import NotFound, ServiceError


class ConversationHost:
    """一次对话请求的宿主（也实现 PipelineHost 协议）。"""

    def __init__(self, *, user_id: int, conversation_id: str, customer_id: str | None, ws: Workspace):
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.customer_id = customer_id
        self.ws = ws
        self.saved_recipes: list[dict[str, Any]] = []
        self.deleted_recipes: list[dict[str, Any]] = []
        self.save_recipe_requested = False
        self.delete_recipe_ids: set[str] = set()
        self._last_recipe: Recipe | None = None
        self.analysis: AnalysisWorkspace | None = None
        self.files: FileCopies | None = None

    # ------------------------------------------------------------------
    # 文件
    # ------------------------------------------------------------------

    def list_files(self) -> list[dict[str, Any]]:
        loaded: dict[str, list[str]] = {}
        for t in self.ws.list_tables():
            if t.file_id:
                loaded.setdefault(t.file_id, []).append(t.name)
        with session_scope() as db:
            records = file_service.list_files(db, customer_id=self.customer_id, conversation_id=self.conversation_id,
                                              kinds=("input", "expected", "output"))
            result = []
            for r in records:
                item = file_service.file_dict(r)
                if r.run_id:
                    run = db.get(run_service.Run, r.run_id)
                    if run is not None and run.kind == "script":
                        item["script_run_id"] = run.id
                item["tables"] = loaded.get(r.id, [])
                item["scope_label"] = "本会话" if r.conversation_id == self.conversation_id else "客户资料"
                result.append(item)
            return result

    def file_path(self, file_id: str) -> Path | None:
        """供分析工具修改的临时副本；Recipe 模板使用 original_file_path。"""
        path = self.original_file_path(file_id)
        if path is None:
            return None
        if self.analysis:
            return self.analysis.file_copy(path)
        return self.files.file_copy(path) if self.files else path

    def original_file_path(self, file_id: str) -> Path | None:
        """仅供内部 Recipe 定位原件，由执行器复制后读取和写出。"""
        with session_scope() as db:
            try:
                return Path(file_service.get_file(db, file_id).stored_path)
            except NotFound:
                return None

    def load_file(self, file_id: str) -> list[TableInfo]:
        with session_scope() as db:
            record = file_service.get_file(db, file_id)
            if record.conversation_id != self.conversation_id and not (self.customer_id and record.customer_id == self.customer_id):
                raise ServiceError("这个文件不属于当前客户或会话")
            source = Path(record.stored_path)
            path = self.analysis.file_copy(source) if self.analysis else (self.files.file_copy(source) if self.files else None)
            loaded = file_service.load_into_workspace(self.ws, record, path=path)
            is_output = record.kind == "output"
        if is_output:
            dependency = self.file_dependency(file_id)
            if dependency.recipe is not None and dependency.ref:
                recipe = dependency.recipe.model_copy(deep=True)
                namespace = dependency.ref.partition("/")[0]
                producer = next((step for step in recipe.steps if step.output == namespace), None)
                if producer is not None:
                    bindings = dict(producer.params.get("file_tables") or {})
                    for info in loaded:
                        bindings[info.name] = {"ref": dependency.ref, "sheet": info.sheet, "header": info.header}
                    producer.params["file_tables"] = bindings
                    for info in loaded:
                        self.ws.update_meta(info.name, source="computed", role="result",
                                            step={"recipe_snapshot": recipe.to_dict(), "table_ref": info.name,
                                                  "run_id": (recipe.metadata.get("last_run") or {}).get("run_id")})
        return [self.ws.require(info.name) for info in loaded]

    def export_excel(self, sheets: list[tuple[str, str]], filename: str) -> dict[str, Any]:
        for table, _ in sheets:
            self.ws.require(table)
        name = file_service.safe_filename(filename)
        if not name.lower().endswith(".xlsx"):
            name += ".xlsx"
        snapshot = None
        try:
            with session_scope() as db:
                library = recipe_service.library_for(db, self.customer_id)
            snapshot = compile_from_workspace(self.ws, [table for table, _ in sheets], library,
                                              name=f"导出 {name}", customer_id=self.customer_id,
                                              conversation_id=self.conversation_id, field_mapping=self.field_mapping(),
                                              file_resolver=self.file_dependency)
            for spec, (_, sheet) in zip(snapshot.output_spec, sheets):
                spec.name = sheet
        except ValueError:
            # 临时查询仍可导出；没有执行血缘的结果不能冒充完整流程。
            pass
        with session_scope() as db:
            run = run_service.create_run(db, kind="analysis", user_id=self.user_id, customer_id=self.customer_id,
                                         conversation_id=self.conversation_id, request_text=f"导出 {name}")
            path = run_service.output_path(run, name)
            written = file_service.export_tables(self.ws, sheets, path)
            record = file_service.register_output(db, path=path, filename=name, customer_id=self.customer_id,
                                                  conversation_id=self.conversation_id, run_id=run.id, user_id=self.user_id)
            run.status = "success"
            run.output_file_id = record.id
            run.finished_at = datetime.now()
            if snapshot is not None:
                snapshot.metadata["last_run"] = {"run_id": run.id, "status": "success"}
                run.recipe_id, run.recipe_version = snapshot.id, snapshot.version
                run.validation = {"recipe_snapshot": snapshot.to_dict(), "plain_export": True}
                self._last_recipe = snapshot
            return {**file_service.file_dict(record), "sheets_written": written}

    def file_dependency(self, file_id: str) -> FileDependency:
        """保留有输入的处理链；从零生成的文件作为后续处理的可替换输入。"""
        with session_scope() as db:
            record = file_service.get_file(db, file_id)
            if record.conversation_id != self.conversation_id and not (self.customer_id and record.customer_id == self.customer_id):
                raise ServiceError("这个文件不属于当前客户或会话")
            dependency = FileDependency(record.id, record.filename)
            run = db.get(Run, record.run_id) if record.run_id else None
            snapshot = (run.validation or {}).get("recipe_snapshot") if run else None
            if not isinstance(snapshot, dict):
                if run is not None and run.kind == "script":
                    legacy_run_id = run.id
                elif record.kind == "output":
                    raise ServiceError("这个结果文件缺少生成步骤，不能将历史输出当作原始输入保存流程")
                else:
                    return dependency
            else:
                legacy_run_id = None
            if legacy_run_id is None:
                recipe = Recipe.model_validate(snapshot)
                if not recipe.inputs:
                    # 样例或模板的生成过程不属于后续文件处理流程；重放时应读取新文件，
                    # 而不是重新执行字面量造数、固定使用上次样例。
                    if run.status not in {"success", "warning"}:
                        raise ServiceError("这个文件的生成记录未成功，不能作为重复流程的输入")
                    dependency.replaceable_source = True
                    return dependency
                validation = run.validation or {}
                outputs = [*validation.get("outputs", []), *validation.get("files", [])]
                match = next((item for item in outputs if item.get("file_id") == file_id and item.get("ref")), None)
                if match:
                    dependency.recipe, dependency.ref = recipe, str(match["ref"])
                else:
                    dependency.recipe, dependency.ref = with_workbook_export(recipe, record.filename, plain=bool(validation.get("plain_export")))
                return dependency
        from sheetmate.services.workflow_capture import capture_legacy_script

        capture_legacy_script(self, legacy_run_id)
        return self.file_dependency(file_id)

    def executed_recipe(self, run_id: str) -> Recipe | None:
        with session_scope() as db:
            run = run_service.get_run(db, run_id)
            if run.conversation_id != self.conversation_id:
                raise ServiceError("这次执行不属于当前会话")
            if (run.validation or {}).get("recipe_deleted"):
                raise ServiceError("这个流程已删除")
            snapshot = (run.validation or {}).get("recipe_snapshot")
            if (run.validation or {}).get("workflow_save_error"):
                raise ServiceError(f"本次处理的依赖记录不完整：{run.validation['workflow_save_error']}")
            if isinstance(snapshot, dict):
                return Recipe.model_validate(snapshot)
            legacy = run.kind == "script"
        if legacy:
            from sheetmate.services.workflow_capture import capture_legacy_script

            return capture_legacy_script(self, run_id)
        return None

    def create_workbook(self, spec: dict[str, Any], filename: str) -> dict[str, Any]:
        from sheetmate.agent.creation import prepare_workbook_spec

        request = dict(spec)
        base_id = request.pop("base_file_id", None)
        saved = self.created_workbook_spec(str(base_id)) if base_id is not None else None
        prepared = prepare_workbook_spec(self.ws, request, base=saved["spec"] if saved else None)
        name = file_service.safe_filename(filename)
        if not name.lower().endswith(".xlsx"):
            name += ".xlsx"
        path: Path | None = None
        written_output = False
        tables: list[TableInfo] = []
        try:
            with session_scope() as db:
                run = run_service.create_run(db, kind="create", user_id=self.user_id, customer_id=self.customer_id,
                                             conversation_id=self.conversation_id, request_text=f"新建 {name}")
                path = run_service.output_path(run, name)
                written = write_created_workbook(path, prepared)
                written_output = True
                record = file_service.register_output(db, path=path, filename=name, customer_id=self.customer_id,
                                                      conversation_id=self.conversation_id, run_id=run.id, user_id=self.user_id)
                warnings: list[str] = []
                try:
                    tables = file_service.load_into_workspace(self.ws, record)
                    for table in tables:
                        self.ws.update_meta(table.name, created_by="create_workbook")
                except ReadError:
                    tables = []
                    warnings.append("空白工作簿已生成；尚无可供分析的数据表。")
                run.status = "success"
                run.output_file_id = record.id
                run.tool_sequence = "create_workbook"
                run.steps = [{"tool": "create_workbook", "label": f"新建 {name}", "ok": True,
                              "params": {"filename": name, **spec}, "workbook_spec": prepared.model_dump(mode="json"),
                              "summary": f"新建 {len(written)} 个工作表"}]
                run.summary = f"已新建「{name}」，包含 {len(written)} 个工作表。"
                run.finished_at = datetime.now().replace(microsecond=0)
                result = {**file_service.file_dict(record), "sheets_written": written,
                          "table_names": [table.name for table in tables], "warnings": warnings}
            return result
        except Exception:
            for table in tables:
                self.ws.drop(table.name)
            if written_output and path is not None:
                path.unlink(missing_ok=True)
            raise

    def created_workbook_spec(self, file_id: str) -> dict[str, Any]:
        """仅访问当前会话或已授权客户的建表快照，不把任意上传件当作建表模板。"""
        with session_scope() as db:
            record = file_service.get_file(db, file_id)
            if record.conversation_id != self.conversation_id and not (self.customer_id and record.customer_id == self.customer_id):
                raise ServiceError("这个文件不属于当前客户或会话")
            run = db.get(run_service.Run, record.run_id) if record.run_id else None
            if record.kind != "output" or run is None or run.kind != "create" or run.output_file_id != record.id:
                raise ServiceError("这个文件没有本建表技能保存的布局，请读取资料后新建，或用修改流程处理已有表")
            saved = next((step["workbook_spec"] for step in run.steps or [] if isinstance(step.get("workbook_spec"), dict)), None)
            if saved is None:
                raise ServiceError("未找到这份建表文件的布局快照")
            return {"file": file_service.file_dict(record), "spec": saved}

    # ------------------------------------------------------------------
    # Python 脚本
    # ------------------------------------------------------------------

    def run_python_script(self, *, code: str, description: str, input_tables: list[str], input_files: dict[str, str], cancel_event: Any) -> dict[str, Any]:
        from sheetmate.services.script_execution import execute_for_conversation

        return execute_for_conversation(self, code=code, description=description, input_tables=input_tables,
                                        input_files=input_files, cancel_event=cancel_event)

    def inspect_python_script(self, run_id: str) -> dict[str, Any]:
        from sheetmate.services.script_execution import inspect_for_conversation

        return inspect_for_conversation(self, run_id)

    # ------------------------------------------------------------------
    # Recipe
    # ------------------------------------------------------------------

    def list_recipes(self, *, include_deprecated: bool = False) -> list[dict[str, Any]]:
        with session_scope() as db:
            records = recipe_service.latest_records(db, customer_id=self.customer_id, include_deprecated=include_deprecated) if self.customer_id else []
            records += [r for r in recipe_service.latest_records(db, conversation_id=self.conversation_id, include_deprecated=include_deprecated)
                        if r.recipe_id not in {x.recipe_id for x in records}]
            briefs = [recipe_service.recipe_brief(r) for r in records]
            recipes = [recipe_service.record_to_recipe(r) for r in records]
        candidates = [Candidate(key=t.name, columns=t.column_names, filename=t.filename, sheet=t.sheet)
                      for t in self.ws.list_tables() if t.source == "uploaded" and t.role == "input"]
        mapping = self.field_mapping()
        for brief, recipe in zip(briefs, recipes):
            if candidates and recipe.inputs:
                hits = match_inputs(recipe, candidates, mapping)
                if len(hits) == len(recipe.inputs):
                    brief["match"] = "；".join(f"{alias}←{hit['key']}" for alias, hit in hits.items())
        return briefs

    def get_recipe(self, recipe_id: str) -> Recipe | None:
        pending = self._recent_recipe(recipe_id)
        with session_scope() as db:
            try:
                record = recipe_service.get_record(db, recipe_id)
                if record.conversation_id != self.conversation_id and not (self.customer_id and record.customer_id == self.customer_id):
                    return pending
                recipe = recipe_service.get_recipe(db, recipe_id)
            except NotFound:
                return pending
        if recipe.customer_id and recipe.customer_id != self.customer_id and recipe.learned_from.get("conversation_id") != self.conversation_id:
            return None
        return pending if pending is not None and pending.version >= recipe.version else recipe

    def latest_conversation_recipe(self) -> Recipe | None:
        recent = self._recent_recipe()
        if recent is not None:
            return recent
        with session_scope() as db:
            records = recipe_service.latest_records(db, conversation_id=self.conversation_id)
            return recipe_service.record_to_recipe(records[0]) if records else None

    def _recent_recipe(self, recipe_id: str | None = None) -> Recipe | None:
        if self._last_recipe is not None and (recipe_id is None or self._last_recipe.id == recipe_id):
            last_run_id = (self._last_recipe.metadata.get("last_run") or {}).get("run_id")
            with session_scope() as db:
                last_run = db.get(Run, last_run_id) if last_run_id else None
                deleted = last_run is not None and (last_run.validation or {}).get("recipe_deleted")
            if not deleted:
                return self._last_recipe.model_copy(deep=True)
            self._last_recipe = None
        # 执行快照只供本会话追问；不会进入客户的可重复流程目录。
        legacy_run_id = None
        with session_scope() as db:
            query = select(Run).where(Run.conversation_id == self.conversation_id,
                                      Run.kind.in_(("modify", "replay", "script", "analysis"))).order_by(Run.finished_at.desc(), Run.created_at.desc())
            if recipe_id is not None:
                query = query.where(Run.recipe_id == recipe_id)
            for run in db.scalars(query):
                if (run.validation or {}).get("recipe_deleted"):
                    continue
                if (run.validation or {}).get("workflow_save_error"):
                    raise ServiceError(f"最近一次处理的依赖记录不完整：{run.validation['workflow_save_error']}。请指定要保存的运行记录。")
                snapshot = (run.validation or {}).get("recipe_snapshot")
                if isinstance(snapshot, dict):
                    return Recipe.model_validate(snapshot)
                if run.kind == "script":
                    legacy_run_id = run.id
                    break
        if legacy_run_id:
            from sheetmate.services.workflow_capture import capture_legacy_script

            return capture_legacy_script(self, legacy_run_id)
        return None

    def store_recipe(self, recipe: Recipe, *, note: str = "") -> dict[str, Any]:
        if not self.save_recipe_requested:
            raise ServiceError("用户没有要求保存重复流程，本次只执行处理。")
        if note:
            recipe.metadata["version_note"] = note
        recipe.customer_id = recipe.customer_id or self.customer_id
        with session_scope() as db:
            last_run_id = (recipe.metadata.get("last_run") or {}).get("run_id")
            last_run = db.get(Run, last_run_id) if last_run_id else None
            if last_run is not None and (last_run.validation or {}).get("recipe_deleted"):
                raise ServiceError("这个流程已删除，不能从历史快照重新保存。")
            record = recipe_service.save_recipe(db, recipe, conversation_id=self.conversation_id)
            brief = recipe_service.recipe_brief(record)
            if last_run is not None and last_run.conversation_id == self.conversation_id and last_run.recipe_id == recipe.id:
                last_run.recipe_version = recipe.version
                last_run.validation = {**(last_run.validation or {}), "recipe_snapshot": recipe.to_dict()}
        self._last_recipe = recipe.model_copy(deep=True)
        self.saved_recipes.append(brief)
        return brief

    def request_recipe_deletion(self, recipe_ids: list[str]) -> dict[str, Any]:
        from sheetmate.services.recipe_deletion import prepare_confirmation

        with session_scope() as db:
            user = db.get(User, self.user_id)
            if user is None:
                raise ServiceError("当前用户不存在")
            return prepare_confirmation(db, user, self.conversation_id, recipe_ids)

    def delete_recipe(self, recipe_id: str) -> dict[str, Any]:
        return self.delete_recipes_confirmed([recipe_id])[0]

    def delete_recipes_confirmed(self, recipe_ids: list[str]) -> list[dict[str, Any]]:
        targets = list(dict.fromkeys(recipe_ids))
        if not targets:
            return []
        if not set(targets) <= self.delete_recipe_ids:
            raise ServiceError("请先确认要删除的流程清单，确认后即可执行。")
        with session_scope() as db:
            user = db.get(User, self.user_id)
            if user is None:
                raise ServiceError("当前用户不存在")
            for recipe_id in targets:
                record = recipe_service.get_record(db, recipe_id)
                if record.conversation_id != self.conversation_id and not (self.customer_id and record.customer_id == self.customer_id):
                    raise ServiceError("这个流程不属于当前客户或会话")
            deleted = [recipe_service.delete_recipe(db, recipe_id, user=user) for recipe_id in targets]
        if self._last_recipe is not None and self._last_recipe.id in targets:
            self._last_recipe = None
        self.saved_recipes[:] = [recipe for recipe in self.saved_recipes if recipe["recipe_id"] not in targets]
        self.delete_recipe_ids.difference_update(targets)
        self.deleted_recipes.extend(deleted)
        return deleted

    # ---- PipelineHost ----

    def pipeline_host(self) -> ConversationHost:
        return self

    def open_run(self, kind: str, recipe: Recipe, request_text: str = "") -> RunHandle:
        input_files = [t.file_id for t in self.ws.list_tables() if t.file_id and t.role == "input"]
        with session_scope() as db:
            run = run_service.create_run(db, kind=kind, user_id=self.user_id, customer_id=self.customer_id,
                                         conversation_id=self.conversation_id, recipe_id=recipe.id, recipe_version=recipe.version,
                                         request_text=request_text, input_file_ids=list(dict.fromkeys(input_files)))
            filename = file_service.safe_filename(f"{recipe.name}_结果.xlsx")
            path = run_service.output_path(run, filename)
        return RunHandle(run_id=run.id, ws=run_service.run_workspaces.get(run.id), output_path=path)

    def finish_run(self, handle: RunHandle, recipe: Recipe, report: dict[str, Any], *,
                   verification: dict[str, Any] | None = None, diff: dict[str, Any] | None = None,
                   register_output: bool = True) -> dict[str, Any] | None:
        with session_scope() as db:
            record: FileRecord | None = None
            delivered: list[dict[str, Any]] = []
            metrics = None
            if register_output and has_reviewable_output(report):
                run = db.get(Run, handle.run_id)
                assert run is not None
                artifacts = [dict(item) for item in report.get("files", [])]
                paths = {str(item["path"]) for item in report.get("artifacts", [])}
                paths.update(str(item["path"]) for item in artifacts)
                if handle.output_path.exists() and str(handle.output_path) not in paths:
                    suffix = ("_结果" if report.get("status") in {"success", "warning"} else "_结果_未通过校验") + handle.output_path.suffix
                    artifacts.append({"path": str(handle.output_path), "name": f"{recipe.name}{suffix}"})
                delivered = run_service.register_delivery_files(db, run, artifacts, user_id=self.user_id)
                if delivered:
                    record = db.get(FileRecord, delivered[-1]["file_id"])
                for artifact in artifacts:
                    path = Path(artifact["path"])
                    try:
                        from sheetmate.engine.reader import read_workbook

                        metrics = run_service.metrics_of({s.name: s.df for s in read_workbook(path)})
                    except Exception:
                        pass
            persisted_report = {**report, "files": delivered} if register_output else report
            run_service.finish_run(db, handle.run_id, persisted_report, verification=verification,
                                   output_file_id=record.id if record else None, diff=diff, outputs_metrics=metrics)
            if register_output:
                snapshot = recipe.model_copy(deep=True)
                snapshot.metadata["last_run"] = {"run_id": handle.run_id, "status": report.get("status")}
                snapshot.metadata["verification"] = verification
                snapshot.learned_from["input_bindings"] = {
                    item["alias"]: {"file_id": item.get("file_id") or (item.get("source") or {}).get("file_id"),
                                    "sheet": item.get("sheet")} for item in report.get("inputs", [])}
                recipe.learned_from["input_bindings"] = dict(snapshot.learned_from["input_bindings"])
                run = db.get(Run, handle.run_id)
                assert run is not None
                actual_inputs = [i.get("file_id") or (i.get("source") or {}).get("file_id") for i in report.get("inputs", [])]
                run.input_file_ids = list(dict.fromkeys(fid for fid in actual_inputs if fid))
                run.validation = {**(run.validation or {}), "recipe_snapshot": snapshot.to_dict()}
                run.validation = {**run.validation, "files": delivered}
                if not run.validation.get("recipe_deleted"):
                    self._last_recipe = snapshot
                elif self._last_recipe is not None and self._last_recipe.id == recipe.id:
                    self._last_recipe = None
            public = [{key: value for key, value in item.items() if key != "path"} for item in delivered]
            return {**public[-1], "files": public} if public else None

    def save_recipe(self, recipe: Recipe, *, verification: dict[str, Any] | None, run_report: dict[str, Any]) -> dict[str, Any]:
        """流程闭环结束后保存：执行成功且（有人工结果时）验证通过 → active，否则 draft。"""
        ok = run_report.get("status") in {"success", "warning"}
        verified = verification is None or verification.get("status") in {"pass", "warn"}
        recipe.status = "active" if ok and verified else "draft"  # type: ignore[assignment]
        recipe.customer_id = recipe.customer_id or self.customer_id
        note = "AI 生成" if recipe.version <= 1 else "AI 修订"
        return self.store_recipe(recipe, note=note)

    def history_metrics(self, recipe_id: str) -> list[dict[str, Any]]:
        with session_scope() as db:
            return run_service.history_metrics(db, recipe_id)

    # ------------------------------------------------------------------
    # 记忆
    # ------------------------------------------------------------------

    def field_mapping(self) -> dict[str, list[str]]:
        with session_scope() as db:
            return memory_service.field_mapping_for(db, self.customer_id)

    def memory_text(self, query: str) -> str:
        with session_scope() as db:
            return memory_service.memory_text(db, user_id=self.user_id, customer_id=self.customer_id,
                                              conversation_id=self.conversation_id, query=query)

    def remember(self, category: str, key: str, value: str) -> str:
        with session_scope() as db:
            memory_service.set_memory(db, category=category, key=key, value=value, user_id=self.user_id,
                                      customer_id=self.customer_id, conversation_id=self.conversation_id)
        return "客户记忆" if self.customer_id else "本会话记忆"

    def save_field_mapping(self, standard: str, aliases: list[str]) -> str:
        if not self.customer_id:
            raise ServiceError("字段映射是按客户保存的：请先把会话绑定到客户")
        with session_scope() as db:
            row = memory_service.upsert_field_mapping(db, self.customer_id, standard, aliases, source="ai")
            return f"已保存字段映射：{row.standard} ← {'、'.join(row.aliases)}"
