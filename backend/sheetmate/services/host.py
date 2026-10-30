"""Agent 工具宿主：把数据库、文件、Recipe 存储这些能力提供给 agent/tools.py 和 recipe/pipeline.py。

每个方法都开自己的短事务（session_scope），因为工具可能在后台线程里执行。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sheetmate.db.database import session_scope
from sheetmate.db.models import FileRecord
from sheetmate.engine.workspace import TableInfo, Workspace
from sheetmate.recipe.matcher import Candidate, match_inputs
from sheetmate.recipe.executor import has_reviewable_output
from sheetmate.recipe.pipeline import RunHandle
from sheetmate.recipe.schema import Recipe
from sheetmate.services import files as file_service
from sheetmate.services import memory as memory_service
from sheetmate.services import recipes as recipe_service
from sheetmate.services import runs as run_service
from sheetmate.services.errors import NotFound, ServiceError


class ConversationHost:
    """一次对话请求的宿主（也实现 PipelineHost 协议）。"""

    def __init__(self, *, user_id: int, conversation_id: str, customer_id: str | None, ws: Workspace):
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.customer_id = customer_id
        self.ws = ws
        self.saved_recipes: list[dict[str, Any]] = []

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
                                              kinds=("input", "expected"))
            result = []
            for r in records:
                item = file_service.file_dict(r)
                item["tables"] = loaded.get(r.id, [])
                item["scope_label"] = "本会话" if r.conversation_id == self.conversation_id else "客户资料"
                result.append(item)
            return result

    def load_file(self, file_id: str) -> list[TableInfo]:
        with session_scope() as db:
            record = file_service.get_file(db, file_id)
            if record.customer_id != self.customer_id and record.conversation_id != self.conversation_id:
                raise ServiceError("这个文件不属于当前客户或会话")
            return file_service.load_into_workspace(self.ws, record)

    def export_excel(self, sheets: list[tuple[str, str]], filename: str) -> dict[str, Any]:
        for table, _ in sheets:
            self.ws.require(table)
        name = file_service.safe_filename(filename)
        if not name.lower().endswith(".xlsx"):
            name += ".xlsx"
        with session_scope() as db:
            run = run_service.create_run(db, kind="analysis", user_id=self.user_id, customer_id=self.customer_id,
                                         conversation_id=self.conversation_id, request_text=f"导出 {name}")
            path = run_service.output_path(run, name)
            written = file_service.export_tables(self.ws, sheets, path)
            record = file_service.register_output(db, path=path, filename=name, customer_id=self.customer_id,
                                                  conversation_id=self.conversation_id, run_id=run.id, user_id=self.user_id)
            run.status = "success"
            run.output_file_id = record.id
            run.finished_at = datetime.now().replace(microsecond=0)
            return {**file_service.file_dict(record), "sheets_written": written}

    # ------------------------------------------------------------------
    # Recipe
    # ------------------------------------------------------------------

    def list_recipes(self) -> list[dict[str, Any]]:
        with session_scope() as db:
            records = recipe_service.latest_records(db, customer_id=self.customer_id) if self.customer_id else []
            records += [r for r in recipe_service.latest_records(db, conversation_id=self.conversation_id)
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
        with session_scope() as db:
            try:
                recipe = recipe_service.get_recipe(db, recipe_id)
            except NotFound:
                return None
        if recipe.customer_id and recipe.customer_id != self.customer_id and recipe.learned_from.get("conversation_id") != self.conversation_id:
            return None
        return recipe

    def latest_conversation_recipe(self) -> Recipe | None:
        with session_scope() as db:
            records = recipe_service.latest_records(db, conversation_id=self.conversation_id)
            return recipe_service.record_to_recipe(records[0]) if records else None

    def store_recipe(self, recipe: Recipe, *, note: str = "") -> dict[str, Any]:
        if note:
            recipe.metadata["version_note"] = note
        recipe.customer_id = recipe.customer_id or self.customer_id
        with session_scope() as db:
            record = recipe_service.save_recipe(db, recipe, conversation_id=self.conversation_id)
            brief = recipe_service.recipe_brief(record)
        self.saved_recipes.append(brief)
        return brief

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
            metrics = None
            if register_output and handle.output_path.exists() and has_reviewable_output(report):
                suffix = "_结果.xlsx" if report.get("status") in {"success", "warning"} else "_结果_未通过校验.xlsx"
                record = file_service.register_output(db, path=handle.output_path, filename=f"{recipe.name}{suffix}",
                                                      customer_id=self.customer_id, conversation_id=self.conversation_id,
                                                      run_id=handle.run_id, user_id=self.user_id)
                try:
                    from sheetmate.engine.reader import read_workbook

                    metrics = run_service.metrics_of({s.name: s.df for s in read_workbook(handle.output_path)})
                except Exception:
                    metrics = None
            run_service.finish_run(db, handle.run_id, report, verification=verification,
                                   output_file_id=record.id if record else None, diff=diff, outputs_metrics=metrics)
            return {**file_service.file_dict(record), "run_id": handle.run_id} if record else None

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
