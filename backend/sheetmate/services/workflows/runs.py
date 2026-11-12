"""运行记录（任务流水）与运行工作区。

每次运行一个独立的 DuckDB 文件（data/runs/{run_id}.duckdb），所有中间表都保留：
可以单步查看每一步的输入/输出，也可以从某一步断点续跑（复用前面已经算好的中间表）。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from sheetmate.core.config.settings import settings
from sheetmate.db.models import FileRecord, Run, new_id, now
from sheetmate.engine.tables.workspace import WorkspaceManager
from sheetmate.engine.workbooks.atomic import link_or_copy
from sheetmate.engine.scripts.references import bind_script_references, externalize_script_references
from sheetmate.recipe.validation.rules import output_metrics
from sheetmate.core.errors import NotFound

run_workspaces = WorkspaceManager(settings.runs_dir, max_open=12)

STATUS_LABELS = {
    "running": "运行中", "success": "成功", "warning": "完成（有提示）", "failed": "失败", "escalated": "需要人工处理",
    "incomplete": "未完成", "cancelled": "已停止", "interrupted": "已中断", "waiting": "等待确认",
}
KIND_LABELS = {"analysis": "分析", "modify": "生成流程", "replay": "重复执行", "create": "建表", "script": "Python 脚本"}


def create_run(db: Session, *, kind: str, user_id: int | None, customer_id: str | None, conversation_id: str | None,
               recipe_id: str | None = None, recipe_version: int | None = None, request_text: str = "",
               input_file_ids: list[str] | None = None) -> Run:
    run = Run(id=new_id("run"), kind=kind, user_id=user_id, customer_id=customer_id, conversation_id=conversation_id,
              recipe_id=recipe_id, recipe_version=recipe_version, request_text=request_text[:2000],
              input_file_ids=input_file_ids or [], status="running", created_at=datetime.now())
    db.add(run)
    db.flush()
    return run


def output_path(run: Run, filename: str) -> Path:
    owner = run.customer_id or (f"conv_{run.conversation_id}" if run.conversation_id else "shared")
    folder = settings.outputs_dir / owner
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{run.id}_{filename}"


def register_delivery_files(db: Session, run: Run, artifacts: list[dict[str, Any]], *, user_id: int | None,
                            delivery_status: str = "final",
                            sheet_cache: dict[str, list[Any]] | None = None) -> list[dict[str, Any]]:
    """把正式交付复制到独立输出件，保留内部文件供后续步骤或断点重跑使用。

    每个交付文件只解析一次：登记画像时读到的工作表交给调用方的 sheet_cache（键为源文件路径），
    随后统计指标等需要内容的步骤直接复用，不必再整文件读取一遍。
    """
    from sheetmate.services.files import service as file_service

    copied: list[Path] = []
    deliveries: list[dict[str, Any]] = []
    seen: set[Path] = set()
    try:
        for artifact in artifacts:
            source_path = Path(artifact["path"])
            if source_path.resolve() in seen:
                continue
            seen.add(source_path.resolve())
            name = file_service.safe_filename(artifact.get("name") or source_path.name)
            path = output_path(run, f"{uuid4().hex[:8]}_{name}")
            copied.append(path)
            link_or_copy(source_path, path)  # 写出路径均为原子替换，交付件与内部文件可共用同一份数据
            sheets = None
            try:
                sheets = file_service.read_file_sheets(path)
            except Exception:
                sheets = None  # 读取失败（损坏、图片等非表格文件）交给登记按原有规则处理
            record = file_service.register_output(db, path=path, filename=name, customer_id=run.customer_id,
                                                  conversation_id=run.conversation_id, run_id=run.id, user_id=user_id,
                                                  delivery_status=delivery_status, sheets=sheets)
            if sheets is not None and sheet_cache is not None:
                sheet_cache[str(source_path)] = sheets
            record.profile = {**(record.profile or {}), "source_path": str(source_path.resolve())}
            deliveries.append({**artifact, **file_service.file_dict(record)})
    except BaseException:
        for path in copied:
            path.unlink(missing_ok=True)
        raise
    return deliveries


def finish_run(db: Session, run_id: str, report: dict[str, Any], *, verification: dict[str, Any] | None = None,
               output_file_id: str | None = None, diff: dict[str, Any] | None = None, outputs_metrics: dict[str, float] | None = None) -> Run:
    run = db.get(Run, run_id)
    if run is None:
        raise NotFound("运行记录不存在")
    steps = deepcopy(report.get("steps", []))
    _retain_step_files(db, run, steps)
    run.status = report.get("status", "failed")
    if run.status == "running":
        run.status = "failed"
    run.steps = externalize_script_references([_slim_step(s) for s in steps])
    run.tool_sequence = " → ".join(str(s.get("formula")) for s in report.get("steps", []))
    validation = dict(report.get("validation") or {})
    if (run.validation or {}).get("recipe_deleted"):
        validation["recipe_deleted"] = True
    if (run.validation or {}).get("resumed_from"):
        validation["resumed_from"] = run.validation["resumed_from"]
    if outputs_metrics:
        validation["metrics"] = outputs_metrics
    if diff:
        validation["before_after"] = diff
    validation["inputs"] = report.get("inputs", [])
    validation["outputs"] = report.get("outputs", [])
    validation["artifacts"] = report.get("artifacts", [])
    validation["files"] = report.get("files", [])
    validation["checkpoints"] = report.get("validation_checkpoints", [])
    run.validation = externalize_script_references(validation)
    _bind_scripts(run)
    run.verification = verification or {}
    run.error = report.get("error", "")
    run.summary = report.get("escalation") or ""
    run.output_file_id = output_file_id
    run.duration_ms = int(report.get("duration_ms", 0))
    run.recipe_version = report.get("recipe_version") or run.recipe_version
    # 连续追问可能在同一秒内完成，保留微秒才能取到最后一次执行快照。
    run.finished_at = datetime.now()
    return run


def _retain_step_files(db: Session, run: Run, steps: list[dict[str, Any]]) -> None:
    """中间文件与最终交付分开登记，保留步骤归属，历史重跑不覆盖旧文件。"""
    from sheetmate.db.models import FileRecord
    from sheetmate.services.files import service as file_service

    existing = {record.profile["source_path"]: record for record in db.scalars(
        select(FileRecord).where(FileRecord.run_id == run.id)) if (record.profile or {}).get("source_path")}
    for step in steps:
        retained = []
        for artifact in step.get("output_files", []):
            if artifact.get("file_id"):
                retained.append(artifact)
                continue
            if not artifact.get("path") or not Path(artifact["path"]).is_file():
                continue
            source_path = str(Path(artifact["path"]).resolve())
            if source_path in existing:
                retained.append({**artifact, **file_service.file_dict(existing[source_path])})
                continue
            delivered = register_delivery_files(db, run, [artifact], user_id=run.user_id, delivery_status="intermediate")[0]
            record = db.get(FileRecord, delivered["file_id"])
            assert record is not None
            record.profile = {**(record.profile or {}), "intermediate": True,
                              "step_id": step.get("step_id"), "step_name": step.get("name", ""), "source_path": source_path}
            existing[source_path] = record
            retained.append({**artifact, **file_service.file_dict(record), "intermediate": True,
                             "step_id": step.get("step_id"), "step_name": step.get("name", "")})
        if retained:
            step["output_files"] = retained


def finish_analysis_run(db: Session, run_id: str, record: dict[str, Any]) -> None:
    run = db.get(Run, run_id)
    if run is None:
        return
    execution = record.get("execution") or {}
    status = execution.get("status")
    run.status = ("success" if status == "completed" and not record.get("error") else
                  status if status in {"incomplete", "cancelled", "interrupted", "waiting", "failed"} else
                  "failed" if record.get("error") else "incomplete")
    run.steps = externalize_script_references(record.get("steps", []))
    _bind_scripts(run)
    run.tool_sequence = record.get("tool_sequence", "")
    run.error = record.get("error", "")
    if run.status in {"failed", "incomplete", "interrupted", "cancelled"}:
        run.error = run.error or execution.get("summary", "")
    run.validation = {**(run.validation or {}), "execution": execution}
    run.summary = record.get("final_text", "")[:1000]
    run.duration_ms = int(record.get("duration_ms", 0))
    run.output_file_id = (record.get("files") or [None])[-1]
    run.finished_at = now()


def _slim_step(step: dict[str, Any]) -> dict[str, Any]:
    """入库时只保留预览的前 20 行，避免数据库膨胀（完整数据在运行工作区里）。"""
    item = dict(step)
    preview = item.get("preview")
    if isinstance(preview, dict) and preview.get("rows"):
        item["preview"] = {**preview, "rows": preview["rows"][:20]}
    return item


def run_dict(run: Run, *, with_steps: bool = True) -> dict[str, Any]:
    if with_steps:
        steps = externalize_script_references(run.steps)
        validation = externalize_script_references(run.validation)
        if steps != run.steps:
            run.steps = steps
        if validation != run.validation:
            run.validation = validation
        _bind_scripts(run)
    data = {
        "run_id": run.id, "kind": run.kind, "kind_label": KIND_LABELS.get(run.kind, run.kind),
        "status": run.status, "status_label": STATUS_LABELS.get(run.status, run.status),
        "customer_id": run.customer_id, "conversation_id": run.conversation_id,
        "recipe_id": run.recipe_id, "recipe_version": run.recipe_version, "request_text": run.request_text,
        "tool_sequence": run.tool_sequence, "error": run.error, "summary": run.summary,
        "output_file_id": run.output_file_id, "duration_ms": run.duration_ms,
        "created_at": run.created_at.isoformat(), "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "verification": {k: run.verification.get(k) for k in ("status", "overall_match_rate", "summary")} if run.verification else None,
    }
    if with_steps:
        data["steps"] = run.steps
        data["validation"] = run.validation
        data["verification_detail"] = run.verification
    # 运行快照保留当时的生成证据；展示时使用当前的交付状态，避免旧候选重新变成结果。
    db = object_session(run)
    if db is not None:
        snapshots = (run.validation or {}).get("files", [])
        step_files = [artifact for step in run.steps or [] for artifact in step.get("output_files", [])]
        ids = {artifact["file_id"] for artifact in [*snapshots, *step_files] if artifact.get("file_id")}
        if run.output_file_id:
            ids.add(run.output_file_id)
        records = {record.id: record for record in db.scalars(select(FileRecord).where(FileRecord.id.in_(ids)))} if ids else {}

        def current_file(artifact: dict[str, Any]) -> dict[str, Any]:
            from sheetmate.services.files.service import file_dict

            record = records.get(artifact.get("file_id"))
            return {**artifact, **file_dict(record)} if record else dict(artifact)

        def delivered(file_id: str | None) -> bool:
            record = records.get(file_id)
            profile = (record.profile or {}) if record else {}
            return record is not None and not profile.get("intermediate") and profile.get("delivery_status") not in {"candidate", "intermediate", "superseded"}

        if not delivered(run.output_file_id):
            data["output_file_id"] = next((item["file_id"] for item in reversed(snapshots)
                                           if delivered(item.get("file_id"))), None)
        if with_steps:
            data["validation"] = {**(run.validation or {}), "files": [current_file(item) for item in snapshots]}
            data["steps"] = [{**step, "output_files": [current_file(item) for item in step["output_files"]]}
                             if "output_files" in step else step for step in run.steps or []]
    return data


def _bind_scripts(run: Run) -> None:
    bind_script_references([run.steps, run.validation], user_id=run.user_id,
                           customer_id=run.customer_id, conversation_id=run.conversation_id)


def get_run(db: Session, run_id: str) -> Run:
    run = db.get(Run, run_id)
    if run is None:
        raise NotFound("运行记录不存在")
    return run


def list_runs(db: Session, *, customer_id: str | None = None, conversation_id: str | None = None,
              recipe_id: str | None = None, limit: int = 30) -> list[Run]:
    stmt = select(Run).order_by(Run.created_at.desc()).limit(limit)
    if customer_id:
        stmt = stmt.where(Run.customer_id == customer_id)
    if conversation_id:
        stmt = stmt.where(Run.conversation_id == conversation_id)
    if recipe_id:
        stmt = stmt.where(Run.recipe_id == recipe_id)
    return list(db.scalars(stmt))


def history_metrics(db: Session, recipe_id: str, limit: int = 12) -> list[dict[str, float]]:
    """同一流程以往成功运行的输出指标（historical_range 校验用）。"""
    runs = db.scalars(select(Run).where(Run.recipe_id == recipe_id, Run.status.in_(["success", "warning"]))
                      .order_by(Run.created_at.desc()).limit(limit))
    return [r.validation.get("metrics", {}) for r in runs if (r.validation or {}).get("metrics")]


def metrics_of(outputs: dict) -> dict[str, float]:
    return output_metrics(outputs)
