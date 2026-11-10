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
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from sheetmate.engine.workspace import Workspace


class DataWorkerCancelled(BaseException):
    pass


def run_data_tool(name: str, args: dict[str, Any], ctx: Any, cancelled: threading.Event) -> Any:
    if getattr(ctx, "parallel_read_only", False):
        with ctx.ws.locked():
            table = ctx.ws.require(args["table"])
            state = [{"meta": table.to_dict(), "frame": ctx.ws.df(table.name)}]
    else:
        state = ctx.ws.export_state()
    if cancelled.is_set():
        raise DataWorkerCancelled()
    baseline = {item["meta"]["name"]: item["meta"]["revision"] for item in state}
    request = {"name": name, "args": args, "state": state, "mode": ctx.mode,
               "conversation_id": ctx.conversation_id, "table_filter": ctx.table_filter,
               "customer": ctx.customer, "formulas": [formula.model_dump() for formula in ctx.library.customer_specs()]}
    with TemporaryDirectory(prefix="sheetmate_data_worker_") as directory:
        root = Path(directory)
        source, output = root / "request.pkl", root / "result.pkl"
        source.write_bytes(pickle.dumps(request, protocol=pickle.HIGHEST_PROTOCOL))
        command = [sys.executable, "-m", "sheetmate.agent.tool_worker", str(source), str(output)]
        options = {"start_new_session": True} if os.name != "nt" else {}
        environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])}
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
    from sheetmate.agent.tools import AgentContext, ToolRegistry, validate_tool_args
    from sheetmate.engine.formulas import Formula, FormulaLibrary

    request = pickle.loads(source.read_bytes())
    ws = Workspace(None)
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
        response = {"result": result, "state": ws.export_state() if result.ok else [], "table_filter": ctx.table_filter}
    except Exception as exc:
        response = {"error": str(exc)}
    finally:
        ws.close()
    output.write_bytes(pickle.dumps(response, protocol=pickle.HIGHEST_PROTOCOL))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
