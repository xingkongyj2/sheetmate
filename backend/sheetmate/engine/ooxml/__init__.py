"""OOXML 结构编辑：直接修改 .xlsx / .xlsm 内部 XML，未改动的部件按字节原样保留。

适用于原件含 openpyxl 载入再保存会丢失的对象（切片器、表单控件、绘图形状、嵌入对象、自定义 XML 等），
而编辑属于改值、删行、插行、排序、右侧追加列这类能证明引用同步正确的操作。
无法证明安全时抛 OoxmlUnsupported，调用方应退回其他写法。

本包只依赖标准库和 openpyxl 的纯工具（公式分词 / 平移、坐标换算），不依赖 pandas、DuckDB、Recipe 或 Agent。
"""

from sheetmate.engine.ooxml.editor import EditReport, OoxmlEditor, SheetEditor
from sheetmate.engine.ooxml.errors import OoxmlUnsupported

__all__ = ["EditReport", "OoxmlEditor", "OoxmlUnsupported", "SheetEditor"]
