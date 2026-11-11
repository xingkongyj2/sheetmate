"""约束模块依赖方向，并验证移动目录后的冷启动与跨进程序列化。"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import threading
from pathlib import Path

import pandas as pd
import pytest

BACKEND = Path(__file__).resolve().parents[1]
PACKAGE = BACKEND / "sheetmate"


def _imports(path: Path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
        elif isinstance(node, ast.Import):
            yield from (item.name for item in node.names)


@pytest.mark.parametrize(("layer", "forbidden"), [
    ("core", ("sheetmate.agent", "sheetmate.api", "sheetmate.services", "sheetmate.db",
              "sheetmate.engine", "sheetmate.recipe", "sqlalchemy", "fastapi")),
    ("db", ("sheetmate.agent", "sheetmate.api", "sheetmate.services", "sheetmate.engine",
            "sheetmate.recipe", "fastapi")),
    ("engine", ("sheetmate.agent", "sheetmate.api", "sheetmate.services", "sheetmate.db",
                "sheetmate.recipe", "sqlalchemy", "fastapi")),
    ("agent", ("sheetmate.api", "sheetmate.services", "sheetmate.db", "sqlalchemy", "fastapi")),
    ("recipe", ("sheetmate.api", "sheetmate.services", "sheetmate.db",
                "sheetmate.agent.framework", "sheetmate.agent.tools", "sqlalchemy", "fastapi")),
    ("services", ("sheetmate.api", "fastapi")),
    ("api", ("sheetmate.agent", "sheetmate.engine", "sheetmate.recipe")),
])
def test_layer_dependencies_point_toward_interfaces(layer, forbidden):
    violations = [
        f"{path.relative_to(PACKAGE)} -> {module}"
        for path in (PACKAGE / layer).rglob("*.py")
        for module in _imports(path)
        if any(module == prefix or module.startswith(prefix + ".") for prefix in forbidden)
    ]
    assert not violations, "\n".join(violations)


def test_all_packages_import_in_a_fresh_process(tmp_path):
    """Catch stale imports and eager package initialization outside pytest's module cache."""
    environment = {**os.environ, "SHEETMATE_DATA_DIR": str(tmp_path), "SEED_DEMO": "0",
                   "PYTHONPATH": str(BACKEND)}
    result = subprocess.run([
        sys.executable, "-c",
        "import importlib, pkgutil, sheetmate; "
        "modules = sorted(item.name for item in pkgutil.walk_packages(sheetmate.__path__, 'sheetmate.')); "
        "[importlib.import_module(name) for name in modules]; "
        "from sheetmate.app import app; "
        "assert any(route.path == '/api/health' for route in app.routes)",
    ], cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_detached_tool_worker_round_trip_after_package_move():
    """Exercise the real -m entry point and relocated ToolResult pickle class."""
    from sheetmate.agent.tools.contracts import AgentContext
    from sheetmate.agent.tools.worker import run_data_tool
    from sheetmate.engine.formulas import FormulaLibrary
    from sheetmate.engine.tables.workspace import Workspace

    workspace = Workspace()
    try:
        workspace.put_dataframe("orders", pd.DataFrame({"金额": [10, 20]}))
        context = AgentContext("analyze", workspace, FormulaLibrary(), None, None, "architecture-test")
        result = run_data_tool("table_info", {"table": "orders"}, context, threading.Event())
        assert result.ok
        assert "金额" in result.text
        assert workspace.df("orders")["金额"].tolist() == [10, 20]
        assert workspace.require("orders").probe_memory
    finally:
        workspace.close()


def test_backend_resource_paths_survive_package_move():
    from sheetmate.core.config.settings import BACKEND_DIR, PROJECT_DIR
    from sheetmate.engine.scripts import runner

    assert BACKEND_DIR == BACKEND
    assert PROJECT_DIR == BACKEND.parent
    assert runner._venv_python().is_file()
    assert Path(runner.__file__).with_name("worker.py").is_file()
