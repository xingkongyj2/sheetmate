"""Deletion behavior for conversation/customer-owned data and shared files."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pandas as pd
from sqlalchemy import select


def _new_conversation(client, customer_id: str | None = None) -> str:
    response = client.post("/api/conversations", json={"customer_id": customer_id})
    assert response.status_code == 200, response.text
    return response.json()["conversation"]["id"]


def _new_customer(client, name: str) -> str:
    response = client.post("/api/customers", json={"name": name})
    assert response.status_code == 200, response.text
    return response.json()["customer"]["id"]


def test_deleting_conversation_reclaims_private_data_and_preserves_shared_file(client, tmp_path):
    from sheetmate.config import settings
    from sheetmate.db.database import session_scope
    from sheetmate.db.models import Conversation, FileConversation, FileCustomer, FileRecord, RecipeRecord, Run
    from sheetmate.services import conversations as conversation_service
    from sheetmate.services import runs as run_service

    conversation_id = _new_conversation(client)
    other_conversation_id = _new_conversation(client)
    recipe_id = f"recipe_{uuid4().hex[:10]}"
    private_path = tmp_path / "private.xlsx"
    shared_path = tmp_path / "shared.xlsx"
    private_path.write_bytes(b"private data")
    shared_path.write_bytes(b"shared data")

    with session_scope() as db:
        private_file = FileRecord(filename=private_path.name, stored_path=str(private_path), size=private_path.stat().st_size,
                                  conversation_id=conversation_id, created_by=1)
        shared_file = FileRecord(filename=shared_path.name, stored_path=str(shared_path), size=shared_path.stat().st_size,
                                 conversation_id=conversation_id, created_by=1)
        db.add_all([private_file, shared_file])
        db.flush()
        db.add_all([
            FileConversation(file_id=private_file.id, conversation_id=conversation_id),
            FileConversation(file_id=shared_file.id, conversation_id=conversation_id),
            FileConversation(file_id=shared_file.id, conversation_id=other_conversation_id),
            RecipeRecord(recipe_id=recipe_id, version=1, name="临时流程", scope="conversation",
                         conversation_id=conversation_id, body={}),
        ])
        run = Run(kind="analysis", conversation_id=conversation_id, user_id=1, request_text="临时运行")
        db.add(run)
        db.flush()
        run_id = run.id
        # Leave both DuckDB files open so deletion must also evict cached workspaces.
        conversation_service.workspace(conversation_id).put_dataframe("source", pd.DataFrame({"value": [1]}))
        run_service.run_workspaces.get(run_id).put_dataframe("result", pd.DataFrame({"value": [1]}))

    conversation_workspace = settings.workspaces_dir / f"{conversation_id}.duckdb"
    run_workspace = settings.runs_dir / f"{run_id}.duckdb"
    assert conversation_workspace.exists() and run_workspace.exists()

    response = client.delete(f"/api/conversations/{conversation_id}")
    assert response.status_code == 200, response.text

    with session_scope() as db:
        assert db.get(Conversation, conversation_id) is None
        assert db.get(FileRecord, private_file.id) is None
        assert db.get(FileRecord, shared_file.id) is not None
        assert db.get(Run, run_id) is None
        assert db.scalar(select(RecipeRecord.id).where(RecipeRecord.recipe_id == recipe_id)) is None
        assert db.get(FileConversation, (shared_file.id, other_conversation_id)) is not None
        assert db.get(FileCustomer, (shared_file.id, "missing-customer")) is None

    assert not private_path.exists()
    assert shared_path.exists()
    assert not conversation_workspace.exists()
    assert not run_workspace.exists()

    # The final conversation reference releasing the file should reclaim its row and disk data.
    response = client.delete(f"/api/conversations/{other_conversation_id}")
    assert response.status_code == 200, response.text
    with session_scope() as db:
        assert db.get(FileRecord, shared_file.id) is None
    assert not shared_path.exists()


def test_deleting_customer_cleans_customer_records_and_only_unreferenced_files(client, tmp_path):
    from sheetmate.config import settings
    from sheetmate.db.database import session_scope
    from sheetmate.db.models import (
        Conversation,
        Customer,
        FieldMapping,
        FileCustomer,
        FileRecord,
        FileRecipe,
        FormulaRecord,
        Memory,
        RecipeFieldMapping,
        RecipeProfile,
        RecipeRecord,
        Run,
    )
    from sheetmate.services import runs as run_service
    from sheetmate.services import conversations as conversation_service

    customer_id = _new_customer(client, f"待删除客户-{uuid4().hex[:8]}")
    other_customer_id = _new_customer(client, f"保留客户-{uuid4().hex[:8]}")
    conversation_id = _new_conversation(client, customer_id)
    recipe_id = f"recipe_{uuid4().hex[:10]}"
    private_path = tmp_path / "customer-private.xlsx"
    shared_path = tmp_path / "customer-shared.xlsx"
    private_path.write_bytes(b"customer private data")
    shared_path.write_bytes(b"customer shared data")

    with session_scope() as db:
        private_file = FileRecord(filename=private_path.name, stored_path=str(private_path), size=private_path.stat().st_size,
                                  customer_id=customer_id, created_by=1)
        shared_file = FileRecord(filename=shared_path.name, stored_path=str(shared_path), size=shared_path.stat().st_size,
                                 customer_id=customer_id, created_by=1)
        db.add_all([private_file, shared_file])
        db.flush()
        db.add_all([
            FileCustomer(file_id=private_file.id, customer_id=customer_id),
            FileCustomer(file_id=shared_file.id, customer_id=customer_id),
            FileCustomer(file_id=shared_file.id, customer_id=other_customer_id),
            FileRecipe(file_id=private_file.id, recipe_id=recipe_id, customer_id=customer_id),
            RecipeRecord(recipe_id=recipe_id, version=1, name="客户流程", scope="customer",
                         customer_id=customer_id, body={}),
            RecipeProfile(recipe_id=recipe_id, customer_id=customer_id, summary="客户规则"),
            FormulaRecord(formula_id=f"formula_{uuid4().hex[:8]}", version=1, scope="customer",
                          customer_id=customer_id, name="客户公式", body={}),
            Memory(customer_id=customer_id, category="domain_knowledge", key="规则", value="只属于该客户"),
            FieldMapping(customer_id=customer_id, standard="订单号", aliases=["单号"]),
            RecipeFieldMapping(customer_id=customer_id, recipe_id=recipe_id, standard="金额", aliases=["总额"]),
        ])
        run = Run(kind="replay", customer_id=customer_id, user_id=1, recipe_id=recipe_id, request_text="客户运行")
        db.add(run)
        db.flush()
        run_id = run.id
        run_service.run_workspaces.get(run_id).put_dataframe("result", pd.DataFrame({"value": [1]}))
        conversation_service.workspace(conversation_id).put_dataframe("customer_data", pd.DataFrame({"value": [1]}))

    run_workspace = settings.runs_dir / f"{run_id}.duckdb"
    conversation_workspace = settings.workspaces_dir / f"{conversation_id}.duckdb"
    assert run_workspace.exists() and conversation_workspace.exists()

    response = client.delete(f"/api/customers/{customer_id}")
    assert response.status_code == 200, response.text

    with session_scope() as db:
        assert db.get(Customer, customer_id) is None
        assert db.get(Conversation, conversation_id) is None
        assert db.get(FileRecord, private_file.id) is None
        retained = db.get(FileRecord, shared_file.id)
        assert retained is not None
        assert retained.customer_id == other_customer_id
        assert db.get(FileCustomer, (shared_file.id, other_customer_id)) is not None
        assert db.get(FileRecipe, (private_file.id, recipe_id)) is None
        assert db.scalar(select(RecipeRecord.id).where(RecipeRecord.recipe_id == recipe_id)) is None
        assert db.get(RecipeProfile, recipe_id) is None
        assert db.scalar(select(FormulaRecord.id).where(FormulaRecord.customer_id == customer_id)) is None
        assert db.scalar(select(Memory.id).where(Memory.customer_id == customer_id)) is None
        assert db.scalar(select(FieldMapping.id).where(FieldMapping.customer_id == customer_id)) is None
        assert db.scalar(select(RecipeFieldMapping.id).where(RecipeFieldMapping.customer_id == customer_id)) is None
        assert db.get(Run, run_id) is None

    assert not private_path.exists()
    assert shared_path.exists()
    assert not run_workspace.exists()
    assert not conversation_workspace.exists()
