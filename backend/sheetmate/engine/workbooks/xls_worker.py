"""在可终止的独立进程中转换 XLS，解析和保存也计入时间及内存限制。"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from sheetmate.engine.workbooks.atomic import atomic_path
from sheetmate.engine.workbooks.reader import ReadError

MAX_RSS_BYTES = 1536 * 1024 * 1024
MAX_OUTPUT_BYTES = 100 * 1024 * 1024


class XlsConversionTimeout(ReadError):
    pass


class XlsConversionCancelled(ReadError):
    pass


def _rss_bytes(pid: int) -> int:
    if sys.platform.startswith("linux"):
        try:
            for line in Path(f"/proc/{pid}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
        except (OSError, ValueError):
            pass
    elif sys.platform == "darwin":
        try:
            result = subprocess.run(["/bin/ps", "-o", "rss=", "-p", str(pid)],
                                    capture_output=True, text=True, timeout=1)
            if result.stdout.strip().isdigit():
                return int(result.stdout.strip()) * 1024
        except (OSError, subprocess.TimeoutExpired):
            pass
    elif sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class MemoryCounters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        *[(name, ctypes.c_size_t) for name in (
                            "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                            "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        handle = kernel.OpenProcess(0x0400 | 0x0010, False, pid)
        if handle:
            try:
                counters = MemoryCounters()
                counters.cb = ctypes.sizeof(counters)
                psapi = ctypes.WinDLL("psapi", use_last_error=True)
                psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
                if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                    return counters.WorkingSetSize
            finally:
                kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                kernel.CloseHandle(handle)
    return 0


def _temporary_size(root: Path) -> int:
    total = 0
    for path in root.iterdir():
        try:
            if path.is_file():
                total += path.stat().st_size
        except FileNotFoundError:
            pass  # 原子保存会在统计期间替换临时文件。
    return total


def _check(deadline: float, cancel_event: Any) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise XlsConversionCancelled("XLS 转换已停止")
    if time.monotonic() >= deadline:
        raise XlsConversionTimeout("XLS 转换超过运行时限，请缩小工作簿后重试")


def convert_xls_in_worker(source: Path, destination: Path, *, timeout_seconds: float = 30,
                          cancel_event: Any = None, max_cells: int = 1_000_000):
    """只在转换、报告和限制检查全部通过后原子写入目标；停止时先结束子进程再清理。"""
    from sheetmate.engine.workbooks.xls_convert import XlsConversion

    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination:
        raise ReadError("XLS 转换必须另存副本，不能覆盖原文件")
    if (not math.isfinite(timeout_seconds) or timeout_seconds <= 0
            or not isinstance(max_cells, int) or max_cells < 1):
        raise ReadError("XLS 转换的运行时限和单元格上限必须为正数")
    deadline = time.monotonic() + timeout_seconds
    _check(deadline, cancel_event)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".sheetmate_xls_", dir=destination.parent) as directory:
        root = Path(directory)
        output, response = root / "converted.xlsx", root / "response.json"
        command = [sys.executable, "-m", "sheetmate.engine.workbooks.xls_worker", str(source),
                   str(output), str(response), str(timeout_seconds), str(max_cells)]
        environment = os.environ.copy()
        environment.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
        options = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if sys.platform == "win32" else {}
        with subprocess.Popen(command, cwd=Path(__file__).resolve().parents[3], env=environment,
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options) as process:
            try:
                next_resource_check = 0
                while process.poll() is None:
                    _check(deadline, cancel_event)
                    if time.monotonic() >= next_resource_check:
                        if _rss_bytes(process.pid) > MAX_RSS_BYTES:
                            raise ReadError("XLS 转换内存超过上限，请分拆工作簿后重试")
                        if _temporary_size(root) > MAX_OUTPUT_BYTES:
                            raise ReadError("XLS 转换临时输出超过 100 MB 上限")
                        next_resource_check = time.monotonic() + 0.2
                    time.sleep(min(0.05, max(0, deadline - time.monotonic())))
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
        _check(deadline, cancel_event)
        if not response.is_file() or response.stat().st_size > 1_000_000:
            raise ReadError(f"XLS 转换进程未能完成（状态 {process.returncode}）")
        try:
            result = json.loads(response.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ReadError("XLS 转换没有返回有效报告") from exc
        if not result.get("ok"):
            raise ReadError(result.get("error") or "XLS 转换失败")
        if process.returncode != 0 or not output.is_file():
            raise ReadError("XLS 转换未生成完整的工作簿")
        if output.stat().st_size > MAX_OUTPUT_BYTES:
            raise ReadError("XLS 转换输出超过 100 MB 上限")
        report = XlsConversion(**result["report"])
        with atomic_path(destination) as staging:
            shutil.copyfile(output, staging)
            _check(deadline, cancel_event)
        return report


def _main() -> None:
    source, output, response, seconds, cells = sys.argv[1:]
    try:
        if sys.platform != "win32":
            import resource

            cpu = max(1, math.ceil(float(seconds)))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
            resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES))
            if sys.platform.startswith("linux"):
                resource.setrlimit(resource.RLIMIT_AS, (MAX_RSS_BYTES, MAX_RSS_BYTES))
        from sheetmate.engine.workbooks.xls_convert import convert_xls_to_xlsx

        report = convert_xls_to_xlsx(Path(source), Path(output), timeout_seconds=float(seconds), max_cells=int(cells))
        result = {"ok": True, "report": asdict(report)}
    except Exception as exc:
        result = {"ok": False, "error": str(exc)[:4000] or type(exc).__name__}
    Path(response).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    _main()
