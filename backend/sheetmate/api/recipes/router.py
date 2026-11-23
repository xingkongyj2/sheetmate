"""Recipe：列表、详情、版本（含和上一版对比）、编辑（存为新版本）、匹配输入、用新文件执行（SSE）、删除；公式目录。"""

from __future__ import annotations

from sheetmate.api.customers.schemas import FormulaGenerateBody
from sheetmate.api.recipes.schemas import GlobalTrialBody, MatchBody, NodeEditBody, RunBody

from typing import Any, Literal

from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Response, UploadFile
from sqlalchemy.orm import Session

from sheetmate.api.deps import admin_user, current_user, sse_response
from sheetmate.db.session import get_db
from sheetmate.db.models import User
from sheetmate.services.conversations import service as conv_service
from sheetmate.services.customers import service as customer_service
from sheetmate.services.privacy import service as privacy_service
from sheetmate.services.formulas import service as formula_service
from sheetmate.services.formulas import suggestions as suggestion_service
from sheetmate.services.files import service as file_service
from sheetmate.services.workflows import file_rename as file_rename_service
from sheetmate.services.workflows import node_edit as node_edit_service
from sheetmate.services.workflows import package as package_service
from sheetmate.services.workflows import service as recipe_service
from sheetmate.services.workflows import replay as replay_service
from sheetmate.services.workflows import runs as run_service
from sheetmate.services.workflows import version_diff as version_diff_service
from sheetmate.services.identity.auth import require_customer

router = APIRouter(prefix="/api", tags=["recipes"])


@router.get("/recipes")
def list_recipes(customer_id: str | None = None, conversation_id: str | None = None,
                 db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    if customer_id:
        require_customer(db, user, customer_id)
    if conversation_id:
        conv_service.get_conversation(db, user, conversation_id)
    records = recipe_service.latest_records(db, customer_id=customer_id, conversation_id=conversation_id)
    if not customer_id and not conversation_id:
        accessible_customer_ids = {c.id for c in customer_service.accessible_customers(db, user)}
        owned_conv_ids = {c.id for c in conv_service.list_conversations(db, user, limit=10_000)}
        records = [
            r for r in records
            if (r.customer_id and r.customer_id in accessible_customer_ids)
            or (r.conversation_id and r.conversation_id in owned_conv_ids)
            or (not r.customer_id and not r.conversation_id and r.scope == "global")
        ]
    return {"ok": True, "recipes": [recipe_service.recipe_brief(r) for r in records]}


@router.get("/recipes/{recipe_id}")
def get_recipe(recipe_id: str, version: int | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    recipe = recipe_service.get_recipe(db, recipe_id, version)
    recipe_service.require_recipe_access(db, user, recipe)
    runs = run_service.list_runs(db, recipe_id=recipe_id, limit=20)
    return {"ok": True, "recipe": recipe.to_dict(), "flow": recipe.flow(), "versions": recipe_service.versions(db, recipe_id, user=user),
            "runs": [run_service.run_dict(r, with_steps=False) for r in runs]}


@router.get("/recipes/{recipe_id}/versions/{version}/changes")
def version_changes(recipe_id: str, version: int, db: Session = Depends(get_db),
                    user: User = Depends(current_user)) -> dict[str, Any]:
    """这个版本和上一版相比改了什么。"""
    recipe_service.require_recipe_access(db, user, recipe_service.get_recipe(db, recipe_id, version))
    return {"ok": True, **version_diff_service.version_changes(db, recipe_id, version)}


@router.get("/recipes/{recipe_id}/package")
def export_package(recipe_id: str, mode: Literal["exact", "rebuild"] = "exact",
                   db: Session = Depends(get_db), user: User = Depends(current_user)) -> Response:
    """导出流程包（.zip）：
    - mode=exact：完整流程包（*_完整流程包.zip），供客户页直接按原格式导入；
    - mode=rebuild：对话重建包（*_对话重建包.zip），供拖入聊天页由 AI 重建执行。"""
    recipe = recipe_service.get_recipe(db, recipe_id)
    recipe_service.require_recipe_access(db, user, recipe)
    filename, data = package_service.export_package(db, recipe, mode=mode)
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


@router.post("/customers/{customer_id}/recipes/import")
async def import_package(customer_id: str, background: BackgroundTasks, file: UploadFile = File(...),
                         db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    """导入「完整流程包（.zip）」到指定客户（支持导入到空客户，拒绝对话重建包等非完整格式文件）。"""
    require_customer(db, user, customer_id)
    data = await file.read()
    result = package_service.import_package(db, data=data, filename=file.filename or "recipe.zip",
                                            customer_id=customer_id, user=user)
    db.commit()
    for file_id in result["uploaded_file_ids"]:
        background.add_task(privacy_service.identify_uploaded, file_id)
    if not result["has_summary"]:
        background.add_task(customer_service.auto_refresh_summary, customer_id)
    return {
        "ok": True,
        "recipe": recipe_service.recipe_brief(result["record"]),
        "imported_files": result["imported_files"],
    }


@router.put("/recipes/{recipe_id}")
def update_recipe(recipe_id: str, body: dict[str, Any], background: BackgroundTasks, db: Session = Depends(get_db),
                  user: User = Depends(current_user)) -> dict[str, Any]:
    """编辑流程：结构检查通过后保存为新版本。"""
    record = recipe_service.update_recipe(db, user, recipe_id, body)
    _refresh_later(db, background, record.customer_id)
    return {"ok": True, "recipe": recipe_service.recipe_brief(record)}


@router.get("/recipes/{recipe_id}/nodes")
def inspect_nodes(recipe_id: str, version: int | None = None, step_id: int | None = None, line_start: int | None = None,
                  line_end: int | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    """各节点的可修改内容；带 step_id 时返回该步脚本内容（可按行区间读取）。"""
    return {"ok": True, "nodes": node_edit_service.inspect_nodes(db, user, recipe_id, version=version, step_id=step_id,
                                                                  line_start=line_start, line_end=line_end)}


@router.post("/recipes/{recipe_id}/nodes/edit")
def edit_nodes(recipe_id: str, body: NodeEditBody, background: BackgroundTasks, db: Session = Depends(get_db),
               user: User = Depends(current_user)) -> dict[str, Any]:
    """直接修改输入/输出文件名、步骤、脚本名和脚本内容，保存为新版本；任一项不合法则整体不保存。"""
    result = node_edit_service.edit_nodes(db, user, recipe_id, body.edits, base_version=body.base_version, note=body.note)
    _refresh_later(db, background, result["customer_id"])
    return {"ok": True, **result}


@router.delete("/recipes/{recipe_id}")
def delete_recipe(recipe_id: str, background: BackgroundTasks, db: Session = Depends(get_db),
                  user: User = Depends(current_user)) -> dict[str, Any]:
    deleted = recipe_service.delete_recipe(db, recipe_id, user=user)
    _refresh_later(db, background, deleted["customer_id"])
    return {"ok": True}


def _refresh_later(db: Session, background: BackgroundTasks, customer_id: str | None) -> None:
    """流程变了：提交后在后台更新客户的整体处理逻辑。"""
    if customer_id:
        db.commit()
        background.add_task(customer_service.auto_refresh_summary, customer_id)


@router.post("/recipes/{recipe_id}/match")
def match_inputs(recipe_id: str, body: MatchBody, user: User = Depends(current_user)) -> dict[str, Any]:
    return {"ok": True, **replay_service.match(user, recipe_id, body.file_ids)}


@router.post("/recipes/{recipe_id}/run")
async def run_recipe(recipe_id: str, body: RunBody, user: User = Depends(current_user)):
    """用新文件重复执行流程（SSE 推送每一步的进度）。"""
    job = replay_service.start_replay(user, recipe_id, file_ids=body.file_ids, inputs=body.inputs,
                                      allow_repair=body.allow_repair, version=body.version, save_recipe=body.save_recipe,
                                      scoped=body.scoped, label=body.label)
    return sse_response(job)


@router.get("/formulas")
def list_formulas(customer_id: str | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    """当前用户可以访问的内置与客户公式目录。"""
    return {"ok": True, **recipe_service.formula_catalog(db, user, customer_id)}


@router.post("/formulas")
def save_global_formula(body: dict[str, Any], db: Session = Depends(get_db), user: User = Depends(admin_user)) -> dict[str, Any]:
    """新增 / 升级内置公式（所有客户共用，SQL 模板；同一个 id 每保存一次升一个版本）。"""
    record = recipe_service.save_global_formula(db, body, created_by="human")
    item = next(f for f in recipe_service.formula_catalog(db, user)["builtin"] if f["id"] == record.formula_id)
    return {"ok": True, "formula": item}


@router.get("/formulas/suggestions")
def formula_suggestions(db: Session = Depends(get_db), user: User = Depends(admin_user)) -> dict[str, Any]:
    """建议整理成内置公式：多个流程里反复出现的同一段自定义 SQL。只发现，不保存。"""
    return {"ok": True, "suggestions": suggestion_service.suggestions(db)}


@router.post("/formulas/suggestions/{suggestion_id}/dismiss")
def dismiss_formula_suggestion(suggestion_id: str, db: Session = Depends(get_db), user: User = Depends(admin_user)) -> dict[str, Any]:
    """忽略一条建议（整理成内置公式之后也用它，建议不再出现）。"""
    suggestion_service.dismiss(db, suggestion_id)
    return {"ok": True}


@router.get("/formulas/{formula_id}/references")
def formula_references(formula_id: str, db: Session = Depends(get_db), user: User = Depends(admin_user)) -> dict[str, Any]:
    """哪些客户的哪些流程用到了这个公式（删除前确认影响）。"""
    return {"ok": True, "references": formula_service.references(db, formula_id)}


@router.delete("/formulas/{formula_id}")
def delete_global_formula(formula_id: str, db: Session = Depends(get_db), user: User = Depends(admin_user)) -> dict[str, Any]:
    """删除内置公式：自己添加的彻底删除；自带的标记为不可用。"""
    formula_service.delete_global(db, formula_id)
    return {"ok": True}


@router.post("/formulas/files")
async def upload_formula_file(background: BackgroundTasks, file: UploadFile = File(...), db: Session = Depends(get_db),
                              user: User = Depends(admin_user)) -> dict[str, Any]:
    """生成内置公式时上传的参考表：不属于任何客户。"""
    record = file_service.save_upload(db, data=await file.read(), filename=file.filename or "file", user=user, kind="input")
    db.commit()   # 后台识别要读到刚保存的文件
    background.add_task(privacy_service.identify_uploaded, record.id)
    return {"ok": True, "file": file_service.file_dict(record)}


@router.post("/formulas/check")
def check_global_formula(body: dict[str, Any], user: User = Depends(admin_user)) -> dict[str, Any]:
    """内置公式边写边检查：和保存时的检查一样，不保存。"""
    return {"ok": True, "problems": formula_service.check_formula(None, body)}


@router.post("/formulas/generate")
async def generate_global_formula(body: FormulaGenerateBody, db: Session = Depends(get_db),
                                  user: User = Depends(admin_user)) -> dict[str, Any]:
    """根据描述和上传的参考表生成内置公式草稿；不保存、不执行，也不绑定客户。"""
    result = await customer_service.generate_customer_formula(
        db, None, description=body.description, file_ids=body.file_ids, formula=body.formula,
        tables=[item.model_dump() for item in body.tables], user=user, require_tables=False)  # 内置公式不针对某张表，参考表可选
    return {"ok": True, **result}


@router.post("/formulas/trial")
def trial_global_formula(body: GlobalTrialBody, db: Session = Depends(get_db), user: User = Depends(admin_user)) -> dict[str, Any]:
    """内置公式试运行：在临时工作区里用直接上传的表跑一遍，返回结果预览。失败也返回 200，原因在 result.error。"""
    result = formula_service.trial_formula(db, None, None, body.params, [i.model_dump() for i in body.inputs],
                                           formula_id=body.formula_id, user=user)
    return {"ok": True, "result": result}
