"""原子落盘：先写同目录临时文件，成功后 os.replace 到目标路径。

目标路径上要么是旧的完整文件、要么是新的完整文件，不会被读到写了一半的内容；并且每次都是新的 inode，
不会改动任何指向旧文件的硬链接——交付件用硬链接代替拷贝时，这是"已交付文件不可变"的前提。
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def atomic_path(path: Path) -> Iterator[Path]:
    """yield 一个与 path 同目录、同扩展名的临时路径；with 块正常结束才替换到 path，异常则丢弃临时文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=".sheetmate_atomic_", suffix=path.suffix, dir=path.parent, delete=False) as handle:
        staging = Path(handle.name)
    try:
        yield staging
        os.replace(staging, path)
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
