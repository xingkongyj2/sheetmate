"""OOXML 包（zip）的读写：未改动的部件保存时按字节原样复制。"""

from __future__ import annotations

import posixpath
from pathlib import PurePosixPath
from types import TracebackType
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from sheetmate.engine.ooxml.errors import OoxmlUnsupported
from sheetmate.engine.workbooks.atomic import atomic_path

MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
WORKBOOK = "xl/workbook.xml"
WORKBOOK_RELS = "xl/_rels/workbook.xml.rels"
CONTENT_TYPES = "[Content_Types].xml"


class OoxmlPackage:
    """一个 .xlsx / .xlsm 包。只记录被改动 / 新增 / 删除的部件，其余部件由原 zip 直接复制。"""

    def __init__(self, path):
        self.path = path
        self._zip = ZipFile(path)
        self._infos = self._zip.infolist()
        self._known = {info.filename for info in self._infos}
        self._replaced: dict[str, bytes] = {}
        self._deleted: set[str] = set()

    def __enter__(self) -> "OoxmlPackage":
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> None:
        self.close()

    def close(self) -> None:
        self._zip.close()

    # ---- 部件读写 ----
    def names(self) -> list[str]:
        result = [info.filename for info in self._infos if info.filename not in self._deleted]
        result.extend(name for name in self._replaced if name not in self._known)
        return result

    def exists(self, name: str) -> bool:
        return name not in self._deleted and (name in self._replaced or name in self._known)

    def read(self, name: str) -> bytes:
        if name in self._deleted or (name not in self._replaced and name not in self._known):
            raise OoxmlUnsupported(f"包中没有部件 {name}")
        return self._replaced[name] if name in self._replaced else self._zip.read(name)

    def read_text(self, name: str) -> str:
        try:
            return self.read(name).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise OoxmlUnsupported(f"部件 {name} 不是 UTF-8 编码") from exc

    def write(self, name: str, data: bytes | str) -> None:
        self._deleted.discard(name)
        self._replaced[name] = data.encode("utf-8") if isinstance(data, str) else data

    def delete(self, name: str) -> None:
        self._replaced.pop(name, None)
        if name in self._known:
            self._deleted.add(name)

    def changed_parts(self) -> list[str]:
        return sorted(set(self._replaced) | self._deleted)

    # ---- 工作表 / 关系 ----
    def sheet_parts(self) -> dict[str, str]:
        """工作表名 → 部件路径（按工作簿中的顺序）。"""
        workbook = ElementTree.fromstring(self.read(WORKBOOK))
        relations = {rel.get("Id"): rel.get("Target", "")
                     for rel in ElementTree.fromstring(self.read(WORKBOOK_RELS)).iter(f"{PKG_REL_NS}Relationship")}
        parts: dict[str, str] = {}
        for sheet in workbook.iter(f"{MAIN_NS}sheet"):
            target = relations.get(sheet.get(f"{REL_NS}id"))
            if target:
                parts[sheet.get("name", "")] = _resolve("xl", target)
        return parts

    def related(self, part: str, kind: str) -> list[str]:
        """某个部件通过 _rels 关联的、类型以 kind 结尾的部件（如 'table'、'drawing'）。"""
        directory, _, name = part.rpartition("/")
        rels = f"{directory}/_rels/{name}.rels"
        if not self.exists(rels):
            return []
        found = []
        for rel in ElementTree.fromstring(self.read(rels)).iter(f"{PKG_REL_NS}Relationship"):
            if rel.get("TargetMode") == "External":
                continue
            if rel.get("Type", "").rsplit("/", 1)[-1] == kind:
                found.append(_resolve(directory, rel.get("Target", "")))
        return found

    # ---- 保存 ----
    def save(self, destination) -> None:
        with atomic_path(destination) as staging:
            with ZipFile(staging, "w") as out:
                written: set[str] = set()
                for info in self._infos:
                    if info.filename in self._deleted:
                        continue
                    data = self._replaced[info.filename] if info.filename in self._replaced else self._zip.read(info.filename)
                    out.writestr(_clone(info), data)
                    written.add(info.filename)
                for name, data in self._replaced.items():
                    if name not in written and name not in self._known:
                        out.writestr(ZipInfo(name, (1980, 1, 1, 0, 0, 0)), data, compress_type=ZIP_DEFLATED)


def _clone(item: ZipInfo) -> ZipInfo:
    copy = ZipInfo(item.filename, item.date_time)
    copy.compress_type = item.compress_type if item.compress_type else ZIP_DEFLATED
    copy.external_attr = item.external_attr
    copy.comment = item.comment
    return copy


def _resolve(directory: str, target: str) -> str:
    """关系目标 → 包内绝对路径（以 / 开头表示包根）。"""
    if target.startswith("/"):
        return target[1:]
    return posixpath.normpath(str(PurePosixPath(directory) / target))
