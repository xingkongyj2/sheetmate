from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import openpyxl
import pytest
from openpyxl.styles import Font, PatternFill

from sheetmate.agent.tools.contracts import Tool, ToolResult
from sheetmate.engine.workbooks.copies import temporary_file_copies
from sheetmate.engine.formulas import FormulaLibrary
from sheetmate.engine.workbooks.reader import ReadError
from sheetmate.engine.tables.workspace import Workspace
from sheetmate.recipe.execution import runner as executor
from sheetmate.recipe.execution.runner import InputSource, RecipeRunner
from sheetmate.recipe.definition.schema import Recipe
from sheetmate.services.conversations import chat as chat_service
from sheetmate.services.files import service as file_service
from tests.test_api import _chat, _new_conversation, _upload


def _workbook(path: Path) -> None:
    workbook = openpyxl.Workbook()
    workbook.active.title = "明细"
    workbook.active.append(["编号", "金额"])
    workbook.active.append([1, 100])
    workbook.active.append([2, 200])
    workbook.save(path)
    workbook.close()


def _runner(workspace: Workspace, *, aliases: tuple[str, ...] = ("明细",)) -> RecipeRunner:
    recipe = Recipe(name="副本处理", inputs=[{"alias": alias, "sheet": "明细", "required_columns": ["编号"]}
                                              for alias in aliases],
                    output_spec=[{"name": "明细", "source": aliases[0]}])
    return RecipeRunner(recipe, FormulaLibrary(), workspace)


def _run(runner: RecipeRunner, sources=None, output_path=None):
    async def consume():
        return [event async for event in runner.run(sources, output_path=output_path)]

    return asyncio.run(consume())


def test_file_copies_keep_same_named_sources_separate(tmp_path):
    first = tmp_path / "first" / "data.csv"
    second = tmp_path / "second" / "data.csv"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("first")
    second.write_text("second")
    with temporary_file_copies() as files:
        first_copy = files.file_copy(first)
        second_copy = files.file_copy(second)
        assert first_copy != second_copy
        first_copy.write_text("changed")
        assert files.file_copy(first) == first_copy
        assert files.file_copy(first_copy) == first_copy
        assert second_copy.read_text() == "second"
        root = files.root
    assert not root.exists()
    assert first.read_text() == "first" and second.read_text() == "second"


def test_recipe_reads_and_writes_independent_disposable_copies(tmp_path, monkeypatch):
    source = tmp_path / "source.xlsx"
    _workbook(source)
    before = source.read_bytes()
    paths = []
    real_read = executor.read_workbook
    real_write = executor.write_like_original

    def read(path):
        assert path != source and path.read_bytes() == before
        paths.append(path)
        return real_read(path)

    def write(path, plans):
        output_template = plans[0].template.path
        assert output_template not in {source, paths[0]}
        assert output_template.read_bytes() == before
        assert all(plan.template.path.resolve() == output_template.resolve() for plan in plans)
        paths.append(output_template)
        return real_write(path, plans)

    monkeypatch.setattr(executor, "read_workbook", read)
    monkeypatch.setattr(executor, "write_like_original", write)
    workspace = Workspace(None)
    try:
        runner = _runner(workspace)
        _run(runner, {"明细": InputSource("明细", path=source, filename=source.name)}, tmp_path / "output.xlsx")
        assert runner.report["status"] == "success"
        assert runner.sources["明细"].path == source
        assert len(paths) == 2 and all(not path.exists() for path in paths)
        assert source.read_bytes() == before
    finally:
        workspace.close()


@pytest.mark.parametrize("damage", ["remove_formatting", "corrupt_file"])
def test_output_recopies_original_after_read_copy_is_modified(tmp_path, monkeypatch, damage):
    source = tmp_path / "source.xlsx"
    output = tmp_path / "output.xlsx"
    _workbook(source)
    workbook = openpyxl.load_workbook(source)
    sheet = workbook["明细"]
    sheet["A1"].font = Font(bold=True, color="FF123456")
    sheet["A1"].fill = PatternFill("solid", fgColor="FFABCDEF")
    sheet["B2"].number_format = "#,##0.00"
    sheet.column_dimensions["B"].width = 27
    sheet.row_dimensions[2].height = 35
    sheet.freeze_panes = "A2"
    workbook.save(source)
    workbook.close()
    before = source.read_bytes()
    read_paths = []
    real_read = executor.read_workbook

    def read_and_modify_copy(path):
        assert path != source and path.read_bytes() == before
        frames = real_read(path)
        read_paths.append(path)
        if damage == "corrupt_file":
            path.write_bytes(b"unrecoverable working copy")
            return frames
        copy = openpyxl.load_workbook(path)
        try:
            copied_sheet = copy["明细"]
            for row in copied_sheet:
                for cell in row:
                    cell._style = None
            copied_sheet.column_dimensions.clear()
            copied_sheet.row_dimensions.clear()
            copied_sheet.freeze_panes = None
            copy.save(path)
        finally:
            copy.close()
        assert path.read_bytes() != before
        return frames

    monkeypatch.setattr(executor, "read_workbook", read_and_modify_copy)
    workspace = Workspace(None)
    try:
        runner = _runner(workspace)
        _run(runner, {"明细": InputSource("明细", path=source)}, output)
        assert runner.report["status"] == "success", runner.report
        result = openpyxl.load_workbook(output)
        try:
            edited = result["明细"]
            assert edited["A1"].font.bold and edited["A1"].font.color.rgb == "FF123456"
            assert edited["A1"].fill.fgColor.rgb == "FFABCDEF"
            assert edited["B2"].number_format == "#,##0.00"
            assert edited.column_dimensions["B"].width == 27
            assert edited.row_dimensions[2].height == 35
            assert edited.freeze_panes == "A2"
            assert edited["B2"].value == 100
        finally:
            result.close()
        assert read_paths and all(not path.exists() for path in read_paths)
        assert source.read_bytes() == before
    finally:
        workspace.close()


def test_recipe_failure_cleans_copy_without_changing_source(tmp_path, monkeypatch):
    source = tmp_path / "source.xlsx"
    _workbook(source)
    before = source.read_bytes()
    paths = []

    def fail(path):
        paths.append(path)
        path.write_bytes(b"broken temporary data")
        raise ReadError("无法读取副本")

    monkeypatch.setattr(executor, "read_workbook", fail)
    workspace = Workspace(None)
    try:
        runner = _runner(workspace)
        _run(runner, {"明细": InputSource("明细", path=source)}, tmp_path / "output.xlsx")
        assert runner.report["status"] == "failed" and runner.report["failed_stage"] == "input"
        assert paths and all(not path.exists() for path in paths)
        assert source.read_bytes() == before and not (tmp_path / "output.xlsx").exists()
    finally:
        workspace.close()


def test_recipe_cancellation_waits_for_file_worker_before_cleanup(tmp_path, monkeypatch):
    source = tmp_path / "source.xlsx"
    _workbook(source)
    before = source.read_bytes()
    started = threading.Event()
    release = threading.Event()
    paths = []
    real_read = executor.read_workbook

    def read(path):
        paths.append(path)
        started.set()
        assert release.wait(5)
        assert path.exists()
        return real_read(path)

    monkeypatch.setattr(executor, "read_workbook", read)
    workspace = Workspace(None)
    runner = _runner(workspace)

    async def run():
        async def consume():
            return [event async for event in runner.run({"明细": InputSource("明细", path=source)})]

        task = asyncio.create_task(consume())
        try:
            assert await asyncio.to_thread(started.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and paths[0].exists()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    try:
        asyncio.run(run())
        assert paths and not paths[0].exists()
        assert source.read_bytes() == before
    finally:
        workspace.close()


def test_rerun_and_later_output_each_copy_restored_sources(tmp_path, monkeypatch):
    source = tmp_path / "source.xlsx"
    _workbook(source)
    paths = []
    real_write = executor.write_like_original

    def write(path, plans):
        template = plans[0].template.path
        assert template != source and template.read_bytes() == source.read_bytes()
        paths.append(template)
        return real_write(path, plans)

    monkeypatch.setattr(executor, "write_like_original", write)
    workspace = Workspace(None)
    try:
        original = _runner(workspace)
        _run(original, {"明细": InputSource("明细", path=source, file_id="source")})
        runner = _runner(workspace)
        runner.restore_sources(original.report["inputs"], lambda _: source)
        _run(runner, output_path=tmp_path / "rerun.xlsx")
        assert runner.report["status"] == "success"
        runner.write_outputs(tmp_path / "later.xlsx", runner.collect_outputs())
        assert len(set(paths)) == 2 and all(not path.exists() for path in paths)
        assert runner.sources["明细"].path == source
    finally:
        workspace.close()


@pytest.mark.parametrize("macro_extension", [False, True])
def test_recipe_cannot_overwrite_any_original_input(tmp_path, macro_extension):
    first = tmp_path / ("first.xlsm" if macro_extension else "first.xlsx")
    second = tmp_path / "second.xlsx"
    _workbook(first)
    _workbook(second)
    before = {path: path.read_bytes() for path in (first, second)}
    workspace = Workspace(None)
    try:
        runner = _runner(workspace, aliases=("第一张", "第二张"))
        output = first.with_suffix(".xlsx") if macro_extension else second
        _run(runner, {"第一张": InputSource("第一张", path=first), "第二张": InputSource("第二张", path=second)}, output)
        assert runner.report["status"] == "failed" and "不能覆盖原文件" in runner.report["error"]
        assert all(path.read_bytes() == contents for path, contents in before.items())
    finally:
        workspace.close()


@pytest.mark.parametrize("operation", ["profile", "preview", "load"])
def test_file_services_read_disposable_copies(tmp_path, monkeypatch, operation):
    source = tmp_path / "source.xlsx"
    _workbook(source)
    before = source.read_bytes()
    paths = []
    real_read = file_service.read_workbook

    def read(path):
        assert path != source and path.read_bytes() == before
        paths.append(path)
        return real_read(path)

    monkeypatch.setattr(file_service, "read_workbook", read)
    record = SimpleNamespace(id="source", stored_path=str(source), filename=source.name, kind="input")
    workspace = Workspace(None)
    try:
        if operation == "profile":
            file_service.profile_file(source)
        elif operation == "preview":
            file_service.preview(record)
        else:
            file_service.load_into_workspace(workspace, record)
        assert paths and all(not path.exists() for path in paths)
        assert source.read_bytes() == before
    finally:
        workspace.close()


def test_modify_chat_uses_temporary_file_copies(client, samples, fake_llm, monkeypatch):
    conversation = _new_conversation(client)
    uploaded = _upload(client, conversation, samples["fees_sep"])
    before = client.get(uploaded["file"]["download_url"]).content
    paths = []

    def process_copy(args, ctx):
        path = ctx.host.file_path(uploaded["file"]["file_id"])
        assert path.read_bytes() == before
        paths.append(path)
        path.write_bytes(b"temporary edit")
        return ToolResult("已处理副本")

    monkeypatch.setitem(chat_service._registry._static, "process_copy", Tool(
        "process_copy", "处理临时副本", {"type": "object"}, frozenset({"modify"}), run=process_copy,
    ))
    fake_llm(script=[{"tools": [("process_copy", {})]}, {"text": "已处理副本。"}], intent_mode="modify")
    events = _chat(client, conversation, "修改表格")
    assert all(event["ok"] for event in events if event["type"] == "tool_result")
    assert paths and all(not path.exists() for path in paths)
    assert client.get(uploaded["file"]["download_url"]).content == before
