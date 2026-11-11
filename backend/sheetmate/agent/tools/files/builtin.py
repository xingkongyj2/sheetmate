"""files / builtin tool capabilities."""

from __future__ import annotations

from typing import Any
from sheetmate.agent.tools.contracts import AgentContext, ToolResult
from sheetmate.agent.tools.presentation import (
    _assert_table_scope,
    _file_detail,
    _table_detail,
    public_detail_section,
    table_payload,
)


def _tool_list_files(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    files = ctx.host.list_files()
    if not files:
        return ToolResult("没有可加载的文件。", summary="没有文件", public_details=[public_detail_section("查看结果", ["当前可访问范围没有文件。"])])
    lines = [
        f"- file_id={f['file_id']}「{f['filename']}」（{f.get('scope_label', '')}，{f.get('kind_label', '')}）"
        + (f" 已加载为表：{f['tables']}" if f.get("tables") else " 未加载")
        for f in files
    ]
    return ToolResult("\n".join(lines), summary=f"{len(files)} 个文件",
                      public_text="\n".join(_file_detail(item) for item in files),
                      public_details=[public_detail_section("查看到的文件", [_file_detail(item) for item in files]),
                                      public_detail_section("查看内容", ["已查询文件名称、类型、工作表和加载状态。"])])


def _tool_show_files(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    """Publish existing, accessible files without reading or re-exporting their contents."""
    if set(args) - {"file_id", "file_ids", "all", "kind", "kinds"}:
        raise ValueError("查看文件只接受已有文件 ID 或类型，不接受路径、网址或其他参数。")
    if "all" in args and not isinstance(args["all"], bool):
        raise ValueError("all 必须为 true 或 false。")
    kind = args.get("kind")
    kinds = args.get("kinds")
    if "kind" in args and "kinds" in args:
        raise ValueError("kind 与 kinds 请选择一种；查看多种文件类型使用 kinds。")
    if kind is not None and (not isinstance(kind, str) or kind not in {"input", "expected", "output"}):
        raise ValueError("文件类型只能是 input、expected 或 output。")
    if "kinds" in args and (not isinstance(kinds, list) or not kinds or
                            any(not isinstance(value, str) or value not in {"input", "expected", "output"} for value in kinds)):
        raise ValueError("kinds 必须为非空的文件类型列表，只能包含 input、expected 或 output。")
    selected_kinds = set(kinds) if kinds is not None else ({kind} if kind is not None else None)
    single = "file_id" in args
    multiple = "file_ids" in args
    all_files = args.get("all") is True
    if sum((single, multiple, all_files)) > 1:
        raise ValueError("请选择一种查看范围：file_id、file_ids 或 all=true。kind 或 kinds 可用于筛选文件类型。")
    requested: list[str] | None = None
    if single:
        value = args["file_id"]
        if not isinstance(value, str) or not value.strip():
            raise ValueError("请提供要查看的文件 ID。")
        requested = [value.strip()]
    elif multiple:
        values = args["file_ids"]
        if not isinstance(values, list) or not values or any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("file_ids 必须为非空的文件 ID 列表。")
        requested = list(dict.fromkeys(value.strip() for value in values))
    elif not all_files and selected_kinds is None:
        raise ValueError("请提供 file_id、file_ids、all=true 或文件类型 kind / kinds。")

    # list_files is scoped by the current conversation/customer; do not resolve arbitrary IDs or paths.
    available = {item["file_id"]: item for item in ctx.host.list_files() if item.get("kind") in {"input", "expected", "output"}}
    if requested is not None:
        if any(file_id not in available or (selected_kinds is not None and available[file_id].get("kind") not in selected_kinds) for file_id in requested):
            raise ValueError("部分文件不存在、不属于当前客户或会话，或不符合指定类型，请重新查看文件列表。")
        files = [available[file_id] for file_id in requested]
    else:
        files = [item for item in available.values() if (selected_kinds is None or item.get("kind") in selected_kinds)
                 and not item.get("intermediate") and item.get("delivery_status") not in {"candidate", "intermediate"}]
    if not files:
        return ToolResult("没有找到符合范围的文件。", summary="没有可查看的文件")

    public_fields = {"file_id", "filename", "kind", "kind_label", "size", "customer_id", "conversation_id", "run_id", "created_at", "sheets", "media_type", "preview_url", "intermediate", "delivery_status"}
    events = [{"type": "file", **{key: value for key, value in item.items() if key in public_fields},
               "download_url": f"/api/files/{item['file_id']}/download", "explicit": True} for item in files]
    summary = f"可查看和下载 {len(files)} 个文件。"
    return ToolResult(
        summary + "界面已提供原文件的查看和下载卡片；只需简短说明，不要读取或转写原表、重新导出副本或编造下载链接。"
        "若本轮还明确要求分析或修改，可继续完成那些要求。",
        summary=summary, public_text=summary, events=events,
        public_details=[public_detail_section("展示文件", [_file_detail(item) for item in files]),
                        public_detail_section("提供内容", ["已提供以上文件的查看和下载入口。"])],
    )


def _tool_load_file(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    infos = ctx.host.load_file(str(args.get("file_id", "")))
    names = [f"「{t.name}」{t.rows} 行 × {len(t.columns)} 列" for t in infos]
    return ToolResult("已加载：" + "、".join(names) + "。可以用 table_info 查看结构。", summary="加载了 " + "、".join(t.name for t in infos),
                      tables=[table_payload(ctx.ws, t.name) for t in infos],
                      public_details=[public_detail_section("加载来源和结果", [_table_detail(info) for info in infos]),
                                      public_detail_section("已加载字段", [f"「{info.name}」：{'、'.join(info.column_names)}" for info in infos])])


def _tool_export_excel(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    sheets_raw = args.get("sheets") or []
    sheets: list[tuple[str, str]] = []
    for item in sheets_raw:
        if isinstance(item, dict):
            table = str(item.get("table") or "")
            sheets.append((table, str(item.get("sheet_name") or table)))
        else:
            sheets.append((str(item), str(item)))
    if not sheets:
        raise ValueError("sheets 至少要有一张表，例如 [{\"table\": \"汇总\", \"sheet_name\": \"汇总\"}]")
    _assert_table_scope(ctx, [table for table, _ in sheets])
    info = ctx.host.export_excel(sheets, str(args.get("filename") or "导出结果"))
    return ToolResult(f"已导出文件「{info['filename']}」，包含 {len(sheets)} 个 sheet，用户可以在右侧下载。",
                      summary=f"导出「{info['filename']}」", events=[{"type": "file", **info}],
                      public_details=[public_detail_section("数据来源与写入位置", [f"「{table}」{ctx.ws.require(table).rows} 行 → 「{info['filename']}」的「{sheet}」工作表" for table, sheet in sheets])])
