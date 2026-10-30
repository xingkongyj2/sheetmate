"""客户公式的管理：列表（带「被哪些流程用到」）、启用 / 停用、删除、实时检查、试运行。

保存公式在 services/recipes.py（save_formula）；这里是它的管理界面要用的那一层。

- 一个公式 = 参数声明 + {{参数}} SQL 模板；同 id 每保存一次升一个版本，流程总是用最新版本
- 被流程用到的公式不能停用 / 删除（否则那些流程会直接无法执行）
- 试运行：在临时 DuckDB 工作区（Workspace(None)，用完即丢）里，用客户的文件或粘贴的几行数据执行公式，返回结果预览
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from sheetmate.db.models import FormulaRecord
from sheetmate.engine.field_mapping import apply_field_mapping
from sheetmate.engine.formulas import BUILTIN_FORMULAS, FormulaError, FormulaLibrary
from sheetmate.engine.reader import ReadError, read_pasted_table, read_workbook
from sheetmate.engine.runner import apply_formula
from sheetmate.engine.sqlutil import sanitize_table_name
from sheetmate.engine.workspace import Workspace, WorkspaceError
from sheetmate.recipe.schema import check_recipe
from sheetmate.services import files as file_service
from sheetmate.services import memory as memory_service
from sheetmate.services import recipes as recipe_service
from sheetmate.services.errors import Conflict, Forbidden, NotFound, ServiceError

TRIAL_OUTPUT = "试运行结果"
TRIAL_TIMEOUT = 20.0  # 秒：试运行的 SQL 超过这个时间就中止
PREVIEW_ROWS = 50
STATUSES = {"active": "已启用", "deprecated": "已停用"}  # 草稿状态不在界面上开放


# ---------------------------------------------------------------------------
# 列表 + 使用情况
# ---------------------------------------------------------------------------


def formula_usage(db: Session, customer_id: str) -> dict[str, list[dict[str, Any]]]:
    """每个公式 id 被这个客户的哪些流程（最新版本、未停用）用到，用在第几步，以及这些步骤现在有没有问题。"""
    library = recipe_service.library_for(db, customer_id)
    usage: dict[str, list[dict[str, Any]]] = {}
    for record in recipe_service.latest_records(db, customer_id=customer_id):
        recipe = recipe_service.record_to_recipe(record)
        by_formula: dict[str, list[Any]] = {}
        for step in recipe.ordered_steps():
            by_formula.setdefault(step.formula, []).append(step)
        if not by_formula:
            continue
        problems = check_recipe(recipe, library)
        for formula_id, steps in by_formula.items():
            labels = [f"第 {s.id} 步「{s.name}」" for s in steps]
            usage.setdefault(formula_id, []).append({
                "recipe_id": record.recipe_id, "name": record.name, "version": record.version, "status": record.status,
                "steps": [{"id": s.id, "name": s.name} for s in steps],
                "problems": [p for p in problems if any(p.startswith(label) for label in labels)],
            })
    return usage


def list_with_usage(db: Session, customer_id: str) -> list[dict[str, Any]]:
    """客户的公式列表：每个公式只有最新版本，已停用的也在。"""
    usage = formula_usage(db, customer_id)
    items = []
    for record in recipe_service.latest_formula_records(db, customer_id):
        spec = recipe_service.record_to_formula(record)
        if spec is None:
            continue
        item = recipe_service.formula_dict(spec, "customer")
        builtin = BUILTIN_FORMULAS.get(spec.id)
        item.update(
            status_label=STATUSES.get(spec.status, recipe_service.STATUS_LABELS.get(spec.status, spec.status)),
            overrides_name=builtin.spec.name if builtin else None,
            used_by=usage.get(spec.id, []),
            created_by=record.created_by,
            updated_at=record.created_at.isoformat() if record.created_at else None,
        )
        items.append(item)
    return items


def get_item(db: Session, customer_id: str, formula_id: str) -> dict[str, Any]:
    for item in list_with_usage(db, customer_id):
        if item["id"] == formula_id:
            return item
    raise NotFound("这个客户没有这个公式")


def _latest_record(db: Session, customer_id: str, formula_id: str) -> FormulaRecord:
    record = next((r for r in recipe_service.latest_formula_records(db, customer_id) if r.formula_id == formula_id), None)
    if record is None:
        raise NotFound("这个客户没有这个公式")
    return record


def _ensure_unused(db: Session, customer_id: str, formula_id: str, action: str) -> None:
    used = formula_usage(db, customer_id).get(formula_id, [])
    if used:
        names = "、".join(f"「{u['name']}」" for u in used)
        raise Conflict(f"公式正在被流程{names}使用，{action}后这些流程就无法执行了。请先修改这些流程里用到它的步骤。")


# ---------------------------------------------------------------------------
# 启用 / 停用 / 删除
# ---------------------------------------------------------------------------


def set_status(db: Session, customer_id: str, formula_id: str, status: str) -> dict[str, Any]:
    if status not in STATUSES:
        raise ServiceError("状态只能是 active / deprecated")
    record = _latest_record(db, customer_id, formula_id)
    if status == "deprecated":
        _ensure_unused(db, customer_id, formula_id, "停用")
    record.status = status
    body = dict(record.body or {})
    body["status"] = status
    record.body = body
    db.flush()
    return get_item(db, customer_id, formula_id)


def delete_formula(db: Session, customer_id: str, formula_id: str) -> None:
    """删除公式（所有版本）。"""
    _latest_record(db, customer_id, formula_id)
    _ensure_unused(db, customer_id, formula_id, "删除")
    db.execute(delete(FormulaRecord).where(FormulaRecord.customer_id == customer_id, FormulaRecord.formula_id == formula_id))


# ---------------------------------------------------------------------------
# 实时检查
# ---------------------------------------------------------------------------


def check_formula(customer_id: str, body: dict[str, Any]) -> list[str]:
    """表单里边写边检查：返回问题列表（空 = 通过）。和保存时的检查完全一样。"""
    try:
        recipe_service.build_formula(customer_id, body)
    except FormulaError as exc:
        return [str(exc)]
    return []


# ---------------------------------------------------------------------------
# 试运行
# ---------------------------------------------------------------------------


def _failed(stage: str, error: str, **extra: Any) -> dict[str, Any]:
    """试运行失败不是接口错误：把原因和阶段交给界面展示。stage：definition 公式定义 / data 试运行数据 / params 参数取值 / run SQL 执行。"""
    return {"status": "failed", "stage": stage, "error": error, **extra}


def _load_inputs(db: Session, ws: Workspace, customer_id: str, inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把试运行用的数据放进临时工作区：客户的文件（按客户字段映射统一列名）或粘贴的表格文本。"""
    mapping = memory_service.field_mapping_for(db, customer_id)
    loaded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in inputs:
        name = sanitize_table_name(str(item.get("table") or ""), fallback="数据")
        if name in seen or name == TRIAL_OUTPUT:
            raise ServiceError(f"试运行用的表名「{name}」重复了（「{TRIAL_OUTPUT}」也是保留的），请换一个")
        seen.add(name)
        file_id, text = item.get("file_id"), item.get("text")
        if bool(file_id) == bool(text):
            raise ServiceError(f"表「{name}」要么选一个客户的文件，要么粘贴表格数据，两个只能选一个")
        info: dict[str, Any] = {"table": name}
        if file_id:
            record = file_service.get_file(db, str(file_id))
            if record.customer_id != customer_id:
                raise Forbidden("这个文件不属于当前客户，不能用来试运行")
            sheets = read_workbook(Path(record.stored_path))
            wanted = item.get("sheet")
            sheet = next((s for s in sheets if s.name == wanted), None) if wanted else sheets[0]
            if sheet is None:
                raise ReadError(f"文件「{record.filename}」里没有工作表「{wanted}」，现有：{'、'.join(s.name for s in sheets)}")
            df, renames = apply_field_mapping(sheet.df, mapping)
            ws.put_dataframe(name, df, label=f"{record.filename} · {sheet.name}", source="uploaded", role="input",
                             file_id=record.id, filename=record.filename, sheet=sheet.name)
            info.update(source="file", filename=record.filename, sheet=sheet.name, renamed=renames)
        else:
            sheet = read_pasted_table(str(text))
            ws.put_dataframe(name, sheet.df, label="粘贴的数据", source="uploaded", role="input")
            info.update(source="text", filename="", sheet="", renamed={})
        table = ws.require(name)
        info.update(rows=table.rows, columns=table.column_names)
        loaded.append(info)
    return loaded


def trial_formula(db: Session, customer_id: str, body: dict[str, Any], params: dict[str, Any],
                  inputs: list[dict[str, Any]]) -> dict[str, Any]:
    """用 body 描述的公式（可以是还没保存的）在临时工作区里跑一遍，返回结果预览。

    - 数据来自客户的文件（会按客户的字段映射统一列名）或粘贴的几行；文件必须是这个客户的
    - 临时工作区是内存里的 DuckDB（沙箱：不能读写本机文件、不能联网），用完即丢，不会动任何客户数据
    - 超过 TRIAL_TIMEOUT 秒自动中止，免得一条写坏的 SQL 把机器拖住
    """
    try:
        formula = recipe_service.build_formula(customer_id, body)
    except FormulaError as exc:
        return _failed("definition", str(exc))
    ws = Workspace(None)
    try:
        try:
            loaded = _load_inputs(db, ws, customer_id, inputs)
        except ReadError as exc:
            return _failed("data", str(exc))
        library = FormulaLibrary([formula])  # 只有这一个公式；即使 id 和内置公式相同也用它
        sql = ""
        timer = threading.Timer(TRIAL_TIMEOUT, ws.interrupt)
        timer.daemon = True
        timer.start()
        try:
            _, plan = library.resolve(formula.id).compile(params, ws)
            sql = plan.sql
            outcome = apply_formula(ws, library, formula.id, params, TRIAL_OUTPUT, label=TRIAL_OUTPUT, created_by="trial")
            page = ws.page(TRIAL_OUTPUT, 0, PREVIEW_ROWS)
        except (FormulaError, WorkspaceError) as exc:
            message = str(exc)
            if "Interrupted" in message:
                message = f"运行超过 {int(TRIAL_TIMEOUT)} 秒，已自动中止。请缩小试运行的数据，或者简化 SQL。"
            return _failed("run" if sql else "params", message, sql=sql, inputs=loaded)
        finally:
            timer.cancel()
        warnings = list(outcome.warnings)
        if outcome.rows_out == 0:
            warnings.append("结果是 0 行：检查筛选条件和试运行用的数据")
        return {
            "status": "success", "table": TRIAL_OUTPUT, "rows": outcome.rows_out, "columns": page["columns"],
            "preview": page, "sql": outcome.sql, "params": outcome.params, "warnings": warnings, "metrics": outcome.metrics,
            "rows_in": outcome.rows_in, "inputs": loaded, "duration_ms": outcome.duration_ms,
        }
    finally:
        ws.close()
