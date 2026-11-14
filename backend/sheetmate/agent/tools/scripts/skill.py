"""Python 脚本技能：独立执行、结果反馈、失败修正和历史脚本复用。"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from sheetmate.engine.scripts.store import normalize_script_title


class ScriptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: StrictStr = Field(min_length=1, max_length=64_000, description="Python 完整脚本。按业务通用规则计算，从实际输入动态取得数据与范围；除用户明确指定的常量外，不写死本次月份、行数、名单或计算结果。可用 pd、np、openpyxl、tables、files、OUTPUT_DIR、result_tables、result、check(condition,message)、deliver_files 和 display_tables；后两者默认空列表，只显式列入需要用户查看的最终文件/结果表。")
    title: StrictStr = Field(default="", max_length=20, description="脚本文件的简短标题，应概括这次处理的目标；超过 20 个字符会自动截断。")
    script_id: StrictStr | None = Field(default=None, pattern=r"^scr_[0-9a-f]{32}$", description="已有脚本文件的唯一 ID；通常由系统自动填写。修改代码后会保存为新的脚本文件。")
    description: StrictStr = Field(min_length=1, max_length=2000, description="本次脚本要完成的用户目标及关键校验。")
    input_tables: list[StrictStr] | None = Field(default=None, max_length=20, description="可选：要传入 tables 的工作区表名；指定 input_files 时默认不传表，否则默认当前可见非期望结果表。建议显式选择实际依赖，[] 表示不传表。")
    input_files: dict[StrictStr, StrictStr] = Field(default_factory=dict, max_length=20, description="需要直接操作工作簿时传 {别名:file_id}，files[别名] 是隔离副本路径。只允许本会话或已授权客户的文件。")

    @field_validator("title", mode="before")
    @classmethod
    def _normalize_title(cls, value: str) -> str:
        return normalize_script_title(value) if value else value


def python_script_tools():
    from sheetmate.agent.tools.contracts import Tool, ToolResult
    from sheetmate.agent.tools.presentation import public_detail_section, table_payload

    parameters = ScriptRequest.model_json_schema()
    # 标题先由展示层归一化和截断，不把既有的可接受输入判为协议错误。
    parameters["properties"]["title"].pop("maxLength", None)

    def details_for(request: ScriptRequest, ctx, tables: list[str], report: dict[str, Any], *, historical: bool = False):
        details = [public_detail_section("执行目标", [request.description])]
        inputs = []
        for name in tables:
            if historical:
                inputs.append(f"运行时绑定表「{name}」；历史记录未保存当时的输入行数。")
            else:
                info = ctx.ws.require(name)
                inputs.append(f"「{name}」：{info.rows} 行" + (f"，来源「{info.filename}」" if info.filename else ""))
        if request.input_files:
            available = {item["file_id"]: item for item in ctx.host.list_files()}
            for alias, file_id in request.input_files.items():
                item = available.get(file_id)
                if item:
                    inputs.append(f"输入「{alias}」 → 「{item['filename']}」的工作副本")
                elif historical:
                    inputs.append(f"运行时绑定文件「{alias}」；原文件名称在当前可访问范围中不可用。")
        if inputs:
            details.append(public_detail_section("处理对象", inputs))
        outputs = [f"「{item['filename']}」" for item in report.get("files", [])]
        outputs.extend(f"「{item['name']}」：{item['rows']} 行，字段：{'、'.join(item.get('columns', []))}" for item in report.get("tables", []))
        if outputs:
            details.append(public_detail_section("实际产出", outputs))
        checks = [f"{'通过' if item.get('ok') else '未通过'}：{item.get('message', '')}" for item in report.get("checks", []) if isinstance(item, dict)]
        details.append(public_detail_section("校验结果", checks or ["本次未记录具体业务校验。" if report.get("ok") else "本次处理未完成，未确认达到处理目标。"]))
        if not report.get("ok") and report.get("error"):
            details.append(public_detail_section("问题反馈", [report["error"]]))
        if report.get("warnings"):
            details.append(public_detail_section("核对提示", report["warnings"]))
        return [section for section in details if section["items"]]

    async def run(args: dict[str, Any], ctx):
        request = ScriptRequest.model_validate(args)
        visible = {table.name for table in ctx.visible_tables()}
        tables = request.input_tables
        if tables is None:
            tables = [] if request.input_files else [table.name for table in ctx.visible_tables() if table.role != "expected"]
        if any(name not in visible for name in tables):
            raise ValueError("input_tables 只能使用当前可见的工作区表名")
        if len(tables) > 20:
            raise ValueError("一次脚本最多传入 20 张表，请显式选择 input_tables")
        cancelled = threading.Event()
        worker = asyncio.create_task(asyncio.to_thread(
            ctx.host.run_python_script, code=request.code, description=request.description,
            input_tables=tables, input_files=request.input_files, cancel_event=cancelled,
            title=request.title, script_id=request.script_id,
        ))
        try:
            report = await asyncio.shield(worker)
        except (asyncio.CancelledError, GeneratorExit):
            cancelled.set()
            # 等待子进程退出，确保临时副本仍存在，且停止后不会在后台悄悄交付文件。
            try:
                await asyncio.shield(worker)
            except Exception:
                pass
            raise
        files = report.get("files", [])
        script = {key: report[key] for key in ("script_id", "script_title", "script_filename") if key in report}
        payload = {
            "run_id": report["run_id"], **script, "status": report["status"], "error": report.get("error", ""),
            "stdout": report.get("stdout", ""), "stderr": report.get("stderr", ""),
            "checks": report.get("checks", []), "result": report.get("result", {}),
            "files": [{"file_id": file["file_id"], "filename": file["filename"], "run_id": report["run_id"]} for file in files],
            "tables": report.get("tables", []), "internal_tables": report.get("internal_tables", []), "warnings": report.get("warnings", []),
        }
        text = json.dumps(payload, ensure_ascii=False)
        if report.get("ok"):
            text += "\n脚本已执行；请继续核对用户目标。files/tables 是明确交付的结果；internal_tables 仅供后续工具计算，不向用户展示或解释。无交付文件时按需设置 deliver_files，测试文件不要列入。"
        else:
            text += "\n本次脚本未完成，未交付本次的部分文件或中间表。根据真实错误修正代码、输入或校验后重试，也可改用内置公式或 SQL。"
        yield ToolResult(text, ok=bool(report.get("ok")),
                         summary=f"脚本 run_id={report['run_id']}：" + ("执行成功" if report.get("ok") else str(report.get("error") or report["status"])[:160]),
                         tables=[table_payload(ctx.ws, name) for name in report.get("table_names", [])],
                         events=[*([{"type": "script", **script, "run_id": report["run_id"]}] if script else []),
                                 *[{"type": "file", **file, "script_run_id": report["run_id"]} for file in files]],
                         public_summary="处理完成" if report.get("ok") else "本次尝试未完成",
                         public_text=(f"已生成 {len(files)} 个文件、{len(report.get('table_names', []))} 张结果表。"
                                      if files or report.get("table_names") else "已完成计算。") if report.get("ok") else "本次处理未完成。",
                         public_details=details_for(request, ctx, tables, report))

    def inspect(args, ctx):
        saved = ctx.host.inspect_python_script(str(args.get("run_id") or ""))
        request = ScriptRequest.model_validate({key: saved[key] for key in ScriptRequest.model_fields if key in saved})
        details = details_for(request, ctx, request.input_tables or [],
                              {**saved, "ok": saved.get("status") == "success"}, historical=True)
        details.insert(0, public_detail_section("查看记录", [saved.get("script_title") or request.description]))
        return ToolResult(json.dumps(saved, ensure_ascii=False), summary=f"查看脚本 run_id={saved['run_id']}",
                          public_summary="已读取处理记录", public_text="已读取上次处理记录。", public_details=details)

    return [
        Tool("run_python_script",
             "［Python 工具］公式和 SQL 优先；仅用于它们不好做或能力范围之外的步骤，例如特殊结构提取、复杂逐单元格排版或自定义算法，或用户明确指定 Python。"
             "常规筛选、计算、匹配、汇总用公式或 SQL，普通分析结果用 export_excel，声明式建表能完成时用 create_workbook；不能仅因脚本灵活或需要保留格式就默认调用。明确超出能力范围时无需先试错；混合任务只为必要部分写脚本。"
             "成功脚本的实际代码、输入绑定、输出与校验可在用户明确要求时用 save_as_recipe 保存为重复流程。脚本中 pd/np/openpyxl 已加载；tables 是传入 DataFrame 副本，files 是按别名传入的文件副本路径；"
             "脚本表达可复用的业务规则，根据输入动态取得月份、分类和数据边界；只有用户明确指定时才能固定业务值，不把探测到的样例数据写死为下一次处理规则。"
             "OUTPUT_DIR 是运行目录，写入文件不会自动交付。deliver_files 默认[]，只有明确列入的最终文件才提供下载，例如 deliver_files=[OUTPUT_DIR / '武汉行程.xlsx']；probe.xlsx 等测试文件留在临时目录。交付文件按内容取有业务含义的名字，不要沿用空白模板的文件名。"
             "result_tables['结果名']=DataFrame 可回传内部数据继续用 SQL/公式；display_tables 默认[]，只把需要用户查看的最终结果表名列入，其他表不展示。"
             "result 可保存简短 JSON 摘要，check(condition,message) 记录校验，失败会阻止本次交付。"
             "使用 openpyxl.load_workbook(files['别名'], rich_text=True) 修改原格式副本并保留单元格内富文本，另存到 OUTPUT_DIR，不覆盖输入。"
             "执行错误、日志、校验和实际产物返回后可继续改代码重试；不要把代码无异常等同于满足用户目标。"
             "无网络、无安装包能力；只能访问隔离副本和运行目录，支持已有的 pandas/numpy/openpyxl 和标准处理库。",
             parameters, stream=run, label=lambda args: "处理表格",
             public_params=lambda args: {"description": args.get("description", "处理表格"),
                                         **({"input_tables": args["input_tables"]} if args.get("input_tables") else {}),
                                         "script_title": normalize_script_title(args.get("title") or args.get("description") or "Python 脚本"),
                                         **({"script_id": args["script_id"], "script_filename": f"{args['script_id']}.py"}
                                            if args.get("script_id") else {})}),
        Tool("inspect_python_script", "读取本会话或客户某次脚本运行的代码、输入绑定、日志、校验及输出文件，用于用户追问时继续调整；run_id 来自脚本结果或文件。",
             {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]},
             run=inspect, label=lambda args: "读取处理记录", public_params=lambda args: {}),
    ]
