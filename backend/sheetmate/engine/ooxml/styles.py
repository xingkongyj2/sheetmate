"""只读地解读 styles.xml 里的数字格式，用来判断一个单元格样式是"百分比 / 日期时间 / 其他"。

写入新单元格时需要知道"借来的样式"会不会把数字显示成日期或百分比；这里不修改样式表。
"""

from __future__ import annotations

import re
from xml.etree import ElementTree

from sheetmate.engine.ooxml.package import MAIN_NS, OoxmlPackage

_PERCENT_IDS = {9, 10}
_DATE_IDS = set(range(14, 23)) | set(range(27, 37)) | set(range(45, 48)) | set(range(50, 59))
_QUOTED = re.compile(r'"[^"]*"|\[[^\]]*\]|\\.')


class NumberFormats:
    def __init__(self, xf_formats: list[int], codes: dict[int, str]):
        self._xf, self._codes = xf_formats, codes

    @classmethod
    def from_package(cls, package: OoxmlPackage) -> "NumberFormats":
        if not package.exists("xl/styles.xml"):
            return cls([], {})
        root = ElementTree.fromstring(package.read("xl/styles.xml"))
        codes = {int(item.get("numFmtId", "-1")): item.get("formatCode", "") for item in root.iter(f"{MAIN_NS}numFmt")}
        xfs = root.find(f"{MAIN_NS}cellXfs")
        formats = [int(xf.get("numFmtId", "0")) for xf in xfs] if xfs is not None else []
        return cls(formats, codes)

    def kind(self, style_id: str | None) -> str:
        """'general' / 'percent' / 'date' / 'other'。无样式或无法识别按 general 处理。"""
        if not style_id or not style_id.isdigit() or int(style_id) >= len(self._xf):
            return "general"
        number = self._xf[int(style_id)]
        if number == 0:
            return "general"
        if number in _PERCENT_IDS:
            return "percent"
        if number in _DATE_IDS:
            return "date"
        code = _QUOTED.sub("", self._codes.get(number, ""))
        if "%" in code:
            return "percent"
        if re.search(r"[ymdhs]", code, re.I) and not re.search(r"[0#?]", code):
            return "date"
        return "other"
