"""对原始 XML 文本做定点编辑所需的小工具（不解析成树，以便未改动的部分保持字节不变）。"""

from __future__ import annotations

import re

from sheetmate.engine.ooxml.errors import OoxmlUnsupported

_ATTR = re.compile(r'([\w:.-]+)\s*=\s*"([^"]*)"')
_ENTITIES = {"&lt;": "<", "&gt;": ">", "&amp;": "&", "&quot;": '"', "&apos;": "'"}
_ENTITY = re.compile(r"&(?:lt|gt|amp|quot|apos);")
_ILLEGAL_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
MAX_CELL_TEXT = 32767


def parse_attrs(text: str) -> dict[str, str]:
    """属性文本 → 有序字典（保留原顺序与原始转义）。遇到无法解析的写法（如单引号）直接拒绝。"""
    if _ATTR.sub("", text).strip():
        raise OoxmlUnsupported(f"无法解析的属性：{text.strip()[:60]!r}")
    return dict(_ATTR.findall(text))


def render_attrs(attrs: dict[str, str]) -> str:
    return "".join(f' {key}="{value}"' for key, value in attrs.items())


def unescape(text: str) -> str:
    if "&#" in text:
        raise OoxmlUnsupported("文本含数字字符引用，无法安全改写")
    return _ENTITY.sub(lambda match: _ENTITIES[match.group(0)], text)


def escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def check_text(value: str) -> str:
    """写入单元格的文本必须合法：长度不超过 Excel 上限，且不含 XML 不允许的控制字符。"""
    if len(value) > MAX_CELL_TEXT or _ILLEGAL_XML.search(value):
        raise OoxmlUnsupported("文本过长或含 XML 不允许的字符")
    return value
