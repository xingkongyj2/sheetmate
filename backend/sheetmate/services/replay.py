"""重复执行（客户页「用新文件执行」）与断点续跑。

- match：新文件的各个 sheet 分别对应流程的哪个输入（自动匹配，用户可以手动调整）
- start_replay：用新文件执行流程，正常情况下不调用 AI；出错且允许修复时才让 AI 修
- start_rerun_from：在原来的运行工作区里从第 n 步重跑（可以先改这一步的参数），复用前面算好的中间表
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sheetmate.agent.llm import LLMError, get_llm
from sheetmate.agent.repair import LLMStepRepairer
from sheetmate.config import settings
from sheetmate.db.database import session_scope
from sheetmate.db.models import FileRecord, User
from sheetmate.recipe.executor import InputSource, RecipeRunner, has_reviewable_output
from sheetmate.recipe.matcher import Candidate, match_inputs
from sheetmate.recipe.schema import Recipe
from sheetmate.services import customers as customer_service
from sheetmate.services import files as file_service
from sheetmate.services import memory as memory_service
from sheetmate.services import recipes as recipe_service
from sheetmate.services import runs as run_service
from sheetmate.services.auth import require_customer
from sheetmate.services.errors import NotFound, ServiceError
from sheetmate.services.jobs import Job, jobs


def _candidates(records: list[FileRecord]) -> list[Candidate]:
    items = []
    for record in records:
        for sheet in (record.profile or {}).get("sheets", []):
            items.append(Candidate(key=f"{record.id}::{sheet['sheet']}", columns=[c["name"] for c in sheet.get("columns", [])],
                                   filename=record.filename, sheet=sheet["sheet"], rows=sheet.get("rows", 0)))
    return items


def _file_path(file_id: str) -> Path | None:
    with session_scope() as db:
        try:
            return Path(file_service.get_file(db, file_id).stored_path)
        except NotFound:
            return None


def _load_recipe_for_user(db, user: User, recipe_id: str) -> Recipe:
    recipe = recipe_service.get_recipe(db, recipe_id)
    if recipe.customer_id:
        require_customer(db, user, recipe.customer_id)
    return recipe


def match(user: User, recipe_id: str, file_ids: list[str]) -> dict[str, Any]:
    with session_scope() as db:
        recipe = _load_recipe_for_user(db, user, recipe_id)
        records = [file_service.get_file(db, fid) for fid in file_ids]
        mapping = memory_service.field_mapping_for(db, recipe.customer_id)
        candidates = _candidates(records)
        hits = match_inputs(recipe, candidates, mapping)
        return {
            "recipe_id": recipe.id,
            "inputs": [
                {"alias": i.alias, "description": i.description, "file_pattern": i.file_pattern,
                 "required_columns": i.required_columns, "match": hits.get(i.alias)}
                for i in recipe.inputs
            ],
            "candidates": [{"key": c.key, "filename": c.filename, "sheet": c.sheet, "rows": c.rows} for c in candidates],
        }


def start_replay(user: User, recipe_id: str, *, file_ids: list[str], inputs: dict[str, str] | None = None,
                 allow_repair: bool = True, version: int | None = None, save_recipe: bool = False) -> Job:
    with session_scope() as db:
        recipe = recipe_service.get_recipe(db, recipe_id, version)
        if recipe.customer_id:
            require_customer(db, user, recipe.customer_id)
        records = {fid: file_service.get_file(db, fid) for fid in file_ids}
        mapping = memory_service.field_mapping_for(db, recipe.customer_id)
        chosen: dict[str, str] = dict(inputs or {})
        pending = [i for i in recipe.inputs if i.alias not in chosen]
        if pending:
            hits = match_inputs(Recipe(name=recipe.name, inputs=pending), _candidates(list(records.values())), mapping)
            chosen.update({alias: hit["key"] for alias, hit in hits.items()})
        missing = [i.alias for i in recipe.inputs if i.alias not in chosen]
        if missing:
            raise ServiceError(f"这些输入没有找到对应的文件：{missing}。请上传对应文件或手动指定")
        sources: dict[str, InputSource] = {}
        for alias, key in chosen.items():
            file_id, _, sheet = key.partition("::")
            record = records.get(file_id) or file_service.get_file(db, file_id)
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
        snapshot = (run.validation or {}).get("recipe_snapshot")
        recipe = Recipe.model_validate(snapshot) if isinstance(snapshot, dict) else recipe_service.get_recipe(db, run.recipe_id, run.recipe_version)
        library = recipe_service.library_for(db, recipe.customer_id)
        mapping = memory_service.field_mapping_for(db, recipe.customer_id)
        if not (settings.runs_dir / f"{run_id}.duckdb").exists():
            raise NotFound("这次运行的中间数据已经不在了，请重新执行")
        out_path = run_service.output_path(run, file_service.safe_filename(f"{recipe.name}_结果_重跑.xlsx"))
        # 断点续跑只重算第 step_id 步及之后；前面步骤的明细和输入信息沿用原记录
        kept_steps = [s for s in (run.steps or []) if int(s.get("step_id") or 0) < step_id]
        kept_inputs = (run.validation or {}).get("inputs", [])
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
        runner.restore_sources(kept_inputs, _file_path)  # 没重新读输入文件：用上次记下的位置恢复「按原样式输出」
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
            saved = recipe_service.recipe_brief(recipe_service.save_recipe(db, runner.recipe, status="draft"))
            report["recipe_version"] = saved["version"]
        record = None
        metrics = None
        if out_path.exists() and has_reviewable_output(report):
            suffix = ("_结果" if report["status"] in {"success", "warning"} else "_结果_未通过校验") + out_path.suffix
            record = file_service.register_output(db, path=out_path, filename=f"{runner.recipe.name}{suffix}",
                                                  customer_id=run.customer_id, conversation_id=run.conversation_id,
                                                  run_id=run.id, user_id=user_id)
            metrics = run_service.metrics_of(runner.collect_outputs())
        run_service.finish_run(db, run.id, report, output_file_id=record.id if record else None, outputs_metrics=metrics)
        snapshot = runner.recipe.model_copy(deep=True)
        snapshot.metadata["last_run"] = {"run_id": run.id, "status": report.get("status")}
        run.validation = {**(run.validation or {}), "recipe_snapshot": snapshot.to_dict()}
        file_info = file_service.file_dict(record) if record else None
    if file_info:
        await job.emit({"type": "file", **file_info, "run_id": report["run_id"]})
    if saved:
        await job.emit({"type": "recipe_saved", **saved, "flow": runner.recipe.flow()})
        customer_service.schedule_summary_refresh(runner.recipe.customer_id)
    await job.emit({"type": "done", "run_id": report["run_id"], "status": report["status"]})
