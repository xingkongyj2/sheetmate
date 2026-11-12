"""OOXML 结构编辑的对外入口。

    with OoxmlEditor.open(src) as editor:
        sheet = editor.sheet("订单")
        sheet.delete_rows({5, 9})
        sheet.set_values({(3, 4): 110.5})
        report = editor.save(dst)

保存时统一完成：把行变化同步到其他工作表的公式、定义名称、图表引用；删除过期的计算链；
有公式时要求 Excel 打开后重算。任何一步无法证明正确就抛 OoxmlUnsupported，且不会写出目标文件。
"""

from __future__ import annotations

import re
from bisect import bisect_left
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any
from xml.etree import ElementTree

from openpyxl.formula import Tokenizer
from openpyxl.formula.tokenizer import TokenizerError
from openpyxl.formula.translate import Translator
from openpyxl.utils import get_column_letter

from sheetmate.engine.ooxml import parts
from sheetmate.engine.ooxml.errors import OoxmlUnsupported
from sheetmate.engine.ooxml.package import OoxmlPackage
from sheetmate.engine.ooxml.styles import NumberFormats
from sheetmate.engine.ooxml.ranges import rewrite_tail, rewrite_tail_for_permutation
from sheetmate.engine.ooxml.sheetxml import F_ELEMENT, Cell, Row, SheetXml, cell_xml, rewrite_f_elements
from sheetmate.engine.ooxml.xmlutil import escape, parse_attrs, render_attrs, unescape
from sheetmate.engine.workbooks.row_references import DeletedRows, InsertedRows, adjust_formula

_ROW_STYLE_ATTRS = ("s", "customFormat", "ht", "customHeight")


class _SortedDeletedRows(DeletedRows):
    """删行映射，行号换算用二分查找（原实现对每一行线性扫描被删行，大表上很慢）。"""

    def __init__(self, deleted: set[int]):
        super().__init__(set(deleted))
        self._sorted = sorted(deleted)

    def map_row(self, row: int) -> int:
        return row - bisect_left(self._sorted, row)


@dataclass
class EditReport:
    changed_parts: list[str] = field(default_factory=list)
    calc_chain_dropped: bool = False
    full_calc_on_load: bool = False


class OoxmlEditor:
    def __init__(self, package: OoxmlPackage):
        self.package = package
        self._parts = package.sheet_parts()
        self._sheets: dict[str, SheetEditor] = {}
        self._maps: dict[str, list] = {}  # 工作表名 → 依次发生的行映射（供其他工作表 / 名称 / 图表同步）
        self._values_changed = False
        self._formats: NumberFormats | None = None

    def number_formats(self) -> NumberFormats:
        if self._formats is None:
            self._formats = NumberFormats.from_package(self.package)
        return self._formats

    @classmethod
    def open(cls, path: Path) -> "OoxmlEditor":
        try:
            return cls(OoxmlPackage(path))
        except OoxmlUnsupported:
            raise
        except Exception as exc:
            raise OoxmlUnsupported(f"无法打开工作簿包：{exc}") from exc

    def __enter__(self) -> "OoxmlEditor":
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> None:
        self.package.close()

    def sheet_names(self) -> list[str]:
        return list(self._parts)

    def sheet(self, name: str) -> "SheetEditor":
        if name not in self._sheets:
            part = self._parts.get(name)
            if part is None:
                raise OoxmlUnsupported(f"找不到工作表「{name}」")
            self._sheets[name] = SheetEditor(self, name, part)
        return self._sheets[name]

    # ---- 保存 ----
    def save(self, destination: Path) -> EditReport:
        report = EditReport()
        package = self.package
        for editor in self._sheets.values():
            if editor.dirty:
                text = editor.xml.serialize()
                _check_well_formed(text, editor.name)
                SheetXml.parse(text)  # 回读一遍：行 / 单元格顺序与坐标必须仍然一致
                package.write(editor.part, text)
        if self._maps:
            self._propagate()
            parts.rewrite_defined_names(package, self._maps)
            parts.rewrite_chart_references(package, self._maps)
            report.calc_chain_dropped = parts.drop_calc_chain(package)
        if (self._maps or self._values_changed) and parts.workbook_has_formulas(package, list(self._parts.values())):
            report.full_calc_on_load = parts.force_full_calc(package)
        report.changed_parts = package.changed_parts()
        for name in report.changed_parts:
            if name.endswith(".xml") and package.exists(name):
                _check_well_formed(package.read_text(name), name)
        package.save(destination)
        return report

    def _propagate(self) -> None:
        """某个工作表的行变化 → 其他工作表里引用它的公式、条件格式 / 数据验证公式。"""
        for sheet_name, part in self._parts.items():
            maps = {target: rowmaps for target, rowmaps in self._maps.items() if target != sheet_name}
            if not maps:
                continue
            xml = self.package.read_text(part)
            folded = xml.casefold()
            if not any(target.casefold() in folded for target in maps):
                continue

            def adjust_cell_formula(text: str, attrs: dict[str, str]) -> str | None:
                new = parts.adjust_text(text, sheet_name, maps)
                if new != text and attrs.get("t") in {"shared", "array", "dataTable"}:
                    raise OoxmlUnsupported(f"「{sheet_name}」里引用了被修改行的共享 / 数组公式，无法安全同步")
                return new

            updated, _ = rewrite_f_elements(xml, adjust_cell_formula)
            updated = re.sub(
                r"<(formula|formula1|formula2)>(.*?)</\1>",
                lambda m: f"<{m.group(1)}>{escape(parts.adjust_text(unescape(m.group(2)), sheet_name, maps))}</{m.group(1)}>",
                updated, flags=re.S)
            if updated != xml:
                self.package.write(part, updated)

    def _record(self, sheet: str, rowmap: Any | None) -> None:
        if rowmap is not None:
            self._maps.setdefault(sheet, []).append(rowmap)


class SheetEditor:
    def __init__(self, editor: OoxmlEditor, name: str, part: str):
        self.editor, self.name, self.part = editor, name, part
        self.xml = SheetXml.parse(editor.package.read_text(part))
        self.dirty = False

    # ---- 查询 ----
    def row_numbers(self) -> list[int]:
        return [row.number for row in self.xml.rows]

    def max_column(self) -> int:
        return self.xml.max_column()

    def has_tables(self) -> bool:
        return parts.has_tables(self.editor.package, self.part)

    def merged_ranges(self) -> list[tuple[int, int, int, int]]:
        from openpyxl.utils.cell import coordinate_to_tuple

        found = []
        for ref in re.findall(r'<mergeCell\b[^>]*?\bref="([^"]+)"', self.xml.tail):
            start, _, end = ref.partition(":")
            (top, left), (bottom, right) = coordinate_to_tuple(start), coordinate_to_tuple(end or start)
            found.append((top, left, bottom, right))
        return found

    def is_formula(self, row: int, col: int) -> bool:
        record = self.xml.row(row)
        cell = record.cell(col) if record else None
        return bool(cell and cell.has_formula)

    # ---- 值 ----
    def style_kind(self, row: int, col: int) -> str:
        """单元格样式的数字格式类别：general / percent / date / other；单元格不存在按 general。"""
        record = self.xml.row(row)
        cell = record.cell(col) if record else None
        return self.editor.number_formats().kind(cell.attrs.get("s") if cell else None)

    def set_values(self, values: dict[tuple[int, int], Any], *, style_from: dict[int, int] | None = None) -> None:
        """写入单元格的值（数字 / 文本 / 布尔 / None）。目标行必须已存在；公式单元格与合并区内的单元格拒绝。

        style_from：{目标列: 样式来源列}。目标单元格还不存在时，沿用同一行来源列单元格的样式（用于在右侧追加新列）；
        未给出的列，新建的单元格不带样式，已存在的单元格始终保留自己的样式。
        """
        merges = self.merged_ranges()
        by_row: dict[int, dict[int, Any]] = {}
        for (row, col), value in values.items():
            by_row.setdefault(row, {})[col] = value
        for number, columns in by_row.items():
            record = self.xml.row(number)
            if record is None:
                raise OoxmlUnsupported(f"第 {number} 行在工作表 XML 中不存在")
            for col in sorted(columns):
                if any(top <= number <= bottom and left <= col <= right for top, left, bottom, right in merges):
                    raise OoxmlUnsupported(f"{get_column_letter(col)}{number} 位于合并区域内")
                reference = f"{get_column_letter(col)}{number}"
                existing = record.cell(col)
                if existing is not None:
                    if existing.has_formula:
                        raise OoxmlUnsupported(f"{reference} 是公式单元格，不能改写成数值")
                    replacement = cell_xml(reference, existing.attrs.get("s"), columns[col])
                    existing.attrs, existing.inner = replacement.attrs, replacement.inner
                    continue
                donor = record.cell((style_from or {}).get(col, 0))
                record.cells.append(cell_xml(reference, donor.attrs.get("s") if donor else None, columns[col]))
                record.cells.sort(key=lambda cell: cell.col)
            record.attrs.pop("spans", None)  # 插入单元格后 spans 提示可能过期，删除它是合法的
            record.touch()
        self.dirty = True
        self.editor._values_changed = True

    # ---- 结构：删除 ----
    def delete_rows(self, rows: set[int] | list[int]) -> None:
        deleted = {int(row) for row in rows if int(row) >= 1}
        if not deleted:
            return
        self._begin_structural(min(deleted))
        rowmap = _SortedDeletedRows(deleted)
        kept: list[Row] = []
        for row in self.xml.rows:
            if row.number in deleted:
                continue
            number = rowmap.map_row(row.number)
            if number != row.number:
                row.number = number
                row.touch()
            kept.append(row)
        self.xml.rows = kept
        self.xml.reindex()
        self._after_row_map(rowmap, min(deleted))

    # ---- 结构：插入 ----
    def insert_rows(self, at: int, count: int = 1, *, style_from: int | None = None, extend_end: bool = False) -> None:
        """在第 at 行之前插入 count 个空行；style_from（插入前的行号）给出新行沿用的行高与单元格样式。"""
        if at < 1 or count < 1:
            raise OoxmlUnsupported("插入位置与行数必须为正整数")
        self._begin_structural(at)
        template = self.xml.row(style_from) if style_from else None
        rowmap = InsertedRows(at, count, extend_end)
        for row in self.xml.rows:
            if row.number >= at:
                row.number += count
                row.touch()
        for offset in range(count):
            attrs = {key: value for key, value in (template.attrs.items() if template else []) if key in _ROW_STYLE_ATTRS}
            cells = [Cell(cell.col, {"s": cell.attrs["s"]}) for cell in (template.cells if template else []) if "s" in cell.attrs]
            self.xml.rows.append(Row(at + offset, attrs, cells))
        self.xml.rows.sort(key=lambda row: row.number)
        self.xml.reindex()
        self._after_row_map(rowmap, at)

    # ---- 结构：重排（排序） ----
    def reorder_rows(self, first: int, order: list[int]) -> None:
        """把 [first, first+len(order)-1] 这一段重排：新的第 i 行是原来的第 order[i] 行（样式、行属性随行移动）。"""
        last = first + len(order) - 1
        if first < 1 or sorted(order) != list(range(first, last + 1)):
            raise OoxmlUnsupported("重排必须是 [first, last] 内所有行的一个排列")
        moved = {old: first + index for index, old in enumerate(order) if old != first + index}
        if not moved:
            return
        self._begin_structural(first)
        parts.check_tables_for_permutation(self.editor.package, self.part, first, last)
        self.xml.tail = rewrite_tail_for_permutation(self.xml.tail, moved, first, last)
        for row in self.xml.rows:
            if row.number in moved:
                self._move_formulas(row, moved[row.number])
        for row in self.xml.rows:
            if row.number in moved:
                row.number = moved[row.number]
                row.touch()
        self.xml.rows.sort(key=lambda row: row.number)
        self.xml.reindex()
        self.dirty = True
        self.editor._values_changed = True
        # 重排不改变块之外任何行的位置，其他工作表 / 名称 / 图表里的引用保持不变，也无需记录行映射。

    # ---- 内部 ----
    def _begin_structural(self, first_changed: int) -> None:
        package = self.editor.package
        parts.guard_objects(package, self.part, first_changed)
        parts.guard_tail_objects(self.xml.tail, first_changed)
        parts.guard_pivot_sources(package, self.name)
        if self.xml.formulas_present(("array", "dataTable")):
            raise OoxmlUnsupported("工作表含数组 / 数据表公式，行结构变化后无法安全同步")
        self._unshare_formulas()

    def _after_row_map(self, rowmap, first_changed: int) -> None:
        def adjust(text: str) -> str:
            try:
                return adjust_formula(text, self.name, {self.name: rowmap})
            except ValueError as exc:
                raise OoxmlUnsupported(f"公式 {text[:60]!r} 无法随行变化同步：{exc}") from exc

        def anchored(text: str, old_top: int, new_top: int) -> str:
            return _move_anchored_formula(text, self.name, old_top, new_top, first_changed)

        self.xml.rewrite_formulas(lambda text, attrs: adjust(text))
        self.xml.tail = rewrite_tail(self.xml.tail, rowmap, anchored)
        parts.rewrite_tables(self.editor.package, self.part, rowmap)
        self.editor._record(self.name, rowmap)
        self.dirty = True
        self.editor._values_changed = True

    def _unshare_formulas(self) -> None:
        """共享公式的子单元格没有公式文本，行位置变化后它们的相对关系会错位：先展开成各自独立的公式。"""
        masters: dict[str, tuple[str, str]] = {}
        for row in self.xml.rows:
            for cell in row.cells:
                if not cell.inner or "<f" not in cell.inner:
                    continue
                for match in F_ELEMENT.finditer(cell.inner):
                    attrs = parse_attrs(match.group(1))
                    if attrs.get("t") == "shared" and match.group(2) and match.group(2).strip():
                        masters[attrs.get("si", "")] = (f"{get_column_letter(cell.col)}{row.number}", unescape(match.group(2)))
        for row in self.xml.rows:
            for cell in row.cells:
                if not cell.inner or "<f" not in cell.inner:
                    continue
                reference = f"{get_column_letter(cell.col)}{row.number}"

                def expand(match: re.Match[str]) -> str:
                    attrs = parse_attrs(match.group(1))
                    if attrs.get("t") != "shared":
                        return match.group(0)
                    origin = masters.get(attrs.get("si", ""))
                    if origin is None:
                        raise OoxmlUnsupported("共享公式缺少主公式")
                    text = origin[1] if reference == origin[0] else Translator("=" + origin[1], origin=origin[0]).translate_formula(reference)[1:]
                    rest = {key: value for key, value in attrs.items() if key not in {"t", "ref", "si"}}
                    return f"<f{render_attrs(rest)}>{escape(text)}</f>"

                expanded = F_ELEMENT.sub(expand, cell.inner)
                if expanded != cell.inner:
                    cell.inner = expanded
                    row.touch()

    def _move_formulas(self, row: Row, new_row: int) -> None:
        for cell in row.cells:
            if not cell.inner or "<f" not in cell.inner:
                continue
            origin, target = f"{get_column_letter(cell.col)}{row.number}", f"{get_column_letter(cell.col)}{new_row}"

            def move(text: str, attrs: dict[str, str]) -> str:
                _require_row_local(text, row.number)
                return Translator("=" + text, origin=origin).translate_formula(target)[1:]

            cell.inner, _ = rewrite_f_elements(cell.inner, move)
            row.touch()


def _move_anchored_formula(text: str, sheet: str, old_top: int, new_top: int, first_changed: int) -> str:
    """条件格式 / 数据验证的公式：相对引用跟着范围锚点平移；绝对行引用只有在改动区域之上才保持不变。"""
    if re.search(r"(?:'%s'|%s)!" % (re.escape(sheet.replace("'", "''")), re.escape(sheet)), text):
        raise OoxmlUnsupported("条件格式 / 验证公式显式引用了本工作表，无法按锚点同步")
    try:
        tokens = Tokenizer("=" + text).items
    except TokenizerError as exc:
        raise OoxmlUnsupported(f"无法解析公式 {text[:60]!r}：{exc}") from exc
    for token in tokens:
        if token.type == "OPERAND" and token.subtype == "RANGE" and "!" not in token.value:
            for part in token.value.split(":"):
                found = re.fullmatch(r"\$?[A-Za-z]{1,3}\$(\d+)|\$(\d+)", part)
                if found and int(found.group(1) or found.group(2)) >= first_changed:
                    raise OoxmlUnsupported(f"条件格式 / 验证公式含绝对行引用 {part}，位于被修改的行或其下方")
    if old_top == new_top:
        return text
    return Translator("=" + text, origin=f"A{old_top}").translate_formula(f"A{new_top}")[1:]


def _require_row_local(text: str, own_row: int) -> None:
    """排序时公式随记录移动，只有"只引用本行"或"绝对行引用"的公式才能保证移动后含义不变。"""
    try:
        tokens = Tokenizer("=" + text).items
    except TokenizerError as exc:
        raise OoxmlUnsupported(f"无法解析公式 {text[:60]!r}：{exc}") from exc
    for token in tokens:
        if token.type != "OPERAND" or token.subtype != "RANGE":
            continue
        for part in token.value.rpartition("!")[2].split(":"):
            cell = re.fullmatch(r"\$?[A-Za-z]{1,3}(\$?)(\d+)", part)
            row_only = re.fullmatch(r"(\$?)(\d+)", part)
            found = cell or row_only
            if found and not found.group(1) and int(found.group(2)) != own_row:
                raise OoxmlUnsupported(f"公式 {text[:60]!r} 引用了其他行，排序后含义可能改变")


def _check_well_formed(xml: str, label: str) -> None:
    try:
        ElementTree.fromstring(xml.encode("utf-8"))
    except ElementTree.ParseError as exc:
        raise OoxmlUnsupported(f"「{label}」改写后不是合法的 XML：{exc}") from exc
