"""使用项目 .venv 执行 Python 表格脚本；macOS / Linux 使用系统沙箱。"""

from __future__ import annotations

import csv
import datetime as dt
import decimal
import json
import math
import os
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import numpy as np
import openpyxl
import pandas as pd

MAX_SCRIPT_BYTES = 100_000
MAX_FILES = 20
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
MAX_LOG_BYTES = 128 * 1024
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_RSS_BYTES = 1536 * 1024 * 1024
MAX_TEMP_ENTRIES = 2000
MAX_TABLES = 20
MAX_ROWS = 100_000
MAX_COLUMNS = 256
MAX_CELLS = 1_000_000
_SUFFIXES = {".xlsx", ".xlsm", ".csv"}


@dataclass
class ScriptResult:
    ok: bool = False
    status: str = "failed"
    stdout: str = ""
    stderr: str = ""
    error: str = ""
    result_tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    display_tables: list[str] = field(default_factory=list)
    output_files: list[Path] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0


def _encode(value: Any) -> Any:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, (pd.Timestamp, dt.datetime)):
        return {"__type__": "datetime", "value": value.isoformat()}
    if isinstance(value, dt.date):
        return {"__type__": "date", "value": value.isoformat()}
    if isinstance(value, decimal.Decimal):
        return {"__type__": "decimal", "value": str(value)}
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (dt.time, dt.timedelta)):
        return str(value)
    try:
        if pd.isna(value):
            return None
    except (ValueError, TypeError):
        pass
    return str(value)


def _decode(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) != {"__type__", "value"} or not isinstance(value["value"], str):
            raise ValueError("脚本返回无效的单元格类型")
        kind, text = value["__type__"], value["value"]
        if kind == "datetime":
            return dt.datetime.fromisoformat(text)
        if kind == "date":
            return dt.date.fromisoformat(text)
        if kind == "decimal":
            return decimal.Decimal(text)
        raise ValueError("脚本返回未知的单元格类型")
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, str) and len(value) > 32767:
            raise ValueError("脚本返回的单元格文字过长")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("脚本返回的数字必须为有限值")
        return value
    raise ValueError("脚本返回的单元格类型不受支持")


def _frame_payload(frame: pd.DataFrame) -> dict[str, Any]:
    if not isinstance(frame, pd.DataFrame):
        raise ValueError("脚本输入表必须是 DataFrame")
    rows, columns = frame.shape
    if rows > MAX_ROWS or columns > MAX_COLUMNS or rows * columns > MAX_CELLS:
        raise ValueError("脚本输入表超过行数、列数或单元格上限")
    return {"columns": [str(column) for column in frame.columns],
            "rows": [[_encode(value) for value in row] for row in frame.itertuples(index=False, name=None)]}


def _read_frames(payload: Any) -> dict[str, pd.DataFrame]:
    if not isinstance(payload, dict) or len(payload) > MAX_TABLES:
        raise ValueError("脚本返回的表格数量无效")
    frames, total = {}, 0
    for name, item in payload.items():
        if not isinstance(name, str) or not name.strip() or len(name) > 120 or not isinstance(item, dict):
            raise ValueError("脚本返回的表名无效")
        columns, rows = item.get("columns"), item.get("rows")
        if not isinstance(columns, list) or not isinstance(rows, list) or len(columns) > MAX_COLUMNS or len(rows) > MAX_ROWS:
            raise ValueError("脚本返回表的结构或尺寸无效")
        if any(not isinstance(column, str) or not column or len(column) > 255 for column in columns) or len(set(columns)) != len(columns):
            raise ValueError("脚本返回表的列名必须非空且唯一")
        total += len(rows) * len(columns)
        if total > MAX_CELLS or any(not isinstance(row, list) or len(row) != len(columns) for row in rows):
            raise ValueError("脚本返回的表格单元格数量或行宽无效")
        frames[name] = pd.DataFrame([[_decode(value) for value in row] for row in rows], columns=columns)
    return frames


def _selected_names(payload: Any, *, field_name: str, maximum: int) -> list[str]:
    if not isinstance(payload, list) or len(payload) > maximum or any(not isinstance(item, str) or not item for item in payload):
        raise ValueError(f"脚本返回的 {field_name} 必须是最多 {maximum} 项的名称列表")
    if len(payload) != len(set(payload)):
        raise ValueError(f"脚本返回的 {field_name} 不能重复选择同一结果")
    return payload


def _venv_python() -> Path:
    venv = Path(__file__).resolve().parents[2] / ".venv"
    python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not python.is_file():
        raise RuntimeError(f"未找到项目虚拟环境 Python：{python}；请先在 backend 目录创建 .venv 并安装 requirements.txt 中的依赖")
    return python


def _venv_base(python: Path) -> Path:
    """沙箱放行目标 venv 的基础运行库，不依赖后端进程使用哪个 Python。"""
    config = python.parent.parent / "pyvenv.cfg"
    if config.is_file():
        for line in config.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() == "home" and value.strip():
                home = Path(value.strip()).resolve()
                return home.parent if home.name == "bin" else home
    return python.resolve().parent.parent


def _sandbox_command(root: Path) -> list[str]:
    python = _venv_python()
    real_python = python.resolve()
    venv = python.parent.parent
    worker = root / "worker.py"
    command = [str(python), "-I", "-B", "-X", "utf8", str(worker), str(root)]
    if sys.platform == "win32":
        return command
    base = _venv_base(python)
    if sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file():
        def literal(path):
            return json.dumps(str(path), ensure_ascii=False)

        allowed = ["/System", "/usr", "/Library", "/bin", "/sbin", "/dev", "/private/preboot",
                   "/private/var/db", "/private/etc", str(base),
                   str(venv / "lib"), str(venv / "bin"), str(root)]
        reads = " ".join(f"(subpath {literal(Path(path).resolve())})" for path in allowed)
        profile = ("(version 1) (deny default) "
                   f"(allow file-read* (literal \"/\") (literal {literal(venv / 'pyvenv.cfg')}) {reads}) "
                   f"(allow file-write* (subpath {literal(root)}) (literal \"/dev/null\")) "
                   f"(allow process-exec (literal {literal(python)}) (literal {literal(real_python)})) "
                   "(allow sysctl-read)")
        return ["/usr/bin/sandbox-exec", "-p", profile, *command]
    if sys.platform.startswith("linux") and shutil.which("bwrap"):
        # 保留 venv 入口，才能发现同一环境内已安装的 pandas/openpyxl。
        args = [shutil.which("bwrap"), "--die-with-parent", "--unshare-all", "--new-session", "--proc", "/proc", "--dev", "/dev"]
        mounts = {"/usr", "/bin", "/sbin", "/lib", "/lib64", str(base), str(venv / "lib"), str(venv / "bin")}
        for path in sorted(mounts):
            if Path(path).exists():
                args.extend(["--ro-bind", path, path])
        if (venv / "pyvenv.cfg").exists():
            config = str(venv / "pyvenv.cfg")
            args.extend(["--ro-bind", config, config])
        args.extend(["--bind", str(root), str(root), "--chdir", str(root), "--", *command])
        return args
    raise RuntimeError("当前系统没有可用的脚本隔离设施（macOS sandbox-exec 或 Linux bubblewrap），未执行脚本")


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        if sys.platform == "win32":
            windows = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
            taskkill = Path(windows) / "System32" / "taskkill.exe"
            try:
                subprocess.run([str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (OSError, subprocess.TimeoutExpired):
                pass
            if process.poll() is None:
                process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()
    process.wait(timeout=3)


def _regular_size(path: Path) -> int:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("脚本执行报告和日志必须是普通文件，不能使用符号链接、管道或其他特殊文件")
    return info.st_size


def _temporary_usage(root: Path) -> int:
    total = entries = 0
    for directory, folders, files in os.walk(root, followlinks=False):
        entries += len(folders) + len(files)
        if entries > MAX_TEMP_ENTRIES:
            raise ValueError("脚本创建的临时文件或目录过多，请减少中间文件")
        for name in files:
            path = Path(directory) / name
            try:
                info = path.lstat()
                if stat.S_ISREG(info.st_mode):
                    total += info.st_size
            except FileNotFoundError:
                pass  # 脚本删除中的临时文件下一轮重新统计。
    return total


def _validate_output(path: Path) -> None:
    if path.is_symlink() or not path.is_file() or path.suffix.lower() not in _SUFFIXES:
        raise ValueError("脚本输出只能是普通的 xlsx、xlsm 或 csv 文件，不能是目录或符号链接")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("单个脚本输出文件不能超过 50 MB")
    if path.suffix.lower() == ".csv":
        cells = 0
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for index, row in enumerate(csv.reader(handle), start=1):
                cells += len(row)
                if index > MAX_ROWS + 1 or len(row) > MAX_COLUMNS or cells > MAX_CELLS:
                    raise ValueError("输出 CSV 超过行数、列数或单元格上限")
        return
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 10_000 or sum(entry.file_size for entry in entries) > 256 * 1024 * 1024:
                raise ValueError("Excel 解压后的内容超过校验上限")
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=False)
        try:
            if len(workbook.worksheets) > MAX_TABLES:
                raise ValueError("Excel 输出工作表不能超过 20 张")
            cells = 0
            for sheet in workbook:
                if (sheet.max_row or 0) > MAX_ROWS or (sheet.max_column or 0) > MAX_COLUMNS:
                    raise ValueError("Excel 输出超过行数或列数上限")
                for row in sheet.iter_rows():
                    cells += len(row)
                    if cells > MAX_CELLS:
                        raise ValueError("Excel 输出累计单元格超过上限")
        finally:
            workbook.close()
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        raise ValueError("脚本生成的 Excel 文件无法读取，请修复后重新生成") from exc


def execute_script(code: str, tables: dict[str, pd.DataFrame], files: dict[str, Path], output_dir: Path, *,
                   timeout_seconds: float = 30, cancel_event: threading.Event | None = None) -> ScriptResult:
    """脚本在临时目录中执行；只有全程成功且结构合法的输出才复制到 output_dir。"""
    started = time.monotonic()
    outcome = ScriptResult()
    copied: list[Path] = []
    try:
        if not isinstance(code, str) or not code.strip() or len(code.encode("utf-8")) > MAX_SCRIPT_BYTES:
            raise ValueError("Python 脚本不能为空，且不能超过 100 KB")
        if not 0 < timeout_seconds <= 120:
            raise ValueError("脚本运行时限必须大于 0 且不超过 120 秒")
        if len(tables) > MAX_TABLES or len(files) > MAX_FILES:
            raise ValueError("脚本输入最多 20 张表和 20 个文件")
        if cancel_event is not None and cancel_event.is_set():
            outcome.status, outcome.error = "cancelled", "脚本执行已停止"
            return outcome
        with TemporaryDirectory(prefix="sheetmate_script_") as directory:
            root = Path(directory).resolve()
            (root / "inputs").mkdir()
            (root / "outputs").mkdir()
            (root / "home").mkdir()
            shutil.copyfile(Path(__file__).with_name("script_worker.py"), root / "worker.py")
            (root / "script.py").write_text(code, encoding="utf-8")
            prepared = {name: _frame_payload(frame) for name, frame in tables.items()}
            if sum(len(item["rows"]) * len(item["columns"]) for item in prepared.values()) > MAX_CELLS:
                raise ValueError("脚本输入表累计超过 1000000 个单元格")
            aliases, input_bytes = {}, 0
            for index, (alias, source) in enumerate(files.items()):
                if not isinstance(alias, str) or not alias or len(alias) > 120:
                    raise ValueError("脚本输入文件别名不能为空，且最多 120 字符")
                source = Path(source)
                input_bytes += source.stat().st_size
                if source.suffix.lower() not in _SUFFIXES | {".xls"} or input_bytes > MAX_TOTAL_BYTES:
                    raise ValueError("脚本输入文件必须是 Excel / CSV，累计不能超过 100 MB")
                filename = f"input_{index}{source.suffix.lower()}"
                shutil.copyfile(source, root / "inputs" / filename)
                aliases[alias] = filename
            manifest = {"tables": prepared, "files": aliases, "timeout_seconds": timeout_seconds}
            (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            command = _sandbox_command(root)
            environment = {"PATH": "/usr/bin:/bin", "HOME": str(root / "home"), "TMPDIR": str(root), "LANG": "en_US.UTF-8",
                           "PYTHONIOENCODING": "utf-8", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
            options = {"start_new_session": True}
            if sys.platform == "win32":
                windows = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
                environment.update(SystemRoot=windows, WINDIR=windows, TEMP=str(root), TMP=str(root),
                                   USERPROFILE=str(root / "home"),
                                   PATH=os.pathsep.join((str(Path(command[0]).parent), str(Path(windows) / "System32"))))
                options = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
            stdout_path, stderr_path = root / "stdout.log", root / "stderr.log"
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                process = subprocess.Popen(command, cwd=root, env=environment, stdin=subprocess.DEVNULL,
                                           stdout=stdout, stderr=stderr, **options)
                deadline = time.monotonic() + timeout_seconds
                try:
                    while process.poll() is None:
                        if cancel_event is not None and cancel_event.is_set():
                            outcome.status, outcome.error = "cancelled", "脚本执行已停止"
                            break
                        if time.monotonic() >= deadline:
                            outcome.status, outcome.error = "timeout", f"脚本超过 {timeout_seconds:g} 秒运行时限，请缩小数据范围或优化脚本"
                            break
                        if _regular_size(stdout_path) + _regular_size(stderr_path) > MAX_LOG_BYTES:
                            outcome.status, outcome.error = "output_limit", "脚本日志过多，请只打印摘要"
                            break
                        total_bytes = _temporary_usage(root)
                        if total_bytes > MAX_TOTAL_BYTES + input_bytes + MAX_RESPONSE_BYTES:
                            outcome.status, outcome.error = "output_limit", "脚本临时输出超过大小上限"
                            break
                        if sys.platform == "darwin":
                            rss = subprocess.run(["/bin/ps", "-o", "rss=", "-p", str(process.pid)], capture_output=True, text=True, timeout=1)
                            if rss.stdout.strip().isdigit() and int(rss.stdout.strip()) * 1024 > MAX_RSS_BYTES:
                                outcome.status, outcome.error = "memory_limit", "脚本内存使用超过上限，请分批处理"
                                break
                        time.sleep(0.05)
                finally:
                    if process.poll() is None:
                        _stop(process)
            if _regular_size(stdout_path) + _regular_size(stderr_path) > MAX_LOG_BYTES:
                outcome.status, outcome.error = "output_limit", "脚本日志过多，请只打印摘要"
            outcome.stdout = stdout_path.read_bytes()[:MAX_LOG_BYTES].decode("utf-8", errors="replace")
            outcome.stderr = stderr_path.read_bytes()[:MAX_LOG_BYTES].decode("utf-8", errors="replace")
            for attribute in ("stdout", "stderr"):
                setattr(outcome, attribute, getattr(outcome, attribute).replace(str(root), "<脚本临时目录>"))
            if outcome.error:
                return outcome
            if _temporary_usage(root) > MAX_TOTAL_BYTES + input_bytes + MAX_RESPONSE_BYTES:
                outcome.status, outcome.error = "output_limit", "脚本临时输出超过大小上限"
                return outcome
            if process.returncode != 0:
                outcome.error = f"脚本进程退出（状态 {process.returncode}），请检查错误摘要后修改脚本"
                return outcome
            response_path = root / "response.json"
            if not response_path.exists() or _regular_size(response_path) > MAX_RESPONSE_BYTES:
                raise ValueError("脚本没有返回有效的执行报告，或报告超过大小上限")
            response = json.loads(response_path.read_text(encoding="utf-8"))
            if not isinstance(response, dict) or not isinstance(response.get("ok"), bool):
                raise ValueError("脚本返回的执行报告结构无效")
            checks = response.get("checks", [])
            if not isinstance(checks, list) or len(checks) > 1000 or any(not isinstance(item, dict) or not isinstance(item.get("ok"), bool)
                    or not isinstance(item.get("message"), str) for item in checks):
                raise ValueError("脚本校验报告无效")
            outcome.checks = checks
            if not response["ok"] or any(not item["ok"] for item in checks):
                outcome.error = str(response.get("error") or "脚本校验未通过")[:4000].replace(str(root), "<脚本临时目录>")
                return outcome
            frames = _read_frames(response.get("tables", {}))
            display_tables = _selected_names(response.get("display_tables", []), field_name="display_tables", maximum=MAX_TABLES)
            if any(name not in frames for name in display_tables):
                raise ValueError("display_tables 只能选择实际存在的结果表")
            result = response.get("result", {})
            if not isinstance(result, dict) or len(json.dumps(result, ensure_ascii=False)) > 20_000:
                raise ValueError("脚本关键结果必须是简短的 JSON 对象")
            output_root = root / "outputs"
            if output_root.is_symlink() or not output_root.is_dir():
                raise ValueError("脚本输出目录不能替换为符号链接或其他文件")
            selected = _selected_names(response.get("deliver_files", []), field_name="deliver_files", maximum=MAX_FILES)
            if any(name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name for name in selected):
                raise ValueError("deliver_files 只能选择 OUTPUT_DIR 顶层文件，不能越界或引用子目录")
            outputs = [output_root / name for name in selected]
            for path in outputs:
                _validate_output(path)
            if sum(path.lstat().st_size for path in outputs) > MAX_TOTAL_BYTES:
                raise ValueError("脚本输出文件数量或大小超过上限")
            output_dir = Path(output_dir)
            if outputs:
                output_dir.mkdir(parents=True, exist_ok=True)
            for path in outputs:
                destination = output_dir / path.name
                # xb 保证只新建，不覆盖同目录已有结果或原件。
                with destination.open("xb") as target, path.open("rb") as source:
                    copied.append(destination)
                    shutil.copyfileobj(source, target)
            outcome.ok, outcome.status = True, "success"
            outcome.result_tables, outcome.output_files, outcome.result = frames, copied, result
            outcome.display_tables = display_tables
    except Exception as exc:
        outcome.error = str(exc) or type(exc).__name__
        for path in copied:
            path.unlink(missing_ok=True)
    finally:
        outcome.duration_ms = int((time.monotonic() - started) * 1000)
    return outcome
