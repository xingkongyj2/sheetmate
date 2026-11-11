"""后台建表技能：把 AI 的布局声明交给新建引擎，不执行生成的代码。"""

from __future__ import annotations

import datetime as dt
import json
import re
from copy import deepcopy
from typing import Any

from openpyxl.utils.cell import coordinate_to_tuple

from sheetmate.engine.workbooks.builder import (
    MAX_COLUMNS,
    MAX_GRID_CELLS,
    MAX_ROWS,
    MAX_SHEETS,
    WorkbookSpec,
    workbook_tool_schema,
)
from sheetmate.engine.tables.workspace import Workspace
from sheetmate.engine.workbooks.writer import _excel_value


def _cell_position(value: Any) -> tuple[int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z]{1,3}[1-9][0-9]*", value):
        raise ValueError("需要单个单元格坐标，如 A3")
    row, column = coordinate_to_tuple(value.upper())
    if row > MAX_ROWS or column > MAX_COLUMNS:
        raise ValueError(f"单元格坐标超出上限（{MAX_ROWS} 行、{MAX_COLUMNS} 列）")
    return row, column


def _export_cell(value: Any) -> Any:
    value = _excel_value(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return {"date": value.isoformat()}
    return value


def creation_parameters() -> dict[str, Any]:
    schema = workbook_tool_schema()
    schema["properties"]["filename"] = {"type": "string", "description": "结果文件名，可含 .xlsx；更新同一结果时沿用原名称，独立交付才使用新名称。"}
    schema["properties"]["base_file_id"] = {"type": "string", "description": "可选：基于本技能已生成文件的布局快照继续调整，保留未指定的内容与工作表；是否替换原结果由本轮用户意图决定。先 inspect_workbook_creation 查看布局。"}
    sheet_schema = schema["properties"]["sheets"]["items"]
    cell_schema = deepcopy(sheet_schema["properties"]["rows"]["items"]["items"])
    sheet_schema["properties"].update({
        "table": {"type": "string", "description": "可选：直接填入当前工作区表的真实数据，无需重新抄写。"},
        "table_start_cell": {"type": "string", "description": "table 数据块左上角，如 A3；默认 A1，不得覆盖 rows 中的已有内容。"},
        "table_headers": {"type": "boolean", "description": "table 是否包含列名行，默认 true。"},
        "cells": {"type": "object", "additionalProperties": cell_schema,
                  "description": "可选：按坐标填入或更新单元格，例如 {\"A1\":\"新标题\"}；null 清空。基于旧文件更新时可避免重抄网格。"},
    })
    sheet_schema["properties"]["styles"]["description"] += " 基于旧文件时追加到已有样式后应用；其他提供的字段替换同名字段，未提供的保留。"
    return schema


def _inherit_layout(base: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    # 保留非法顶层参数，交给最终模型校验，避免悄悄忽略模型写错的字段。
    result.update({key: value for key, value in changes.items() if key != "sheets"})
    patches = changes.get("sheets")
    if not isinstance(patches, list):
        raise ValueError("基于已有建表文件更新时，sheets 应包含按 name 指定的工作表修改")
    by_name = {sheet["name"].casefold(): sheet for sheet in result["sheets"]}
    seen: set[str] = set()
    for patch in patches:
        if not isinstance(patch, dict) or not isinstance(patch.get("name"), str):
            raise ValueError("每个工作表修改都必须指定 name")
        key = patch["name"].casefold()
        if key in seen:
            raise ValueError("同一工作表不能重复指定修改")
        seen.add(key)
        target = by_name.get(key)
        if target is None:
            result["sheets"].append(deepcopy(patch))
        else:
            inherited_styles = target.get("styles", [])
            target.update(deepcopy(patch))
            if isinstance(patch.get("styles"), list):
                target["styles"] = inherited_styles + deepcopy(patch["styles"])
    return result


def prepare_workbook_spec(ws: Workspace, raw: dict[str, Any], *, base: dict[str, Any] | None = None) -> WorkbookSpec:
    """可选地填入工作区真实数据，再使用与引擎相同的布局校验。"""
    data = _inherit_layout(base, raw) if base is not None else deepcopy(raw)
    sheets = data.get("sheets")
    if not isinstance(sheets, list):
        return WorkbookSpec.model_validate(data)
    if len(sheets) > MAX_SHEETS:
        raise ValueError(f"新建工作簿最多 {MAX_SHEETS} 个工作表")
    grid_cells = 0
    for sheet in sheets:
        if not isinstance(sheet, dict):
            continue  # 类型错误统一交给声明模型报告。
        table = sheet.pop("table", None)
        start = sheet.pop("table_start_cell", "A1")
        headers = sheet.pop("table_headers", True)
        cells = sheet.pop("cells", {})
        if not isinstance(cells, dict):
            raise ValueError("cells 必须是坐标到单元格值的对象，如 {\"A1\":\"标题\"}")
        if len(cells) > MAX_GRID_CELLS:
            raise ValueError("cells 超出工作簿单元格上限")
        existing_rows = sheet.get("rows", [])
        if cells:
            if not isinstance(existing_rows, list) or any(not isinstance(row, list) for row in existing_rows):
                raise ValueError("rows 必须是二维数组")
            cell_count = sum(len(row) for row in existing_rows)
        for coordinate, value in cells.items():
            top, left = _cell_position(coordinate)
            cell_count += max(0, left - (len(existing_rows[top - 1]) if top <= len(existing_rows) else 0))
            if cell_count > MAX_GRID_CELLS:
                raise ValueError(f"新建工作簿数据网格不能超过 {MAX_GRID_CELLS} 个单元格")
            while len(existing_rows) < top:
                existing_rows.append([])
            row = existing_rows[top - 1]
            row.extend([None] * max(0, left - len(row)))
            row[left - 1] = value
        if cells:
            sheet["rows"] = existing_rows
        if table is None:
            continue
        if not isinstance(table, str) or not table.strip():
            raise ValueError("table 必须是当前工作区的表名")
        if not isinstance(headers, bool):
            raise ValueError("table_headers 必须是 true 或 false")
        try:
            top, left = _cell_position(start)
        except ValueError as exc:
            raise ValueError(f"table_start_cell {exc}") from exc
        info = ws.require(table)
        row_count, column_count = info.rows + int(headers), len(info.columns)
        if top < 1 or left < 1 or top + row_count - 1 > MAX_ROWS or left + column_count - 1 > MAX_COLUMNS:
            raise ValueError(f"填入「{table}」后超出新建工作表上限（{MAX_ROWS} 行、{MAX_COLUMNS} 列）")
        # 先限制目标网格大小，再读取并展开数据，避免不受控的空白填充。
        existing = sheet.get("rows", [])
        if not isinstance(existing, list) or any(not isinstance(row, list) for row in existing):
            raise ValueError("rows 必须是二维数组")
        expanded = sum(len(row) for row in existing)
        width = left + column_count - 1
        for index in range(top - 1, top - 1 + row_count):
            expanded += max(0, width - (len(existing[index]) if index < len(existing) else 0))
        grid_cells += expanded
        if grid_cells > MAX_GRID_CELLS:
            raise ValueError(f"新建工作簿数据网格不能超过 {MAX_GRID_CELLS} 个单元格")
        df = ws.df(table)
        rows = ([info.column_names] if headers else []) + [[_export_cell(value) for value in row]
                                                         for row in df.itertuples(index=False, name=None)]
        for row_offset, row in enumerate(rows, start=top - 1):
            while len(existing) <= row_offset:
                existing.append([])
            target = existing[row_offset]
            target.extend([None] * max(0, left - 1 + len(row) - len(target)))
            for column_offset, value in enumerate(row, start=left - 1):
                if target[column_offset] is not None and target[column_offset] != "":
                    raise ValueError(f"填入「{table}」会覆盖 rows 中已有的内容，请调整 table_start_cell")
                target[column_offset] = value
        sheet["rows"] = existing
    return WorkbookSpec.model_validate(data)


def workbook_creation_tool():
    # 延迟导入避免工具注册表和独立技能之间形成循环依赖。
    from sheetmate.agent.tools.contracts import Tool, ToolResult
    from sheetmate.agent.tools.presentation import public_detail_section, public_detail_text, table_payload

    def cell_text(value):
        if value is None:
            return "空白"
        if value == "":
            return "空文本"
        if isinstance(value, dict):
            value = value.get("formula", value.get("date"))
        return public_detail_text(value, 180) or "未记录可展示的值"

    def run(args, ctx):
        spec = {key: value for key, value in args.items() if key != "filename"}
        base = ctx.host.created_workbook_spec(str(spec["base_file_id"])) if spec.get("base_file_id") else None
        info = ctx.host.create_workbook(spec, str(args.get("filename") or "新建表格"))
        sheets = info.get("sheets_written", [])
        merges = sum(len(sheet.get("merges", [])) for sheet in sheets)
        warning = "；".join(info.get("warnings", []))
        text = f"已新建文件「{info['filename']}」，包含 {len(sheets)} 个工作表、{merges} 处合并区域，用户可以下载。"
        if any(sheet.get("formula_cells") for sheet in sheets):
            text += "公式已写入，需在 Excel 等支持公式计算的软件中打开后重算；服务端预览不代表计算结果。"
        if warning:
            text += f"提示：{warning}"
        details = [public_detail_section("生成文件", [info["filename"]]),
                   public_detail_section("写入工作表", [f"「{sheet['sheet']}」：{sheet['rows']} 行 × {sheet['columns']} 列" for sheet in sheets])]
        if base:
            details.insert(0, public_detail_section("调整来源", [base["file"]["filename"]]))
        previous = {sheet["name"].casefold(): sheet for sheet in base["spec"]["sheets"]} if base else {}
        changes, sources = [], []
        for sheet in spec.get("sheets", []):
            name = sheet["name"]
            old = previous.get(name.casefold(), {})
            old_rows = old.get("rows", [])
            for coordinate, value in sheet.get("cells", {}).items():
                row, column = _cell_position(coordinate)
                before = old_rows[row - 1][column - 1] if row <= len(old_rows) and column <= len(old_rows[row - 1]) else None
                if before != value:
                    changes.append(f"「{name}」!{coordinate.upper()}：{cell_text(before)} → {cell_text(value)}")
            for column, width in sheet.get("column_widths", {}).items():
                before = old.get("column_widths", {}).get(column)
                if before != width:
                    changes.append(f"「{name}」的 {column} 列宽：{before if before is not None else '自动列宽'} → {width}")
            if sheet.get("table"):
                table = ctx.ws.require(sheet["table"])
                sources.append(f"「{table.name}」{table.rows} 行 → 「{name}」!{sheet.get('table_start_cell') or 'A1'}，{'包含' if sheet.get('table_headers', True) else '不包含'}表头")
            if "merges" in sheet and old.get("merges", []) != sheet["merges"]:
                changes.append(f"「{name}」合并区域：{'、'.join(old.get('merges', [])) or '无'} → {'、'.join(sheet['merges']) or '无'}")
        if changes:
            details.append(public_detail_section("具体变化", changes[:50] + ([f"另有 {len(changes) - 50} 项变化，完整内容见生成文件。"] if len(changes) > 50 else [])))
        if sources:
            details.append(public_detail_section("数据来源与写入位置", sources))
        if warning:
            details.append(public_detail_section("核对提示", info["warnings"]))
        return ToolResult(text, summary=f"新建「{info['filename']}」",
                          tables=[table_payload(ctx.ws, name) for name in info.get("table_names", [])],
                          events=[{"type": "file", **{key: value for key, value in info.items() if key != "table_names"}}],
                          public_details=[section for section in details if section["items"]])

    return Tool("create_workbook",
                "根据用户文字、口述转写或已读取资料从零创建带格式的 Excel，无需上传模板。"
                "rows 是含标题/表头的完整网格；支持多 sheet、合并、边框、底色、数字格式、行高列宽、冻结、筛选。"
                "公式使用明确对象 {formula:'=SUM(C3:C8)'}，普通字符串始终是文本。可用 table 引用工作区真实数据。"
                "使用 base_file_id 复制本技能已创建的布局，sheets 按 name 更新；cells 可局部更新标题等内容，styles 叠加，其他未指定项保留。"
                "未知的真实数据留空；仅在用户要求示例/模拟时编写并注明模拟数据。成功返回可下载文件及可继续处理的表。",
                creation_parameters(), frozenset({"analyze", "modify", "create"}), run=run,
                label=lambda args: f"新建 Excel · {args.get('filename') or '新建表格'}")


def workbook_inspection_tool():
    from sheetmate.agent.tools.contracts import Tool, ToolResult
    from sheetmate.agent.tools.presentation import public_detail_section

    def run(args, ctx):
        saved = ctx.host.created_workbook_spec(str(args.get("file_id") or ""))
        specs = saved["spec"]["sheets"]
        requested = args.get("sheet_name")
        chosen = next((sheet for sheet in specs if sheet["name"] == requested), None) if requested else specs[0]
        if chosen is None:
            raise ValueError("找不到指定的工作表，可用：" + "、".join(sheet["name"] for sheet in specs))
        layout = {key: value for key, value in chosen.items() if key != "rows"}
        layout["styles"] = layout.get("styles", [])[:40]
        layout["merges"] = layout.get("merges", [])[:100]
        layout["preview"] = [[value[:100] if isinstance(value, str) else value for value in row[:14]]
                             for row in chosen.get("rows", [])[:12]]
        layout["preview_note"] = "预览最多前12行、14列；未显示的数据、公式和格式由 base_file_id 完整继承，无需重抄。"
        response = {"file_id": saved["file"]["file_id"], "filename": saved["file"]["filename"],
                    "sheets": [{"name": sheet["name"], "rows": len(sheet.get("rows", [])),
                                "columns": max((len(row) for row in sheet.get("rows", [])), default=0)} for sheet in specs],
                    "layout": layout}
        return ToolResult(json.dumps(response, ensure_ascii=False), summary=f"查看「{saved['file']['filename']}」建表布局",
                          public_details=[public_detail_section("查看对象", [f"「{saved['file']['filename']}」的「{chosen['name']}」工作表"]),
                                          public_detail_section("读取内容", ["已读取前 12 行、14 列范围内的数据预览，以及已保存的合并区域、样式、列宽、行高、冻结和筛选设置。"]),
                                          public_detail_section("合并区域", chosen.get("merges", []) or ["未设置合并区域。"])])

    return Tool("inspect_workbook_creation", "查看本建表技能已生成文件的布局与数据预览，供按 base_file_id 继续调整标题、合并或样式。仅可读取当前会话或客户的建表文件。",
                {"type": "object", "properties": {"file_id": {"type": "string"}, "sheet_name": {"type": "string"}},
                 "required": ["file_id"]}, run=run, label=lambda args: "查看建表布局")
