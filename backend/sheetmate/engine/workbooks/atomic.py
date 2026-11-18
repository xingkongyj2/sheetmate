"""原子落盘：先写同目录临时文件，成功后 os.replace 到目标路径。

目标路径上要么是旧的完整文件、要么是新的完整文件，不会被读到写了一半的内容；并且每次都是新的 inode，
不会改动任何指向旧文件的硬链接——交付件用硬链接代替拷贝时，这是"已交付文件不可变"的前提。
"""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


REPLACE_WAIT_SECONDS = 30          # Windows 上目标文件被占用时最多等多久
_RETRY_LOCKED = os.name == "nt"     # 只有 Windows 会因为「别的程序正打开着这个文件」而替换失败


def replace_file(source: Path | str, target: Path | str, *, wait: float = REPLACE_WAIT_SECONDS) -> None:
    """os.replace，Windows 上遇到「拒绝访问」（目标或临时文件正被杀毒软件、索引服务、正在读这个文件的请求短暂占用）时
    隔一会儿再试，最多等 wait 秒；还不行就照常抛出 PermissionError，由调用方决定怎么办。"""
    deadline, delay = time.monotonic() + wait, 0.05
    while True:
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if not _RETRY_LOCKED or time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 1.0)


@contextmanager
def atomic_path(path: Path) -> Iterator[Path]:
    """yield 一个与 path 同目录、同扩展名的临时路径；with 块正常结束才替换到 path，异常则丢弃临时文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=".sheetmate_atomic_", suffix=path.suffix, dir=path.parent, delete=False) as handle:
        staging = Path(handle.name)
    try:
        yield staging
        replace_file(staging, path)
    finally:
        staging.unlink(missing_ok=True)


def link_or_copy(source: Path, destination: Path) -> bool:
    """只新建 destination（已存在则抛 FileExistsError，绝不覆盖）。优先硬链接，跨文件系统等不支持时拷贝。

    返回是否为硬链接。仅适用于 source 此后不会被原地改写的文件（本项目的写出路径均为原子替换）。
    """
    import shutil

    try:
        os.link(source, destination)
        return True
    except FileExistsError:
        raise
    except OSError:
        with Path(source).open("rb") as src, Path(destination).open("xb") as dst:
            shutil.copyfileobj(src, dst)
        return False
