"""Killable, detached execution for trusted pure workspace tools.

No host, database session, live DuckDB connection or model credential is
sent to the child. The parent alone publishes a successful result.
"""

from __future__ import annotations

import os
import pickle
import signal
import subprocess
import sys
import threading

import sheetmate
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from sheetmate.engine.tables.workspace import Workspace


_SINGLE_TABLE_READS = frozenset({"table_info", "value_counts", "column_stats", "preview_table"})


# 只读、单表的轻量工具：表不大时子进程启动（约 0.4 秒）远比计算本身（毫秒级）昂贵，直接在进程内执行。
# 大表仍走可杀死的子进程。超时/取消依旧由调用方的协作式取消和 DuckDB interrupt 负责。
IN_PROCESS_READ_MAX_CELLS = 400_000


def runs_in_process(name: str, args: dict[str, Any], ctx: Any) -> bool:
    """小表上的纯读取工具是否可以不经子进程执行。任何不确定都回到子进程，由子进程路径给出原有的报错。"""
    if name not in _SINGLE_TABLE_READS:
        return False
    try:
        from sheetmate.agent.tools.presentation import _assert_table_scope

        table = args["table"]
        _assert_table_scope(ctx, [table])
        info = ctx.ws.require(table)
        return info.rows * max(1, len(info.columns)) <= IN_PROCESS_READ_MAX_CELLS
    except Exception:
        return False


class DataWorkerCancelled(BaseException):
    pass


def _input_tables(name: str, args: dict[str, Any], ctx: Any) -> list[str] | None:
    """Resolve trusted dependencies without running data-dependent builders.

    A formula's compile step can scan lookup/pivot inputs. Keep that work in
    the killable child; only its declared table parameters and parsed SQL
    expressions are inspected here. Unknown tools retain the full snapshot.
    """
    from sheetmate.agent.tools.presentation import _assert_table_scope
    from sheetmate.agent.tools.sql_scope import checked_sql_tables
    from sheetmate.engine.formulas.base import normalize_params, render_template, template_inputs
    from sheetmate.engine.tables.sql import assert_single_select

    if name in _SINGLE_TABLE_READS:
        references = [args["table"]]
    elif name == "run_sql":
        references = checked_sql_tables(ctx, assert_single_select(args["sql"]))
    elif ctx.library.has(name):
        resolved = ctx.library.resolve(name)
        params = normalize_params(resolved.spec, {key: value for key, value in args.items()
                                                  if key != "result_name"}, ctx.ws)
        references = template_inputs(resolved.spec, params)
        try:
            if resolved.builtin is None:
                references.extend(checked_sql_tables(ctx, render_template(resolved.spec, params)))
            else:
                for key, spec in resolved.spec.params.items():
                    value = params.get(key)
                    if value is not None and spec.type in {"expr", "sql"}:
                        sql = f"SELECT ({value})" if spec.type == "expr" else str(value)
                        references.extend(checked_sql_tables(ctx, sql))
        except ValueError:
            # Some valid expressions cannot be parsed in isolation. Do not
            # guess their input scope or change their execution semantics.
            return None
    else:
        return None
    references = list(dict.fromkeys(references))
    _assert_table_scope(ctx, references)
    return references


def _snapshot_request(name: str, args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    with ctx.ws.locked():
        dependencies = _input_tables(name, args, ctx)
        if dependencies is None:
            state = ctx.ws.export_state()
            reserved_names = []
        else:
            # Names reserve all existing results without transferring their
            # data. The child must choose the same unique name as the parent.
            reserved_names = [info.name for info in ctx.ws.list_tables()]
            if name == "run_sql" and args.get("result_name"):
                from sheetmate.agent.tools.analysis.sql_reuse import empty_query_key, reusable_empty_query

                cached = reusable_empty_query(ctx, empty_query_key(ctx.ws, args["sql"]))
                if cached is not None and cached.name not in dependencies:
                    # At most one empty frame crosses the worker boundary, so
                    # a repeated query can reuse its receipt in the child too.
                    dependencies = [*dependencies, cached.name]
            state = [{"meta": ctx.ws.require(table).to_dict(), "frame": ctx.ws.df(table)}
                     for table in dependencies]
    return {"name": name, "args": args, "state": state, "mode": ctx.mode,
            "conversation_id": ctx.conversation_id, "table_filter": ctx.table_filter,
            "customer": ctx.customer, "formulas": [formula.model_dump() for formula in ctx.library.customer_specs()],
            "incremental": dependencies is not None, "reserved_names": reserved_names}


class _WorkerWorkspace(Workspace):
    def __init__(self, reserved_names: list[str]):
        self._reserved_names = frozenset(reserved_names)
        super().__init__(None)

    def exists(self, name: str) -> bool:
        return name in self._reserved_names or super().exists(name)


def _changed_state(ws: Workspace, original: list[dict[str, Any]]) -> list[dict[str, Any]]:
    before = {item["meta"]["name"]: item["meta"] for item in original}
    changed = []
    for info in ws.list_tables():
        meta = info.to_dict()
        previous = before.get(info.name)
        if previous is None or previous["revision"] != info.revision:
            changed.append({"meta": meta, "frame": ws.df(info.name)})
        elif previous != meta:
            # Existing revisions only update probe metadata on publication;
            # import_state never reads their frame. Keep all metadata fields,
            # including internal source/row lineage added by other engines.
            changed.append({"meta": meta})
    return changed


def run_data_tool(name: str, args: dict[str, Any], ctx: Any, cancelled: threading.Event) -> Any:
    if cancelled.is_set():
        raise DataWorkerCancelled()
    request = _snapshot_request(name, args, ctx)
    state = request["state"]
    if cancelled.is_set():
        raise DataWorkerCancelled()
    baseline = {item["meta"]["name"]: item["meta"]["revision"] for item in state}
    with TemporaryDirectory(prefix="sheetmate_data_worker_") as directory:
        root = Path(directory)
        source, output = root / "request.pkl", root / "result.pkl"
        source.write_bytes(pickle.dumps(request, protocol=pickle.HIGHEST_PROTOCOL))
        command = [sys.executable, "-m", "sheetmate.agent.tools.worker", str(source), str(output)]
        options = {"start_new_session": True} if os.name != "nt" else {}
        environment = {**os.environ, "PYTHONPATH": str(Path(sheetmate.__file__).resolve().parent.parent)}
        with (root / "stderr.txt").open("wb") as stderr:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=stderr, env=environment, **options)
            try:
                while process.poll() is None:
                    if cancelled.wait(0.025):
                        raise DataWorkerCancelled()
                if cancelled.is_set():
                    raise DataWorkerCancelled()
                if process.returncode != 0 or not output.is_file():
                    raise RuntimeError("数据计算子进程未正常结束，本次结果未发布。")
            finally:
                if process.poll() is None:
                    if os.name != "nt":
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    else:
                        process.kill()
                    process.wait()  # A killed worker must really exit before resources can be reused.
        response = pickle.loads(output.read_bytes())
        if response.get("error"):
            raise RuntimeError(response["error"])
        if cancelled.is_set():
            raise DataWorkerCancelled()
        if response["result"].ok:
            published = response["state"]
            if getattr(ctx, "parallel_read_only", False):
                # Sibling reads started with older snapshots of other tables.
                # Publishing their full snapshots would overwrite fresh probes.
                target = args["table"]
                published = [item for item in published if item["meta"]["name"] == target]
                baseline = {target: baseline[target]}
            ctx.ws.import_state(published, baseline=baseline)
            if ctx.table_filter is not None and not getattr(ctx, "parallel_read_only", False):
                ctx.table_filter.update(response["table_filter"] or set())
        return response["result"]


def main(source: Path, output: Path) -> None:
    from sheetmate.agent.tools.contracts import AgentContext, validate_tool_args
    from sheetmate.agent.tools.registry import ToolRegistry
    from sheetmate.engine.formulas import Formula, FormulaLibrary

    request = pickle.loads(source.read_bytes())
    ws = _WorkerWorkspace(request.get("reserved_names", []))
    try:
        ws.import_state(request["state"])
        ctx = AgentContext(mode=request["mode"], ws=ws,
                           library=FormulaLibrary([Formula.model_validate(item) for item in request["formulas"]]),
                           host=None, llm=None, conversation_id=request["conversation_id"], customer=request["customer"],
                           table_filter=request["table_filter"])
        tool = ToolRegistry().find(request["name"], ctx)
        if tool is None or not tool.process_safe or tool.run is None:
            raise ValueError("该工具不支持隔离数据计算。")
        result = tool.run(validate_tool_args(tool, request["args"]), ctx)
        state = []
        if result.ok:
            state = _changed_state(ws, request["state"]) if request.get("incremental") else ws.export_state()
        response = {"result": result, "state": state, "table_filter": ctx.table_filter}
    except Exception as exc:
        response = {"error": str(exc)}
    finally:
        ws.close()
    output.write_bytes(pickle.dumps(response, protocol=pickle.HIGHEST_PROTOCOL))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
