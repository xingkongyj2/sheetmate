"""最近读过的工作簿放在内存里，文件一变就失效。

一个文件上传之后短时间内会被读好几遍：算画像、挑出要逐个检查的值、改成脱敏版、载入工作区、生成给脚本用的副本……
大文件解析一遍就要好几秒，每一步各读一遍，光读文件就占了大半时间。这里按「路径 + 修改时间 + 大小 + 文件号」缓存，
文件被改写（比如原地换成令牌版）之后键就变了，自然重新读。

拿到的是浅拷贝：pandas 3 写时复制，调用方改自己手里的表不会影响缓存里的那份。
"""

from __future__ import annotations

import dataclasses
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Callable

from sheetmate.engine.workbooks.reader import SheetData, read_workbook

MAX_BOOKS = 2

_books: OrderedDict[tuple[str, int, int, int], list[SheetData]] = OrderedDict()
_lock = threading.Lock()
_reading: dict[tuple[str, int, int, int], threading.Lock] = {}


def _key(path: Path) -> tuple[str, int, int, int]:
    stat = os.stat(path)
    return (str(Path(path).resolve()), stat.st_mtime_ns, stat.st_size, stat.st_ino)


def fingerprint(path: str | Path) -> tuple[str, int, int, int]:
    """文件的「指纹」：路径 + 修改时间 + 大小 + 文件号。内容被改写后指纹就变，拿它做缓存键。文件不存在时抛 OSError。"""
    return _key(Path(path))


def _copy(sheets: list[SheetData]) -> list[SheetData]:
    return [dataclasses.replace(sheet, df=sheet.df.copy(deep=False), quality=dict(sheet.quality or {})) for sheet in sheets]


def read_cached(path: str | Path, loader: Callable[[Path], list[SheetData]] = read_workbook) -> list[SheetData]:
    """读工作簿；同一个文件内容没变就直接用上次读的结果。同时有两处要读同一个文件时只解析一次。"""
    path = Path(path)
    key = _key(path)
    with _lock:
        hit = _books.get(key)
        if hit is not None:
            _books.move_to_end(key)
            return _copy(hit)
        reading = _reading.setdefault(key, threading.Lock())
    with reading:
        with _lock:
            hit = _books.get(key)
            if hit is not None:
                return _copy(hit)
        try:
            sheets = loader(path)
            with _lock:
                _books[key] = sheets
                while len(_books) > MAX_BOOKS:
                    _books.popitem(last=False)
        finally:
            with _lock:
                _reading.pop(key, None)
    return _copy(sheets)


def clear() -> None:
    with _lock:
        _books.clear()
