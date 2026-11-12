"""工作表之外、会引用单元格位置的部件：绘图 / 批注 / 控件、表格对象、定义名称、图表、透视表、计算链。

原则一致：能证明同步正确才改；碰到不认识或无法同步的位置信息就抛 OoxmlUnsupported。
"""

from __future__ import annotations

import re

from sheetmate.engine.ooxml.errors import OoxmlUnsupported
from sheetmate.engine.ooxml.package import CONTENT_TYPES, WORKBOOK, WORKBOOK_RELS, OoxmlPackage
from sheetmate.engine.ooxml.ranges import _area_rows
from sheetmate.engine.ooxml.xmlutil import escape, unescape
from sheetmate.engine.workbooks.row_references import adjust_formula, shift_range_ref

_DRAWING_ROW = re.compile(r"<(?:\w+:)?row>(\d+)</(?:\w+:)?row>")  # 0 起
_CALC_PR = re.compile(r"<calcPr\b([^>]*?)(/?)>")
_AFTER_CALC = ("oleSize", "customWorkbookViews", "pivotCaches", "smartTagPr", "smartTagTypes", "webPublishing",
               "fileRecoveryPr", "webPublishObjects", "extLst")


# --------------------------------------------------------------------------- 位置保护


def guard_objects(package: OoxmlPackage, sheet_part: str, first_changed: int) -> None:
    """第 first_changed 行起会发生变化。落在该位置及其下方的绘图 / 批注 / 控件无法同步，拒绝。"""
    for kind in ("pivotTable", "queryTable"):
        if package.related(sheet_part, kind):
            raise OoxmlUnsupported("工作表含透视表或查询表，行结构变化后无法保证其范围正确")
    for drawing in package.related(sheet_part, "drawing"):
        rows = [int(value) + 1 for value in _DRAWING_ROW.findall(package.read_text(drawing))]
        _reject_if_reaches(rows, first_changed, "图表 / 形状 / 切片器")
    for vml in package.related(sheet_part, "vmlDrawing"):
        text = package.read_text(vml)
        rows = [int(value) + 1 for value in re.findall(r"<x:Row>(\d+)</x:Row>", text)]
        for anchor in re.findall(r"<x:Anchor>([^<]*)</x:Anchor>", text):
            numbers = [item.strip() for item in anchor.split(",")]
            if len(numbers) >= 8 and numbers[2].isdigit() and numbers[6].isdigit():
                rows.extend([int(numbers[2]) + 1, int(numbers[6]) + 1])
        _reject_if_reaches(rows, first_changed, "批注 / 表单控件")
    for comments in package.related(sheet_part, "comments"):
        rows = [int(value) for value in re.findall(r'<comment\b[^>]*?\bref="[A-Za-z]+(\d+)"', package.read_text(comments))]
        _reject_if_reaches(rows, first_changed, "批注")
    for control in package.related(sheet_part, "ctrlProp"):
        refs = re.findall(r'\b(?:fmlaLink|fmlaRange|fmlaGroup|fmlaTxbx)="([^"]*)"', package.read_text(control))
        rows = [int(value) for ref in refs for value in re.findall(r"\$?[A-Za-z]{1,3}\$?(\d+)", ref)]
        _reject_if_reaches(rows, first_changed, "控件链接的单元格")


def guard_tail_objects(tail: str, first_changed: int) -> None:
    """工作表自身尾部内嵌的锚点（控件 <controls>、OLE 对象 <oleObjects>）。"""
    rows = [int(value) + 1 for value in _DRAWING_ROW.findall(tail)]
    _reject_if_reaches(rows, first_changed, "内嵌控件 / 对象")
    for ref in re.findall(r'\b(?:fmlaLink|fmlaRange)="([^"]*)"', tail):
        _reject_if_reaches([int(v) for v in re.findall(r"\$?[A-Za-z]{1,3}\$?(\d+)", ref)], first_changed, "控件链接的单元格")


def _reject_if_reaches(rows: list[int], first_changed: int, what: str) -> None:
    if rows and max(rows) >= first_changed:
        raise OoxmlUnsupported(f"{what}位于被修改的行或其下方（到第 {max(rows)} 行），无法同步其位置")


def guard_pivot_sources(package: OoxmlPackage, sheet_name: str) -> None:
    for name in package.names():
        if name.startswith("xl/pivotCache/pivotCacheDefinition") and name.endswith(".xml"):
            for source in re.findall(r'<worksheetSource\b[^>]*?\bsheet="([^"]*)"', package.read_text(name)):
                if unescape(source).casefold() == sheet_name.casefold():
                    raise OoxmlUnsupported("有透视表以本工作表为数据源，行结构变化后需要刷新透视表缓存")


# --------------------------------------------------------------------------- 表格对象


def has_tables(package: OoxmlPackage, sheet_part: str) -> bool:
    return bool(package.related(sheet_part, "table"))


def rewrite_tables(package: OoxmlPackage, sheet_part: str, rowmap) -> None:
    for table in package.related(sheet_part, "table"):
        text = package.read_text(table)
        found = re.search(r'<table\b[^>]*?\bref="([^"]*)"', text)
        if found is None:
            raise OoxmlUnsupported("表格对象缺少范围")
        old = found.group(1)
        top = _area_rows(old.split(":")[0])
        header_rows = re.search(r'<table\b[^>]*?\bheaderRowCount="(\d+)"', text)
        has_header = header_rows is None or header_rows.group(1) != "0"
        if has_header and top is not None and top[0] in rowmap:
            raise OoxmlUnsupported("表格对象的表头行被删除")
        new = shift_range_ref(old, rowmap)
        rows = _area_rows(new) if new else None
        if rows is None or rows[1] - rows[0] < 1:
            raise OoxmlUnsupported("表格对象在行变化后没有数据行")

        def shift_ref(match: re.Match[str]) -> str:
            shifted = shift_range_ref(match.group(2), rowmap)
            if not shifted:
                raise OoxmlUnsupported("表格筛选范围被完全删除")
            return f"{match.group(1)}{shifted}{match.group(3)}"

        text = re.sub(r'(<table\b[^>]*?\bref=")([^"]*)(")', shift_ref, text, count=1)
        text = re.sub(r'(<autoFilter\b[^>]*?\bref=")([^"]*)(")', shift_ref, text)
        text = re.sub(r'(<sortState\b[^>]*?\bref=")([^"]*)(")', shift_ref, text)
        package.write(table, text)


def check_tables_for_permutation(package: OoxmlPackage, sheet_part: str, first: int, last: int) -> None:
    for table in package.related(sheet_part, "table"):
        found = re.search(r'<table\b[^>]*?\bref="([^"]*)"', package.read_text(table))
        rows = _area_rows(found.group(1)) if found else None
        if rows is None or rows[1] < first or rows[0] > last:
            continue
        if not (rows[0] < first and rows[1] >= last):
            raise OoxmlUnsupported("排序区域包含表格对象的表头，或只覆盖表格的一部分")


# --------------------------------------------------------------------------- 工作簿级引用


def rewrite_defined_names(package: OoxmlPackage, maps: dict[str, list]) -> None:
    xml = package.read_text(WORKBOOK)
    if "<definedName" not in xml:
        return

    def replace(match: re.Match[str]) -> str:
        body = unescape(match.group(2))
        new = _adjust_for_all(body, "", maps)
        return match.group(0) if new == body else f"<definedName{match.group(1)}>{escape(new)}</definedName>"

    updated = re.sub(r"<definedName\b([^>]*)>(.*?)</definedName>", replace, xml, flags=re.S)
    if updated != xml:
        package.write(WORKBOOK, updated)


def rewrite_chart_references(package: OoxmlPackage, maps: dict[str, list]) -> None:
    for name in package.names():
        if not (name.startswith("xl/charts/") and name.endswith(".xml") and "/_rels/" not in name):
            continue
        xml = package.read_text(name)
        if not any(sheet.casefold() in xml.casefold() for sheet in maps):
            continue

        def replace(match: re.Match[str]) -> str:
            body = unescape(match.group(2))
            new = _adjust_for_all(body, "", maps)
            return match.group(0) if new == body else f"<{match.group(1)}f>{escape(new)}</{match.group(1)}f>"

        updated = re.sub(r"<((?:\w+:)?)f>(.*?)</\1f>", replace, xml, flags=re.S)
        if updated != xml:
            package.write(name, updated)


def adjust_text(text: str, current_sheet: str, maps: dict[str, list]) -> str:
    """一段公式 / 引用文本在各工作表的行变化之后的新文本（先用名字粗筛，避免无谓解析）。"""
    return _adjust_for_all(text, current_sheet, maps)


def _adjust_for_all(text: str, current_sheet: str, maps: dict[str, list]) -> str:
    folded = text.casefold()
    for target, rowmaps in maps.items():
        mentions = target.casefold() in folded
        if not mentions and target != current_sheet:
            continue
        for rowmap in rowmaps:
            try:
                text = adjust_formula(text, current_sheet, {target: rowmap})
            except ValueError as exc:
                raise OoxmlUnsupported(f"公式 {text[:60]!r} 无法随行变化同步：{exc}") from exc
    return text


# --------------------------------------------------------------------------- 计算链与重算


def drop_calc_chain(package: OoxmlPackage) -> bool:
    """行号变化后计算链里的单元格坐标已失效，Excel 会提示修复。删除它，Excel 打开时会自动重建。"""
    if not package.exists("xl/calcChain.xml"):
        return False
    package.delete("xl/calcChain.xml")
    types = package.read_text(CONTENT_TYPES)
    package.write(CONTENT_TYPES, re.sub(r'<Override\b[^>]*PartName="/xl/calcChain\.xml"[^>]*/>', "", types))
    rels = package.read_text(WORKBOOK_RELS)
    package.write(WORKBOOK_RELS, re.sub(r'<Relationship\b[^>]*Target="(?:/xl/)?calcChain\.xml"[^>]*/>', "", rels))
    return True


def workbook_has_formulas(package: OoxmlPackage, sheet_parts: list[str]) -> bool:
    return any(b"<f>" in (data := package.read(part)) or b"<f " in data for part in sheet_parts)


def force_full_calc(package: OoxmlPackage) -> bool:
    """要求 Excel 打开时重算：值或行结构变了，公式的缓存值已经过期。"""
    xml = package.read_text(WORKBOOK)
    existing = _CALC_PR.search(xml)
    if existing:
        if "fullCalcOnLoad" in existing.group(1):
            return False
        updated = xml.replace(existing.group(0), f'<calcPr{existing.group(1)} fullCalcOnLoad="1"{existing.group(2)}>', 1)
    else:
        tag = '<calcPr fullCalcOnLoad="1"/>'
        anchor = re.search(r"<(?:\w+:)?(?:%s)\b" % "|".join(_AFTER_CALC), xml)
        if anchor:
            updated = xml[:anchor.start()] + tag + xml[anchor.start():]
        else:
            end = xml.rfind("</workbook>")
            if end < 0:
                raise OoxmlUnsupported("workbook.xml 结构异常")
            updated = xml[:end] + tag + xml[end:]
    package.write(WORKBOOK, updated)
    return True
