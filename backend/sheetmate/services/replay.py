"""重复执行（客户页「用新文件执行」）与断点续跑。

- match：新文件的各个 sheet 分别对应流程的哪个输入（自动匹配，用户可以手动调整）
- start_replay：用新文件执行流程，正常情况下不调用 AI；出错且允许修复时才让 AI 修
- start_rerun_from：在原来的运行工作区里从第 n 步重跑（可以先改这一步的参数），复用前面算好的中间表
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from sheetmate.agent.llm import LLMError, get_llm
from sheetmate.agent.repair import LLMStepRepairer
from sheetmate.config import settings
from sheetmate.db.database import session_scope
from sheetmate.db.models import Conversation, FileRecord, User
from sheetmate.recipe.executor import InputSource, RecipeRunner, has_reviewable_output
from sheetmate.recipe.matcher import Candidate, match_inputs
from sheetmate.recipe.schema import Recipe
from sheetmate.services import customers as customer_service
from sheetmate.services import files as file_service
from sheetmate.services import memory as memory_service
from sheetmate.services import recipes as recipe_service
from sheetmate.services import runs as run_service
from sheetmate.services.auth import require_customer
from sheetmate.services.errors import Forbidden, NotFound, ServiceError
from sheetmate.services.jobs import Job, jobs
from sheetmate.services.scripts import require_script_references


def _candidates(records: list[FileRecord]) -> list[Candidate]:
    items = []
    for record in records:
        items.append(Candidate(key=record.id, columns=[], filename=record.filename, kind="file"))
        for sheet in (record.profile or {}).get("sheets", []):
            items.append(Candidate(key=f"{record.id}::{sheet['sheet']}", columns=[c["name"] for c in sheet.get("columns", [])],
                                   filename=record.filename, sheet=sheet["sheet"], rows=sheet.get("rows", 0)))
    return items


def _file_for_user(db, user: User, file_id: str) -> FileRecord:
    record = file_service.get_file(db, file_id)
    if file_service.can_access_file(db, user, record):
        return record
    raise Forbidden("你没有权限访问这个文件")


def _file_path(file_id: str, user: User) -> Path | None:
    with session_scope() as db:
        try:
            return Path(_file_for_user(db, user, file_id).stored_path)
        except NotFound:
            return None


def _load_recipe_for_user(db, user: User, recipe_id: str, version: int | None = None) -> Recipe:
    record = recipe_service.get_record(db, recipe_id, version)
    require_script_references(db, user, record.body)
    recipe = recipe_service.get_recipe(db, recipe_id, version)
    require_script_references(db, user, recipe.to_dict())
    if recipe.customer_id:
        require_customer(db, user, recipe.customer_id)
    elif user.role != "admin":
        record = recipe_service.get_record(db, recipe_id)
        conv_id = record.conversation_id or recipe.learned_from.get("conversation_id")
        conv = db.get(Conversation, conv_id) if conv_id else None
        if conv_id and (conv is None or conv.user_id != user.id):
            raise Forbidden("你没有权限访问这个流程")
    return recipe


def match(user: User, recipe_id: str, file_ids: list[str]) -> dict[str, Any]:
    with session_scope() as db:
        recipe = _load_recipe_for_user(db, user, recipe_id)
        records = [_file_for_user(db, user, fid) for fid in dict.fromkeys(file_ids)]
        mapping = memory_service.field_mapping_for(db, recipe.customer_id)
        candidates = _candidates(records)
        hits = match_inputs(recipe, candidates, mapping)
        return {
            "recipe_id": recipe.id,
            "inputs": [
                {"alias": i.alias, "kind": i.kind, "description": i.description, "file_pattern": i.file_pattern,
                 "required_columns": i.required_columns, "match": hits.get(i.alias)}
                for i in recipe.inputs
            ],
            "candidates": [{"key": c.key, "kind": c.kind, "filename": c.filename, "sheet": c.sheet, "rows": c.rows} for c in candidates],
        }


def start_replay(user: User, recipe_id: str, *, file_ids: list[str], inputs: dict[str, str] | None = None,
                 allow_repair: bool = True, version: int | None = None, save_recipe: bool = False) -> Job:
    with session_scope() as db:
        recipe = _load_recipe_for_user(db, user, recipe_id, version)
        records = {fid: _file_for_user(db, user, fid) for fid in file_ids}
        mapping = memory_service.field_mapping_for(db, recipe.customer_id)
        chosen: dict[str, str] = dict(inputs or {})
        unknown = set(chosen) - set(recipe.input_aliases())
        if unknown:
            raise ServiceError(f"流程没有这些输入：{sorted(unknown)}")
        pending = [i for i in recipe.inputs if i.alias not in chosen]
        if pending:
            hits = match_inputs(Recipe(name=recipe.name, inputs=pending), _candidates(list(records.values())), mapping)
            chosen.update({alias: hit["key"] for alias, hit in hits.items()})
        missing = [i.alias for i in recipe.inputs if i.alias not in chosen]
        if missing:
            raise ServiceError(f"这些输入没有找到对应的文件：{missing}。请上传对应文件或手动指定")
        sources: dict[str, InputSource] = {}
        definitions = {i.alias: i for i in recipe.inputs}
        for alias, key in chosen.items():
            file_id, _, sheet = key.partition("::")
            record = records.get(file_id) or _file_for_user(db, user, file_id)
            records[file_id] = record
            if definitions[alias].kind == "file":
                sheet = ""
            elif sheet and not any(s.get("sheet") == sheet for s in (record.profile or {}).get("sheets", [])):
                raise ServiceError(f"文件「{record.filename}」没有工作表「{sheet}」")
            sources[alias] = InputSource(alias=alias, path=Path(record.stored_path), sheet=sheet or None,
                                         file_id=record.id, filename=record.filename)
        library = recipe_service.library_for(db, recipe.customer_id)
        run = run_service.create_run(db, kind="replay", user_id=user.id, customer_id=recipe.customer_id, conversation_id=None,
                                     recipe_id=recipe.id, recipe_version=recipe.version,
                                     request_text=f"用新文件执行「{recipe.name}」", input_file_ids=list(records))
        out_path = run_service.output_path(run, file_service.safe_filename(f"{recipe.name}_结果.xlsx"))
        run_id = run.id
        history = run_service.history_metrics(db, recipe.id)

    async def work(job: Job) -> None:
        repairer = None
        if allow_repair:
            try:
                repairer = LLMStepRepairer(get_llm())
            except LLMError:
                repairer = None
        ws = run_service.run_workspaces.get(run_id)
        runner = RecipeRunner(recipe, library, ws, field_mapping=mapping, repairer=repairer,
                              max_repair=settings.repair_max_attempts, run_id=run_id)
        async for event in runner.run(sources, output_path=out_path, history=history):
            await job.emit(event)
        await _finish(job, user.id, runner, runner.output_path or out_path, note="执行时自动修复", save_recipe=save_recipe)

    return jobs.start(f"run:{run_id}", work)


def start_rerun_from(user: User, run_id: str, step_id: int, params: dict[str, Any] | None = None, *, save_recipe: bool = False) -> Job:
    with session_scope() as db:
        run = run_service.get_run(db, run_id)
        if not run.recipe_id:
            raise ServiceError("这条运行记录没有关联流程")
        if run.customer_id:
            require_customer(db, user, run.customer_id)
        elif user.role != "admin" and run.user_id != user.id:
            conv = db.get(Conversation, run.conversation_id) if run.conversation_id else None
            if conv is None or conv.user_id != user.id:
                raise Forbidden("你没有权限查看这条运行记录")
        snapshot = (run.validation or {}).get("recipe_snapshot")
        require_script_references(db, user, snapshot)
        require_script_references(db, user, params)
        if not isinstance(snapshot, dict):
            require_script_references(db, user, recipe_service.get_record(db, run.recipe_id, run.recipe_version).body)
        recipe = Recipe.model_validate(snapshot) if isinstance(snapshot, dict) else recipe_service.get_recipe(db, run.recipe_id, run.recipe_version)
        require_script_references(db, user, recipe.to_dict())
        library = recipe_service.library_for(db, recipe.customer_id)
        mapping = memory_service.field_mapping_for(db, recipe.customer_id)
        if not (settings.runs_dir / f"{run_id}.duckdb").exists():
            raise NotFound("这次运行的中间数据已经不在了，请重新执行")
        try:
            recipe.step(step_id)
        except KeyError as exc:
            raise ServiceError(f"流程没有第 {step_id} 步") from exc
        out_path = run_service.output_path(run, file_service.safe_filename(f"{recipe.name}_结果_重跑_{uuid4().hex[:8]}.xlsx"))
        # 断点续跑只重算第 step_id 步及之后；前面步骤的明细和输入信息沿用原记录
        kept_steps = [s for s in (run.steps or []) if int(s.get("step_id") or 0) < step_id]
        kept_inputs = (run.validation or {}).get("inputs", [])
        kept_artifacts = [a for a in (run.validation or {}).get("artifacts", []) if int(a.get("step_id") or 0) < step_id]
        run.status = "running"
    edited = False
    if params:
        step = recipe.step(step_id)
        recipe.steps = [s.model_copy(update={"params": params}) if s.id == step_id else s for s in recipe.steps]
        edited = step.params != params

    async def work(job: Job) -> None:
        ws = run_service.run_workspaces.get(run_id)
        runner = RecipeRunner(recipe, library, ws, field_mapping=mapping, repairer=None, run_id=run_id)
        runner.report["inputs"] = []
        runner.restore_sources(kept_inputs, lambda fid: _file_path(fid, user))
        runner.restore_artifacts(kept_artifacts)
        async for event in runner.run(None, start_step=step_id, output_path=out_path):
            await job.emit(event)
        if edited and runner.report["status"] not in {"failed", "escalated"}:
            runner.recipe_changed = True
        runner.report["steps"] = kept_steps + runner.report["steps"]
        runner.report["inputs"] = kept_inputs
        await _finish(job, user.id, runner, runner.output_path or out_path, note=f"手动调整第 {step_id} 步参数后重跑", save_recipe=save_recipe)

    return jobs.start(f"run:{run_id}", work)


async def _finish(job: Job, user_id: int, runner: RecipeRunner, out_path: Path, note: str, *, save_recipe: bool = False) -> None:
    """登记本次输出和执行快照；只有用户要求时才把变更存为重复流程。"""
    report = runner.report
    with session_scope() as db:
        run = run_service.get_run(db, report["run_id"])
        runner.recipe.metadata["last_run"] = {"run_id": run.id, "status": report.get("status")}
        saved = None
        if save_recipe and runner.recipe_changed and report["status"] in {"success", "warning"} and not (run.validation or {}).get("recipe_deleted"):
            runner.recipe.metadata["version_note"] = note
            saved = recipe_service.recipe_brief(recipe_service.save_recipe(db, runner.recipe, status="active"))
            report["recipe_version"] = saved["version"]
        metrics = None
        file_infos: list[dict[str, Any]] = []
        if has_reviewable_output(report):
            script_files = [dict(item) for item in report.get("files", []) if item.get("path")]
            artifact_paths = {str(Path(item["path"]).resolve()) for item in report.get("artifacts", []) if item.get("path")}
            # 脚本交付只认 runner 完整执行后公布的 files，不能发布失败步骤留下的半成品。
            if out_path.is_file() and str(out_path.resolve()) not in artifact_paths:
                suffix = ("_结果" if report["status"] in {"success", "warning"} else "_结果_未通过校验") + out_path.suffix
                record = file_service.register_output(db, path=out_path, filename=f"{runner.recipe.name}{suffix}",
                                                      customer_id=run.customer_id, conversation_id=run.conversation_id,
                                                      run_id=run.id, user_id=user_id)
                file_infos.append(file_service.file_dict(record))
            report["files"] = run_service.register_delivery_files(db, run, script_files, user_id=user_id)
            file_infos.extend(report["files"])
            report["files"] = file_infos
            metrics = run_service.metrics_of(runner.collect_outputs())
        run_service.finish_run(db, run.id, report, output_file_id=file_infos[-1]["file_id"] if file_infos else None, outputs_metrics=metrics)
        snapshot = runner.recipe.model_copy(deep=True)
        snapshot.metadata["last_run"] = {"run_id": run.id, "status": report.get("status")}
        snapshot.learned_from["input_bindings"] = {
            item["alias"]: {"file_id": item.get("file_id") or (item.get("source") or {}).get("file_id"),
                            "sheet": item.get("sheet")} for item in report.get("inputs", [])}
        run.validation = {**(run.validation or {}), "recipe_snapshot": snapshot.to_dict()}
    for file_info in file_infos:
        await job.emit({"type": "file", **{k: v for k, v in file_info.items() if k != "path"}, "run_id": report["run_id"]})
    if saved:
        await job.emit({"type": "recipe_saved", **saved, "flow": runner.recipe.flow()})
        customer_service.schedule_summary_refresh(runner.recipe.customer_id)
    await job.emit({"type": "done", "run_id": report["run_id"], "status": report["status"]})
