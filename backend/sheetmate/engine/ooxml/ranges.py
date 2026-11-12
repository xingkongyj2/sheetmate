"""工作表尾部（<sheetData> 之后）里所有带单元格范围的元素，在行增删 / 重排后的同步改写。

覆盖：合并区域、条件格式、数据验证、自动筛选、排序状态、超链接、忽略错误、保护区域、分页符。
不认识的、与行位置有关的内容（extLst 里的 sqref / 公式）一律拒绝，而不是静默留下错位的引用。
"""

from __future__ import annotations

import re
from typing import Callable

from sheetmate.engine.ooxml.errors import OoxmlUnsupported
from sheetmate.engine.ooxml.xmlutil import escape, unescape
from sheetmate.engine.workbooks.row_references import shift_range_ref

# 条件格式 / 数据验证里的公式：相对引用是相对于范围左上角（锚点）存放的。
# anchored(text, 旧锚点行, 新锚点行) → 新公式文本。
AnchoredFormula = Callable[[str, int, int], str]
_CELL = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d+)$")
_ROWS = re.compile(r"^\$?(\d+):\$?(\d+)$")


# --------------------------------------------------------------------------- 删除 / 插入


def rewrite_tail(tail: str, rowmap, anchored: AnchoredFormula) -> str:
    """rowmap 是 row_references 的 DeletedRows / InsertedRows；anchored 改写条件格式 / 验证里的一条公式。"""
    if "<extLst" in tail:
        extension = tail[tail.index("<extLst"):]
        if "sqref" in extension or "<xm:f" in extension:
            raise OoxmlUnsupported("工作表扩展区含与位置相关的条件格式 / 验证，无法安全同步")
    tail = _merge_cells(tail, rowmap)
    tail = _conditional_formatting(tail, rowmap, anchored)
    tail = _data_validations(tail, rowmap, anchored)
    tail = _ref_attribute(tail, "autoFilter", "ref", rowmap, required=True)
    tail = _ref_attribute(tail, "sortState", "ref", rowmap, required=True)
    tail = _self_closing(tail, "hyperlink", "ref", rowmap, container="hyperlinks")
    tail = _self_closing(tail, "ignoredError", "sqref", rowmap, container="ignoredErrors")
    tail = _self_closing(tail, "protectedRange", "sqref", rowmap, container="protectedRanges")
    return _row_breaks(tail, rowmap)


def _shift(reference: str, rowmap) -> str:
    return shift_range_ref(unescape(reference), rowmap)


def _merge_cells(tail: str, rowmap) -> str:
    def container(match: re.Match[str]) -> str:
        kept = []
        deleted = getattr(rowmap, "deleted", None)
        for item in re.finditer(r'<mergeCell\b[^>]*?\bref="([^"]+)"[^>]*/>', match.group(2)):
            rows = _area_rows(item.group(1))
            if deleted and rows is not None and rows[0] < rows[1]:
                removed = sum(rows[0] <= row <= rows[1] for row in deleted)
                if 0 < removed < rows[1] - rows[0] + 1:
                    # 只删掉纵向合并区域的一部分：锚点值可能随被删行消失，结果与计划不一致，交给常规流程处理。
                    raise OoxmlUnsupported(f"删除了纵向合并单元格 {item.group(1)} 的一部分行")
            shifted = _shift(item.group(1), rowmap)
            if not shifted:
                continue
            first, _, last = shifted.partition(":")
            if not last or first == last:
                continue  # 缩成单个单元格的合并没有意义，Excel 也不接受
            kept.append(f'<mergeCell ref="{shifted}"/>')
        return f'<mergeCells count="{len(kept)}">{"".join(kept)}</mergeCells>' if kept else ""

    return re.sub(r"<mergeCells\b([^>]*)>(.*?)</mergeCells>", container, tail, flags=re.S)


def _anchor_rows(sqref: str, rowmap) -> tuple[int, int]:
    """范围的左上角（取第一个区域）在变化前后的行号。第一个区域被完全删除时锚点不再明确，拒绝。"""
    first = unescape(sqref).split()[0]
    old = _area_rows(first)
    if old is None:  # 整列引用：与行位置无关
        return 1, 1
    shifted = shift_range_ref(first, rowmap)
    if not shifted:
        raise OoxmlUnsupported("条件格式 / 验证的第一个范围被完全删除，公式锚点无法确定")
    return old[0], _area_rows(shifted.split()[0])[0]


def _conditional_formatting(tail: str, rowmap, anchored: AnchoredFormula) -> str:
    def block(match: re.Match[str]) -> str:
        sqref = re.search(r'\bsqref="([^"]*)"', match.group(1))
        if sqref is None:
            return match.group(0)
        shifted = _shift(sqref.group(1), rowmap)
        if not shifted:
            return ""
        old_top, new_top = _anchor_rows(sqref.group(1), rowmap)
        inner = _formulas(match.group(2), ("formula",), lambda text: anchored(text, old_top, new_top))
        attrs = match.group(1).replace(f'sqref="{sqref.group(1)}"', f'sqref="{shifted}"', 1)
        return f"<conditionalFormatting{attrs}>{inner}</conditionalFormatting>"

    return re.sub(r"<conditionalFormatting\b([^>]*)>(.*?)</conditionalFormatting>", block, tail, flags=re.S)


def _data_validations(tail: str, rowmap, anchored: AnchoredFormula) -> str:
    def item(match: re.Match[str]) -> str:
        sqref = re.search(r'\bsqref="([^"]*)"', match.group(1))
        if sqref is None:
            return match.group(0)
        shifted = _shift(sqref.group(1), rowmap)
        if not shifted:
            return ""
        old_top, new_top = _anchor_rows(sqref.group(1), rowmap)
        inner = _formulas(match.group(2) or "", ("formula1", "formula2"), lambda text: anchored(text, old_top, new_top))
        attrs = match.group(1).replace(f'sqref="{sqref.group(1)}"', f'sqref="{shifted}"', 1)
        return f"<dataValidation{attrs}>{inner}</dataValidation>"

    def container(match: re.Match[str]) -> str:
        body = re.sub(r"<dataValidation\b([^>]*?)(?:/>|>(.*?)</dataValidation>)", item, match.group(2), flags=re.S)
        count = len(re.findall(r"<dataValidation\b", body))
        if not count:
            return ""
        attrs = re.sub(r'\bcount="\d+"', f'count="{count}"', match.group(1))
        return f"<dataValidations{attrs}>{body}</dataValidations>"

    return re.sub(r"<dataValidations\b([^>]*)>(.*?)</dataValidations>", container, tail, flags=re.S)


def _formulas(xml: str, tags: tuple[str, ...], adjust: Callable[[str], str]) -> str:
    pattern = r"<(%s)>(.*?)</\1>" % "|".join(tags)
    return re.sub(pattern, lambda m: f"<{m.group(1)}>{escape(adjust(unescape(m.group(2))))}</{m.group(1)}>", xml, flags=re.S)


def _ref_attribute(tail: str, tag: str, attr: str, rowmap, *, required: bool) -> str:
    def replace(match: re.Match[str]) -> str:
        shifted = _shift(match.group(2), rowmap)
        if not shifted:
            if required:
                raise OoxmlUnsupported(f"<{tag}> 的范围被完全删除")
            return match.group(0)
        return f"{match.group(1)}{shifted}{match.group(3)}"

    return re.sub(r'(<%s\b[^>]*?\b%s=")([^"]*)(")' % (tag, attr), replace, tail)


def _self_closing(tail: str, tag: str, attr: str, rowmap, *, container: str) -> str:
    def replace(match: re.Match[str]) -> str:
        found = re.search(r'\b%s="([^"]*)"' % attr, match.group(0))
        if found is None:
            return match.group(0)
        shifted = _shift(found.group(1), rowmap)
        if not shifted:
            return ""
        return match.group(0).replace(f'{attr}="{found.group(1)}"', f'{attr}="{shifted}"', 1)

    tail = re.sub(r"<%s\b[^>]*?/>" % tag, replace, tail)
    return re.sub(r"<%s\b[^>]*>\s*</%s>" % (container, container), "", tail)


def _row_breaks(tail: str, rowmap) -> str:
    def container(match: re.Match[str]) -> str:
        kept = []
        for item in re.finditer(r"<brk\b([^>]*?)/>", match.group(2)):
            found = re.search(r'\bid="(\d+)"', item.group(1))
            if found is None:
                raise OoxmlUnsupported("分页符缺少位置")
            row = int(found.group(1))
            if row in rowmap:
                continue
            kept.append(item.group(0).replace(f'id="{row}"', f'id="{rowmap.map_row(row)}"', 1))
        if not kept:
            return ""
        attrs = re.sub(r'\b(count|manualBreakCount)="\d+"', lambda m: f'{m.group(1)}="{len(kept)}"', match.group(1))
        return f"<rowBreaks{attrs}>{''.join(kept)}</rowBreaks>"

    return re.sub(r"<rowBreaks\b([^>]*)>(.*?)</rowBreaks>", container, tail, flags=re.S)


# --------------------------------------------------------------------------- 重排（排序）


def rewrite_tail_for_permutation(tail: str, moved: dict[int, int], first: int, last: int) -> str:
    """块 [first, last] 内的行按 moved（旧行号 → 新行号）重排后，尾部元素的同步。

    只接受能证明正确的情形：单行的合并 / 超链接随行移动；整块被一个范围完整覆盖（如整列的条件格式）保持不变。
    任何只覆盖块的一部分的范围、分页符、扩展区都拒绝。
    """
    if "<extLst" in tail:
        extension = tail[tail.index("<extLst"):]
        if "sqref" in extension or "<xm:f" in extension:
            raise OoxmlUnsupported("工作表扩展区含与位置相关的条件格式 / 验证，无法在排序时同步")

    def check_covering(reference: str, what: str) -> None:
        for area in unescape(reference).split():
            rows = _area_rows(area)
            if rows is None or rows[1] < first or rows[0] > last:
                continue
            if not (rows[0] <= first and rows[1] >= last):
                raise OoxmlUnsupported(f"{what}只覆盖被排序区域的一部分，无法保证排序后位置正确")

    def merge(match: re.Match[str]) -> str:
        items = []
        for item in re.finditer(r'<mergeCell\b[^>]*?\bref="([^"]+)"[^>]*/>', match.group(2)):
            rows = _area_rows(item.group(1))
            if rows is None or rows[1] < first or rows[0] > last or (rows[0] <= first and rows[1] >= last):
                items.append(f'<mergeCell ref="{item.group(1)}"/>')
            elif rows[0] == rows[1]:
                items.append(f'<mergeCell ref="{_remap_row_in_ref(item.group(1), moved)}"/>')
            else:
                raise OoxmlUnsupported("排序区域内含跨行的合并单元格")
        return f'<mergeCells count="{len(items)}">{"".join(items)}</mergeCells>'

    tail = re.sub(r"<mergeCells\b([^>]*)>(.*?)</mergeCells>", merge, tail, flags=re.S)
    for pattern, what in ((r'<conditionalFormatting\b[^>]*?\bsqref="([^"]*)"', "条件格式"),
                          (r'<dataValidation\b[^>]*?\bsqref="([^"]*)"', "数据验证"),
                          (r'<autoFilter\b[^>]*?\bref="([^"]*)"', "自动筛选"),
                          (r'<sortState\b[^>]*?\bref="([^"]*)"', "排序状态"),
                          (r'<ignoredError\b[^>]*?\bsqref="([^"]*)"', "忽略错误"),
                          (r'<protectedRange\b[^>]*?\bsqref="([^"]*)"', "保护区域")):
        for found in re.finditer(pattern, tail):
            check_covering(found.group(1), what)
    for found in re.finditer(r'<brk\b[^>]*?\bid="(\d+)"', tail):
        if first <= int(found.group(1)) <= last:
            raise OoxmlUnsupported("排序区域内含分页符")

    def hyperlink(match: re.Match[str]) -> str:
        found = re.search(r'\bref="([^"]*)"', match.group(0))
        rows = _area_rows(found.group(1)) if found else None
        if rows is None or rows[1] < first or rows[0] > last:
            return match.group(0)
        if rows[0] != rows[1]:
            raise OoxmlUnsupported("排序区域内含跨行的超链接范围")
        return match.group(0).replace(f'ref="{found.group(1)}"', f'ref="{_remap_row_in_ref(found.group(1), moved)}"', 1)

    return re.sub(r"<hyperlink\b[^>]*?/>", hyperlink, tail)


def _area_rows(area: str) -> tuple[int, int] | None:
    """范围 → (起始行, 结束行)；整列引用（A:A）返回 None（与行无关）。"""
    parts = area.split(":")
    rows = []
    for part in parts:
        found = _CELL.match(part)
        if found:
            rows.append(int(found.group(2)))
            continue
        if re.fullmatch(r"\$?\d+", part):
            rows.append(int(part.lstrip("$")))
            continue
        if re.fullmatch(r"\$?[A-Za-z]{1,3}", part):
            return None
        raise OoxmlUnsupported(f"无法识别的范围 {area!r}")
    return (min(rows), max(rows))


def _remap_row_in_ref(ref: str, moved: dict[int, int]) -> str:
    parts = []
    for part in ref.split(":"):
        found = re.fullmatch(r"(\$?[A-Za-z]{1,3}\$?)(\d+)", part)
        if not found:
            raise OoxmlUnsupported(f"无法重映射的范围 {ref!r}")
        row = int(found.group(2))
        parts.append(f"{found.group(1)}{moved.get(row, row)}")
    return ":".join(parts)
