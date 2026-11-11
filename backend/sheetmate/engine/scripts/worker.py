"""隔离子进程入口；仅通过受限 JSON 协议与宿主交换表和执行结果。"""

from __future__ import annotations

import datetime as dt
import decimal
import json
import math
import os
import sys
import traceback
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd

MAX_TABLES = 20
MAX_ROWS = 100_000
MAX_COLUMNS = 256
MAX_CELLS = 1_000_000
MAX_FILES = 20


def encode_value(value):
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
        if isinstance(value, str) and len(value) > 32767:
            raise ValueError("单元格文字超过 Excel 的 32767 字符上限")
        return value
    if isinstance(value, (dt.time, dt.timedelta)):
        return str(value)
    try:
        if pd.isna(value):
            return None
    except (ValueError, TypeError):
        pass
    raise ValueError(f"不支持的单元格类型：{type(value).__name__}；请转换为数字、文字、日期或空值")


def decode_value(value):
    if isinstance(value, dict):
        kind, data = value.get("__type__"), value.get("value")
        if kind == "datetime":
            return dt.datetime.fromisoformat(data)
        if kind == "date":
            return dt.date.fromisoformat(data)
        if kind == "decimal":
            return decimal.Decimal(data)
        raise ValueError("无效的单元格类型")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("单元格只能是数字、文字、日期或空值")


def encode_frame(frame):
    if not isinstance(frame, pd.DataFrame):
        raise ValueError("result_tables 的每个值必须是 pandas DataFrame")
    rows, columns = frame.shape
    if rows > MAX_ROWS or columns > MAX_COLUMNS or rows * columns > MAX_CELLS:
        raise ValueError("结果表超过行数、列数或单元格数量上限")
    names = [str(column) for column in frame.columns]
    if len(names) != len(set(names)) or any(not name or len(name) > 255 for name in names):
        raise ValueError("结果表列名必须非空、唯一且不超过 255 字符")
    return {"columns": names,
            "rows": [[encode_value(value) for value in row] for row in frame.itertuples(index=False, name=None)]}


def decode_frame(data):
    return pd.DataFrame([[decode_value(value) for value in row] for row in data["rows"]], columns=data["columns"])


def delivery_names(items, output_root):
    """只登记显式选中的顶层文件；同目录其他文件始终视为运行临时文件。"""
    if not isinstance(items, list) or len(items) > MAX_FILES:
        raise ValueError("deliver_files 必须是最多 20 个文件的列表")
    names = []
    for item in items:
        if not isinstance(item, (str, Path)) or not str(item):
            raise ValueError("deliver_files 只能包含 OUTPUT_DIR 下的文件 Path 或文件名")
        path = Path(item)
        if path.is_absolute():
            if path.parent != output_root:
                raise ValueError("deliver_files 只能选择 OUTPUT_DIR 顶层文件，不能越界或引用子目录")
            name = path.name
        else:
            if len(path.parts) != 1:
                raise ValueError("deliver_files 只能选择 OUTPUT_DIR 顶层文件，不能越界或引用子目录")
            name = str(path)
        if name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
            raise ValueError("deliver_files 中的文件名无效")
        if name in names:
            raise ValueError("deliver_files 不能重复选择同一文件")
        names.append(name)
    return names


def displayed_table_names(items, tables):
    if not isinstance(items, list) or len(items) > MAX_TABLES or any(not isinstance(item, str) for item in items):
        raise ValueError("display_tables 必须是结果表名称列表")
    if len(items) != len(set(items)):
        raise ValueError("display_tables 不能重复选择同一结果表")
    if any(item not in tables for item in items):
        raise ValueError("display_tables 只能选择 result_tables 中实际存在的结果表")
    return items


def _limits(seconds):
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CPU, (max(1, math.ceil(seconds)), max(2, math.ceil(seconds) + 1)))
        resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
        if hasattr(resource, "RLIMIT_NPROC"):
            resource.setrlimit(resource.RLIMIT_NPROC, (1, 1))
        # macOS 的 RLIMIT_AS 对当前 Python/NumPy 不可靠；父进程另外监测实际 RSS。
        if sys.platform.startswith("linux"):
            maximum = 2 * 1024 * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (maximum, maximum))
    except (ImportError, ValueError, OSError):
        pass


def _block_linux_process_creation():
    """bubblewrap 限制文件和网络；不可撤销的 seccomp 补充禁止派生进程。"""
    if not sys.platform.startswith("linux"):
        return
    import ctypes
    import errno
    import platform

    architecture = platform.machine().lower()
    if architecture in {"x86_64", "amd64"}:
        audit_architecture = 0xC000003E  # AUDIT_ARCH_X86_64
        denied = [56, 57, 58, 59, 322, 435]  # clone/fork/vfork/execve/execveat/clone3
    elif architecture in {"aarch64", "arm64"}:
        audit_architecture = 0xC00000B7  # AUDIT_ARCH_AARCH64
        denied = [220, 221, 281, 435]
    else:
        raise RuntimeError("当前 Linux 架构缺少脚本子进程隔离规则，未执行脚本")

    class Filter(ctypes.Structure):
        _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte), ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint)]

    class Program(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ushort), ("filters", ctypes.POINTER(Filter))]

    # 先验证 syscall ABI，阻止 x86_64 的 int 0x80 等兼容 ABI 使用其他 syscall 编号。
    instructions = [Filter(0x20, 0, 0, 4),  # seccomp_data.arch
                    Filter(0x15, 1, 0, audit_architecture),
                    Filter(0x06, 0, 0, 0x80000000),  # SECCOMP_RET_KILL_PROCESS
                    Filter(0x20, 0, 0, 0)]  # seccomp_data.nr
    # x32 ABI 另有 syscall 编号；拒绝以免绕过 x86_64 规则。
    instructions.extend([Filter(0x35, 0, 1, 0x40000000), Filter(0x06, 0, 0, 0x00050000 | errno.EPERM)])
    for syscall in denied:
        instructions.extend([Filter(0x15, 0, 1, syscall), Filter(0x06, 0, 0, 0x00050000 | errno.EPERM)])
    instructions.append(Filter(0x06, 0, 0, 0x7FFF0000))
    array = (Filter * len(instructions))(*instructions)
    program = Program(len(instructions), array)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0 or libc.prctl(22, 2, ctypes.byref(program)) != 0:
        raise RuntimeError("无法启用脚本子进程隔离，未执行脚本")


def main():
    root = Path(sys.argv[1]).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    _limits(float(manifest["timeout_seconds"]))
    checks = []

    def check(condition, message):
        passed = bool(condition)
        checks.append({"message": str(message)[:500], "ok": passed})
        if not passed:
            raise AssertionError(str(message))

    scope = {"__name__": "__sheetmate_script__", "pd": pd, "np": np, "openpyxl": openpyxl,
             "tables": {name: decode_frame(data) for name, data in manifest["tables"].items()},
             "files": {alias: root / "inputs" / name for alias, name in manifest["files"].items()},
             "OUTPUT_DIR": root / "outputs", "result_tables": {}, "result": {}, "check": check,
             "deliver_files": [], "display_tables": []}
    response = {"ok": False, "error": "", "checks": checks, "tables": {}, "result": {},
                "deliver_files": [], "display_tables": []}
    try:
        _block_linux_process_creation()
        code = (root / "script.py").read_text(encoding="utf-8")
        exec(compile(code, "<sheetmate-script>", "exec"), scope, scope)
        output_tables = scope.get("result_tables")
        if not isinstance(output_tables, dict) or len(output_tables) > MAX_TABLES:
            raise ValueError("result_tables 必须是最多 20 张表的字典")
        response["tables"] = {str(name): encode_frame(frame) for name, frame in output_tables.items()}
        response["display_tables"] = displayed_table_names(scope.get("display_tables"), response["tables"])
        response["deliver_files"] = delivery_names(scope.get("deliver_files"), root / "outputs")
        if sum(len(data["rows"]) * len(data["columns"]) for data in response["tables"].values()) > MAX_CELLS:
            raise ValueError("全部结果表累计不能超过 1000000 个单元格")
        result = scope.get("result", {})
        if not isinstance(result, dict):
            raise ValueError("result 必须是简短的 JSON 对象")
        if len(json.dumps(result, ensure_ascii=False, allow_nan=False)) > 20_000:
            raise ValueError("result 过长，请只保留关键统计结果")
        response["result"] = result
        response["ok"] = all(item["ok"] for item in checks)
        if not response["ok"]:
            response["error"] = "脚本的业务校验未通过"
    except BaseException as exc:
        response.update(ok=False, error=f"{type(exc).__name__}: {exc}", tables={}, result={}, deliver_files=[], display_tables=[])
        traceback.print_exc(limit=8)
    (root / "response.json").write_text(json.dumps(response, ensure_ascii=False, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main()
