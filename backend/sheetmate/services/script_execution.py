"""脚本运行的权限、审计、文件登记和工作区交付。引擎不访问数据库。"""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from sheetmate.db.database import session_scope
from sheetmate.db.models import Run, now
from sheetmate.engine.reader import ReadError
from sheetmate.engine.sqlutil import sanitize_table_name
from sheetmate.services import files as file_service
from sheetmate.services import runs as run_service
from sheetmate.services.errors import ServiceError


def _scoped(host, record) -> bool:
    return record.conversation_id == host.conversation_id or bool(host.customer_id and record.customer_id == host.customer_id)


def execute_for_conversation(host, *, code: str, description: str, input_tables: list[str],
                             input_files: dict[str, str], cancel_event: threading.Event) -> dict[str, Any]:
    from sheetmate.engine.script_runner import execute_script

    started = time.perf_counter()
    if len(input_tables) > 20 or len(input_files) > 20 or len(set(input_tables)) != len(input_tables):
        raise ValueError("一次脚本最多使用 20 张不同的表和 20 个文件")
    tables = {}
    source_files: dict[str, Path] = {}
    input_file_ids: list[str] = []
    for name in input_tables:
        info = host.ws.require(name)
        tables[name] = host.ws.df(name)
        if info.file_id:
            input_file_ids.append(info.file_id)
    with session_scope() as db:
        for alias, file_id in input_files.items():
            if not alias.strip() or len(alias) > 100:
                raise ValueError("文件别名应为 1~100 个字符")
            record = file_service.get_file(db, file_id)
            if not _scoped(host, record):
                raise ServiceError("这个文件不属于当前客户或会话")
            source_files[alias] = Path(record.stored_path)
            input_file_ids.append(file_id)
        run = run_service.create_run(db, kind="script", user_id=host.user_id, customer_id=host.customer_id,
                                     conversation_id=host.conversation_id, request_text=description,
                                     input_file_ids=list(dict.fromkeys(input_file_ids)))
        run.steps = [{"tool": "run_python_script", "label": description[:100],
                      "params": {"code": code, "description": description, "input_tables": input_tables, "input_files": input_files}}]
        run.tool_sequence = "run_python_script"
        run_id = run.id
    report: dict[str, Any] = {"run_id": run_id, "ok": False, "status": "failed", "error": "", "files": [],
                              "table_names": [], "tables": [], "internal_tables": [], "warnings": [], "checks": [], "result": {}, "stdout": "", "stderr": ""}
    committed_paths: list[Path] = []
    created_tables: list[str] = []
    previous_tables = {table.name for table in host.ws.list_tables()}
    try:
        with TemporaryDirectory(prefix="sheetmate_script_delivery_") as folder:
            outcome = execute_script(code, tables, source_files, Path(folder), cancel_event=cancel_event)
            report.update(ok=outcome.ok, status=outcome.status, error=outcome.error, stdout=outcome.stdout,
                          stderr=outcome.stderr, checks=outcome.checks, result=outcome.result)
            if cancel_event.is_set():
                report.update(ok=False, status="cancelled", error="用户已停止脚本")
            if report["ok"]:
                with session_scope() as db:
                    run = db.get(Run, run_id)
                    assert run is not None
                    for output in outcome.output_files:
                        if cancel_event.is_set():
                            raise InterruptedError("用户已停止脚本")
                        name = file_service.safe_filename(output.name)
                        path = run_service.output_path(run, name)
                        # 只创建新文件，禁止覆盖旧结果或任何输入件。
                        with output.open("rb") as source, path.open("xb") as destination:
                            committed_paths.append(path)
                            shutil.copyfileobj(source, destination)
                        record = file_service.register_output(db, path=path, filename=name, customer_id=host.customer_id,
                                                              conversation_id=host.conversation_id, run_id=run_id, user_id=host.user_id)
                        report["files"].append({**file_service.file_dict(record), "script_run_id": run_id})
                        try:
                            loaded = file_service.load_into_workspace(host.ws, record)
                            for info in loaded:
                                created_tables.append(info.name)
                                host.ws.update_meta(info.name, created_by="run_python_script",
                                                    description=f"Python 脚本结果，run_id={run_id}")
                        except ReadError:
                            report["warnings"].append(f"「{name}」没有可供数据分析的非空表，文件仍可下载。")
                    for label, df in outcome.result_tables.items():
                        name = host.ws.unique_name(sanitize_table_name(str(label)))
                        host.ws.put_dataframe(name, df, label=str(label), source="computed", role="derived",
                                              created_by="run_python_script", description=f"Python 脚本结果，run_id={run_id}")
                        if label in outcome.display_tables:
                            created_tables.append(name)
                        else:
                            report["internal_tables"].append({"name": name, "rows": len(df), "columns": [str(column) for column in df.columns]})
                    if cancel_event.is_set():
                        raise InterruptedError("用户已停止脚本")
                    report["table_names"] = created_tables
                    report["tables"] = [{"name": name, "rows": host.ws.require(name).rows,
                                         "columns": host.ws.require(name).column_names} for name in created_tables]
                    if not outcome.checks:
                        report["warnings"].append("脚本执行成功，尚未声明业务校验；请继续核对用户要求，不将无异常视为全部达标。")
                    if not report["files"] and not report["table_names"] and not report["internal_tables"] and not outcome.result and not outcome.stdout:
                        report["warnings"].append("脚本没有返回文件、数据表或结果摘要，仍需完成用户目标。")
                    if not report["files"]:
                        report["warnings"].append("本次未声明交付文件；如需交付，将最终文件列入 deliver_files，临时测试文件不要列入。")
                    # 文件登记和全部表元信息准备好后再提交；失败不留下可下载的半成品。
                    run.output_file_id = report["files"][-1]["file_id"] if report["files"] else None
    except Exception as exc:
        # 多 sheet 导入也可能在中间出错，不能仅清理完整返回的 loaded 列表。
        for name in {table.name for table in host.ws.list_tables()} - previous_tables:
            host.ws.drop(name)
        for path in committed_paths:
            path.unlink(missing_ok=True)
        report.update(ok=False, status="cancelled" if cancel_event.is_set() else "failed", error=str(exc)[:4000],
                      files=[], table_names=[], tables=[], internal_tables=[])
    finally:
        with session_scope() as db:
            run = db.get(Run, run_id)
            if run is not None:
                run.status = "success" if report["ok"] else "failed"
                run.error = report["error"]
                run.summary = ("脚本执行成功" if report["ok"] else "脚本未完成") + f"：{description[:500]}"
                run.duration_ms = int((time.perf_counter() - started) * 1000)
                run.finished_at = now()
                run.validation = {"status": "pass" if report["ok"] and report["checks"] else "not_checked" if report["ok"] else "fail",
                                  "checks": report["checks"], "outputs": report["files"], "tables": report["tables"],
                                  "result": report["result"], "warnings": report["warnings"], "script_status": report["status"]}
                steps = list(run.steps)
                steps[0] = {**steps[0], "ok": report["ok"], "stdout": report["stdout"], "stderr": report["stderr"],
                            "summary": run.summary, "error": report["error"], "duration_ms": run.duration_ms}
                run.steps = steps
    return report


def inspect_for_conversation(host, run_id: str) -> dict[str, Any]:
    with session_scope() as db:
        run = run_service.get_run(db, run_id)
        if not _scoped(host, run):
            raise ServiceError("这次脚本运行不属于当前客户或会话")
        if run.kind != "script":
            raise ServiceError("这条记录不是 Python 脚本运行")
        params = next((step.get("params", {}) for step in run.steps if step.get("tool") == "run_python_script"), {})
        return {"run_id": run.id, "status": run.status, **params, "error": run.error,
                "stdout": run.steps[0].get("stdout", "") if run.steps else "",
                "stderr": run.steps[0].get("stderr", "") if run.steps else "",
                "checks": (run.validation or {}).get("checks", []), "result": (run.validation or {}).get("result", {}),
                "files": (run.validation or {}).get("outputs", [])}
