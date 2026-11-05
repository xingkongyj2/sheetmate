"""首次启动的初始化：建表、创建管理员账号、写入示例客户（可用 SEED_DEMO=0 关闭）。

示例客户「顺达物流（示例）」包含：两个原始文件 + 一个人工结果样例、一个已验证的月结账单流程、
字段映射（单号→运单号、店铺→店铺名称）、业务规则记忆、一次成功的运行记录和整体处理逻辑说明，
登录后打开客户卡片就能看到完整效果，并能直接用 10 月的新文件重复执行。
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

from sqlalchemy import select

from sheetmate.config import settings
from sheetmate.db.database import init_db, session_scope
from sheetmate.db.models import Customer, User
from sheetmate.demo.samples import DEFAULT_OWNER, demo_recipe_body, write_samples
from sheetmate.engine.formulas import FormulaLibrary
from sheetmate.engine.reader import read_workbook
from sheetmate.recipe.executor import InputSource, RecipeRunner
from sheetmate.recipe.schema import Recipe
from sheetmate.recipe.verifier import verify_outputs
from sheetmate.services import auth as auth_service
from sheetmate.services import customers as customer_service
from sheetmate.services import files as file_service
from sheetmate.services import memory as memory_service
from sheetmate.services import recipes as recipe_service
from sheetmate.services import runs as run_service

DEMO_NAME = "顺达物流（示例）"


def bootstrap() -> None:
    settings.ensure_dirs()
    init_db()
    with session_scope() as db:
        recipe_service.migrate_recipe_statuses(db)
        admin = auth_service.ensure_admin(db)
        admin_id = admin.id
        has_demo = db.scalar(select(Customer).where(Customer.name == DEMO_NAME)) is not None
    if settings.seed_demo and not has_demo:
        seed_demo(admin_id)


def seed_demo(user_id: int) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="sheetmate_demo_"))
    paths = write_samples(tmp)
    with session_scope() as db:
        user = db.get(User, user_id)
        customer = customer_service.create_customer(db, user, DEMO_NAME, "SD-001", "快递月结客户：每月对账出账单，按店铺归属汇总。")
        customer.mascot = "blob"
        records = {}
        for key, kind in (("fees_sep", "input"), ("owners", "input"), ("expected_sep", "expected"), ("fees_oct", "input")):
            records[key] = file_service.save_upload(db, data=paths[key].read_bytes(), filename=paths[key].name, user=user,
                                                    customer_id=customer.id, kind=kind)
        memory_service.upsert_field_mapping(db, customer.id, "运单号", ["单号", "运单编号"])
        memory_service.upsert_field_mapping(db, customer.id, "店铺名称", ["店铺", "店铺名"])
        memory_service.set_memory(db, category="domain_knowledge", key="归属兜底规则",
                                  value=f"店铺在归属表里匹配不到时，归属统一记为「{DEFAULT_OWNER}」", customer_id=customer.id, source="human")
        memory_service.set_memory(db, category="preferences", key="金额格式", value="金额保留两位小数", customer_id=customer.id, source="human")
        memory_service.set_memory(db, category="user_context", key="对账周期", value="每月 5 号前出上个月的账单", customer_id=customer.id, source="human")

        recipe = Recipe(**demo_recipe_body(), customer_id=customer.id, created_by="human", status="active")
        recipe.learned_from = {"input_files": [paths["fees_sep"].name, paths["owners"].name], "expected_file": paths["expected_sep"].name,
                               "requirement": "示例流程"}
        customer_id = customer.id
        run = run_service.create_run(db, kind="replay", user_id=user_id, customer_id=customer_id, conversation_id=None,
                                     recipe_id=recipe.id, recipe_version=1, request_text="示例：用 9 月数据执行月结账单流程",
                                     input_file_ids=[records["fees_sep"].id, records["owners"].id])
        out_path = run_service.output_path(run, "月结账单整理_结果.xlsx")
        run_id = run.id
        fee_record, owner_record = records["fees_sep"], records["owners"]
        sources = {
            "运费明细": InputSource("运费明细", path=Path(fee_record.stored_path), file_id=fee_record.id, filename=fee_record.filename),
            "店铺归属": InputSource("店铺归属", path=Path(owner_record.stored_path), file_id=owner_record.id, filename=owner_record.filename),
        }
        expected_path = Path(records["expected_sep"].stored_path)
        mapping = memory_service.field_mapping_for(db, customer_id)

    runner = RecipeRunner(recipe, FormulaLibrary(), run_service.run_workspaces.get(run_id), field_mapping=mapping, run_id=run_id)

    async def _run() -> None:
        async for _ in runner.run(sources, output_path=out_path):
            pass

    asyncio.run(_run())
    verification = verify_outputs(runner.collect_outputs(), {s.name: s.df for s in read_workbook(expected_path)})
    recipe.metadata["verification"] = {"status": verification["status"], "match_rate": verification["overall_match_rate"],
                                       "summary": verification["summary"]}
    recipe.metadata["last_run"] = {"run_id": run_id, "status": runner.report["status"]}
    with session_scope() as db:
        record = file_service.register_output(db, path=out_path, filename="月结账单整理_结果.xlsx", customer_id=customer_id,
                                              conversation_id=None, run_id=run_id, user_id=user_id)
        run_service.finish_run(db, run_id, runner.report, verification=verification, output_file_id=record.id,
                               outputs_metrics=run_service.metrics_of(runner.collect_outputs()))
        recipe.metadata["version_note"] = "示例流程"
        recipe_service.save_recipe(db, recipe, status="active")
        customer = db.get(Customer, customer_id)
        asyncio.run(customer_service.refresh_summary(db, customer, None))
    shutil.rmtree(tmp, ignore_errors=True)
