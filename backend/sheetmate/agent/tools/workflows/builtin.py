"""workflows / builtin tool capabilities."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
import pandas as pd
from sheetmate.engine.tables.sql import sanitize_table_name
from sheetmate.engine.tables.workspace import TableInfo
from sheetmate.recipe.definition.compiler import compile_from_workspace
from sheetmate.recipe.execution.runner import InputSource, RecipeRunner, has_reviewable_output
from sheetmate.recipe.learning.learner import InputMeta, LearnContext
from sheetmate.recipe.learning.matcher import Candidate, match_inputs
from sheetmate.recipe.execution.pipeline import RecipePipeline
from sheetmate.recipe.definition.schema import Recipe
from sheetmate.agent.tools.contracts import AgentContext, ToolResult
from sheetmate.agent.tools.workflows.repair import LLMStepRepairer
from sheetmate.agent.tools.workflows.revision import RevisionStepRepairer, apply_input_changes, revision_request
from sheetmate.agent.tools.presentation import _clip, _status_label, table_payload


def _table_meta(info: TableInfo, ctx: AgentContext) -> InputMeta:
    path = ctx.host.original_file_path(info.file_id) if info.file_id else None
    return InputMeta(filename=info.filename or info.label, sheet=info.sheet, header=info.header, file_id=info.file_id, path=path)


def _recipe_input(ctx: AgentContext, table: str) -> TableInfo:
    info = ctx.ws.require(table)
    if info.source != "uploaded" or info.role != "input":
        inputs = "、".join(_default_inputs(ctx)) or "（请先加载原始文件）"
        raise ValueError(
            f"「{table}」可用于辅助分析；修改流程需要从上传的原始输入表开始以保留格式。"
            f"请将分析得出的处理步骤写入流程，并选择输入表：{inputs}"
        )
    return info


def _learn_context(ctx: AgentContext, requirement: str, inputs: dict[str, str], expected_tables: list[str],
                   recipe_name: str | None = None, mode: str = "learn", file_inputs: dict[str, str] | None = None) -> LearnContext:
    frames, meta = {}, {}
    for alias, table in inputs.items():
        info = _recipe_input(ctx, table)
        frames[alias] = ctx.ws.df(table)
        meta[alias] = _table_meta(info, ctx)
    available = {f["file_id"]: f for f in ctx.host.list_files()} if file_inputs else {}
    for alias, file_id in (file_inputs or {}).items():
        if alias in meta:
            raise ValueError(f"文件别名「{alias}」和输入表重名")
        if file_id not in available:
            raise ValueError("输入文件不属于当前客户或会话")
        meta[alias] = InputMeta(kind="file", filename=available[file_id]["filename"], file_id=file_id,
                                path=ctx.host.original_file_path(file_id))
    expected, sources = {}, []
    for table in expected_tables:
        info = ctx.ws.require(table)
        sheet = info.sheet or table
        expected[sheet if sheet not in expected else table] = ctx.ws.df(table)
        if info.filename:
            sources.append(info.filename)
    return LearnContext(
        requirement=requirement, library=ctx.library, input_frames=frames, input_meta=meta,
        expected=expected, expected_source="、".join(dict.fromkeys(sources)),
        memory_text=ctx.host.memory_text(requirement), field_mapping=ctx.host.field_mapping(),
        customer_id=(ctx.customer or {}).get("id"), conversation_id=ctx.conversation_id, recipe_name=recipe_name,
        mode=mode,
    )


def _default_inputs(ctx: AgentContext) -> list[str]:
    return [t.name for t in ctx.ws.list_tables() if t.source == "uploaded" and t.role == "input"]


def _default_expected(ctx: AgentContext) -> list[str]:
    return [t.name for t in ctx.ws.list_tables() if t.role == "expected"]


def _copy_results(ctx: AgentContext, outputs: dict[str, pd.DataFrame], recipe: Recipe) -> list[dict[str, Any]]:
    """把流程产出的结果表放回会话工作区，方便继续追问和查看。同一个流程再次执行时覆盖上一次的结果表。"""
    payloads = []
    owner = f"recipe:{recipe.id}"
    for index, (sheet, df) in enumerate(outputs.items()):
        name = sanitize_table_name(f"结果_{sheet}")
        info = ctx.ws.info(name)
        if info is not None and not (info.role == "result" and info.created_by == owner):
            name = ctx.ws.unique_name(name)
        ctx.ws.put_dataframe(name, df, label=f"流程结果 · {sheet}", source="computed", role="result",
                             created_by=owner, description=f"「{recipe.name}」v{recipe.version} 的输出",
                             step={"recipe_snapshot": recipe.to_dict(),
                                   "table_ref": next((o.source for o in recipe.output_spec if o.name == sheet),
                                                     recipe.output_spec[index].source if index < len(recipe.output_spec) else recipe.final_outputs()[-1]),
                                   "run_id": (recipe.metadata.get("last_run") or {}).get("run_id")})
        payloads.append(table_payload(ctx.ws, name))
    return payloads


def _recipe_result_text(result: dict[str, Any]) -> str:
    if not result:
        return "流程没有完成。"
    recipe: Recipe = result["recipe"]
    if result.get("error"):
        return f"流程生成失败：{result['error']}"
    lines = [f"本次处理「{recipe.name}」（recipe_id={recipe.id}）：执行状态 {_status_label(result['report']['status'])}。"]
    lines.append("已按用户要求保存为重复流程。" if result.get("saved") else "内部状态：本次没有保存或更新重复流程。无需向用户说明默认行为，也不要主动询问是否保存。")
    report_error = str(result.get("report", {}).get("error") or "").strip()
    if report_error:
        lines.append(f"处理未完成：{report_error}")
        if report_error.startswith("TemplatePreservationError:"):
            lines.append("原始文件未修改，未生成可交付文件。请依据具体错误修正执行方式，重新复制原件后按步骤处理；修改交付以保留原格式的副本为基础，去格式化副本只辅助分析定位。副本内的格式或结构变化不需要再次授权，不要把手动处理或放弃修改作为前置条件。")
    for warning in result["report"].get("output_warnings", []):
        lines.append(f"输出提示：{warning}")
    by_id = {s["step_id"]: s for s in result["report"]["steps"]}
    for step in recipe.ordered_steps():
        rep = by_id.get(step.id, {})
        kind = "Python 脚本" if step.kind == "python" else "SQL" if step.kind == "sql" else "客户独有公式" if step.formula_kind == "customer" else "内置公式"
        rows = f"，结果 {rep['rows_out']} 行" if rep.get("rows_out") is not None else ""
        status = {"repaired": "（自动修复过）", "failed": "（失败）", "warning": "（有提示）"}.get(rep.get("status", ""), "")
        lines.append(f"{step.id}. {step.name}［{step.formula}·{kind}］{step.description}{rows}{status}")
        for w in rep.get("warnings", [])[:2]:
            lines.append(f"   提示：{w}")
    lines.append(f"执行摘要：{result.get('summary', '')}")
    if result.get("verification"):
        lines.append(f"与人工结果比对：{result['verification']['summary']}")
    if result.get("file"):
        sheets = "；".join(f"「{x['sheet']}」{x['rows']} 行 × {len(x.get('columns') or [])} 列（{'、'.join(x.get('columns') or [])}）"
                          for x in result["report"].get("outputs", []))
        lines.append(f"输出文件：「{result['file']['filename']}」（右侧可下载），包含 {sheets}")
    questions = recipe.metadata.get("questions") or []
    delivered = bool(result.get("file")) and result["report"].get("status") in {"success", "warning"}
    if questions and not delivered:
        lines.append("流程提出的待核实信息：" + "；".join(q["question"] for q in questions[:3]))
        lines.append("只询问尚未明确的必要业务信息；当前明确要求优先旧记忆，已明确的修改和副本内格式变化不需要再次确认。")
    if result["report"].get("escalation"):
        lines.append("错误报告：\n" + result["report"]["escalation"])
    return "\n".join(lines)


async def _stream_build_recipe(args: dict[str, Any], ctx: AgentContext) -> AsyncIterator[Any]:
    requirement = str(args.get("requirement") or "").strip()
    if not requirement:
        raise ValueError("requirement 不能为空：请把用户的处理需求写成清楚的处理说明")
    input_tables = [str(t) for t in args.get("input_tables", _default_inputs(ctx))]
    expected_tables = [str(t) for t in args.get("expected_tables", _default_expected(ctx))]
    expected_tables = [t for t in expected_tables if t not in input_tables]
    file_inputs = {str(k): str(v) for k, v in (args.get("input_files") or {}).items()}
    if not input_tables and not file_inputs:
        raise ValueError("没有输入表或文件：请先上传要处理的文件")
    lctx = _learn_context(ctx, requirement, {t: t for t in input_tables}, expected_tables, args.get("recipe_name"), file_inputs=file_inputs)
    pipeline = RecipePipeline(ctx.llm, ctx.library, ctx.host.pipeline_host(), repairer=LLMStepRepairer(ctx.llm),
                              max_rounds=ctx.verify_rounds, max_repair=ctx.max_repair)
    async for event in pipeline.build(lctx, save_recipe=ctx.save_recipe_requested):
        yield event
    yield _finish_recipe_tool(ctx, pipeline.result)


async def _stream_revise_recipe(args: dict[str, Any], ctx: AgentContext) -> AsyncIterator[Any]:
    instruction = str(args.get("instruction") or "").strip()
    if not instruction:
        raise ValueError("instruction 不能为空")
    edit = getattr(ctx, "recipe_edit", None) or {}
    selected_id = args.get("recipe_id") or edit.get("recipe_id")
    recipe = ctx.host.get_recipe(str(selected_id)) if selected_id else ctx.host.latest_conversation_recipe()
    if recipe is None:
        raise ValueError("没有找到要修订的流程。请先用 build_recipe 生成，或提供 recipe_id")
    revision = revision_request(recipe, args, edit, ctx.library)
    saved_version = getattr(ctx.host, "recipe_saved_version", None)
    revision["saved_version"] = (edit["saved_version"] if "saved_version" in edit else
                                 saved_version(recipe.id) if saved_version is not None else
                                 recipe.metadata.get("revision_saved_version", recipe.version))
    resolved_recipe, explicit = apply_input_changes(recipe, revision["input_changes"], args.get("inputs"), revision)
    mapping = _resolve_inputs(ctx, resolved_recipe, explicit)
    activate = getattr(ctx.host, "activate_recipe", None)
    if activate is not None:
        activate(recipe)
    expected_tables = [str(t) for t in args.get("expected_tables", _default_expected(ctx))]
    previous = recipe.metadata.get("requirement") or recipe.description or ""
    requirement = (
        f"历史处理说明（仅沿用未被本次要求覆盖的部分）：\n{previous}\n\n"
        f"本次修改要求（沿用用户维护的流程记忆；未明确取舍的冲突先澄清，用户已明确本次例外则直接执行）：\n{instruction}\n\n"
        f"本次已确认的结构修改与范围（必须按当前真实输入重新运行完整流程）：\n{json.dumps(revision, ensure_ascii=False)}"
    )
    file_aliases = {i.alias for i in resolved_recipe.inputs if i.kind == "file"}
    lctx = _learn_context(ctx, requirement, {k: v for k, v in mapping.items() if k not in file_aliases}, expected_tables, mode="revise",
                          file_inputs={k: v for k, v in mapping.items() if k in file_aliases})
    lctx.revision = revision
    protected_ids = {step["id"] for step in revision.get("protected_steps", [])}
    lctx.protected_steps = {step.id: step.model_copy(deep=True) for step in recipe.steps if step.id in protected_ids}
    pipeline = RecipePipeline(ctx.llm, ctx.library, ctx.host.pipeline_host(),
                              repairer=RevisionStepRepairer(LLMStepRepairer(ctx.llm), revision),
                              max_rounds=ctx.verify_rounds, max_repair=ctx.max_repair)
    async for event in pipeline.build(lctx, base=resolved_recipe, instruction=instruction, save_recipe=ctx.save_recipe_requested):
        yield event
    yield _finish_recipe_tool(ctx, pipeline.result)


def _finish_recipe_tool(ctx: AgentContext, result: dict[str, Any]) -> ToolResult:
    tables = []
    if result.get("ok"):
        recipe = result["recipe"]
        tables = _copy_results(ctx, result.get("outputs") or {}, recipe)
        if "revision_base_version" in recipe.metadata:
            retained = {table["name"] for table in tables}
            for previous in ctx.ws.list_tables():
                if (previous.role == "result" and previous.created_by == f"recipe:{recipe.id}"
                        and previous.name not in retained):
                    ctx.ws.drop(previous.name)
            prune = getattr(ctx.host, "prune_recipe_results", None)
            if prune is not None:
                prune(recipe.id, retained)
    ok = bool(result.get("ok"))
    recipe = result.get("recipe")
    summary = (f"流程「{recipe.name}」v{recipe.version}：{_status_label(result['report']['status'])}" if recipe and result.get("report")
               else result.get("error", "流程未完成"))
    return ToolResult(_clip(_recipe_result_text(result) + _result_tables_hint(tables)), ok=ok, summary=summary, tables=tables)


def _result_tables_hint(tables: list[dict[str, Any]]) -> str:
    """告诉模型流程结果在会话工作区里的表名（流程中间表在独立的运行工作区里，会话里查不到）。"""
    if not tables:
        return ""
    names = "、".join(f"「{t['name']}」（{t['rows']} 行）" for t in tables)
    return f"\n结果已放进当前工作区：{names}，可以直接用这些表名继续查询或分析。"


def _input_mapping_hint(recipe: Recipe) -> str:
    definitions = "；".join(
        f"{i.alias}（{'完整文件' if i.kind == 'file' else '数据表'}"
        f"{('，来源：' + i.source_file) if i.source_file else ''}）" for i in recipe.inputs
    )
    example = json.dumps({i.alias: "file_id" if i.kind == "file" else "工作区表名" for i in recipe.inputs}, ensure_ascii=False)
    return f"流程输入：{definitions}。显式指定格式：inputs={example}；键必须使用这里的完整输入名，文件 ID 可用 list_files 查看"


def _resolve_inputs(ctx: AgentContext, recipe: Recipe, explicit: Any) -> dict[str, str]:
    """用户指定 → 当前范围内的原始绑定 → 按文件名或列匹配。"""
    mapping: dict[str, str] = {}
    if isinstance(explicit, dict):
        mapping = {str(k): str(v) for k, v in explicit.items() if v}
    unknown = set(mapping) - set(recipe.input_aliases())
    if unknown:
        raise ValueError(f"流程没有这些输入：{sorted(unknown)}。{_input_mapping_hint(recipe)}")
    available_files = {f["file_id"]: f for f in ctx.host.list_files()} if any(i.kind == "file" for i in recipe.inputs) else {}
    # 数据库记录可能仍在，但原件已丢失；不能将它当成可执行的绑定或匹配候选。
    available_files = {fid: item for fid, item in available_files.items()
                       if (path := ctx.host.original_file_path(fid)) is not None and path.is_file()}
    tables = [t for t in ctx.ws.list_tables() if t.source == "uploaded" and t.role == "input"]
    bindings = recipe.learned_from.get("input_bindings")
    if isinstance(bindings, dict):
        for inp in recipe.inputs:
            if inp.alias in mapping:
                continue
            binding = bindings.get(inp.alias)
            if not isinstance(binding, dict) or not binding.get("file_id"):
                continue
            file_id = binding["file_id"]
            if inp.kind == "file":
                if file_id in available_files:
                    mapping[inp.alias] = file_id
            else:
                sheet = binding.get("sheet") or inp.sheet
                bound_tables = [t.name for t in tables if t.file_id == file_id and (not sheet or t.sheet == sheet)]
                if len(bound_tables) == 1:
                    mapping[inp.alias] = bound_tables[0]
    pending = [i for i in recipe.inputs if i.alias not in mapping]
    if pending:
        candidates = [Candidate(key=t.name, columns=t.column_names, filename=t.filename, sheet=t.sheet, rows=t.rows)
                      for t in tables if t.name not in mapping.values()]
        candidates.extend(Candidate(key=f["file_id"], kind="file", columns=[], filename=f["filename"])
                          for f in available_files.values() if f.get("kind") == "input" and f["file_id"] not in mapping.values())
        partial = Recipe(name=recipe.name, inputs=pending)
        for alias, hit in match_inputs(partial, candidates, ctx.host.field_mapping()).items():
            mapping[alias] = hit["key"]
    missing = [f"{i.source_file or i.description or i.alias}（输入名：{i.alias}）" for i in recipe.inputs if i.alias not in mapping]
    if missing:
        table_names = "、".join(t.name for t in tables) or "（没有）"
        file_names = "、".join(f"{item['filename']}（file_id={fid}）" for fid, item in available_files.items()) or "（没有）"
        raise ValueError(f"找不到流程需要的输入：{'、'.join(missing)}。工作区里的表：{table_names}。"
                         f"可用文件：{file_names}。{_input_mapping_hint(recipe)}，或先上传对应文件")
    definitions = {i.alias: i for i in recipe.inputs}
    for alias, value in mapping.items():
        if definitions[alias].kind == "file":
            if value not in available_files:
                raise ValueError(f"输入文件不存在、已被删除或不属于当前客户或会话。{_input_mapping_hint(recipe)}")
        else:
            _recipe_input(ctx, value)
    return mapping


async def _stream_run_recipe(args: dict[str, Any], ctx: AgentContext) -> AsyncIterator[Any]:
    recipe_id = str(args.get("recipe_id") or "")
    recipe = ctx.host.get_recipe(recipe_id)
    if recipe is None:
        raise ValueError(f"没有找到流程 {recipe_id}")
    activate = getattr(ctx.host, "activate_recipe", None)
    if activate is not None:
        activate(recipe)
    mapping = _resolve_inputs(ctx, recipe, args.get("inputs"))
    host = ctx.host.pipeline_host()
    handle = host.open_run("replay", recipe, f"在对话中执行「{recipe.name}」")
    from sheetmate.agent.tools.workflows.repair import LLMStepRepairer

    repairer = LLMStepRepairer(ctx.llm) if args.get("allow_repair", True) else None
    runner = RecipeRunner(recipe, ctx.library, handle.ws, field_mapping=ctx.host.field_mapping(),
                          field_mapping_authoritative=bool(getattr(ctx.host, "field_mapping_authoritative", lambda: False)()),
                          repairer=repairer, max_repair=ctx.max_repair, run_id=handle.run_id)
    sources = {}
    for alias, table in mapping.items():
        if next(i for i in recipe.inputs if i.alias == alias).kind == "file":
            item = next(f for f in ctx.host.list_files() if f["file_id"] == table)
            sources[alias] = InputSource(alias=alias, filename=item["filename"], file_id=table, path=ctx.host.original_file_path(table))
            continue
        info = ctx.ws.require(table)
        sources[alias] = InputSource(alias=alias, df=ctx.ws.df(table), filename=info.filename or info.label,
                                     sheet=info.sheet, file_id=info.file_id, header=info.header,
                                     path=ctx.host.original_file_path(info.file_id) if info.file_id else None)
    async for event in runner.run(sources, output_path=handle.output_path, history=host.history_metrics(recipe.id)):
        yield event
    handle.output_path = runner.output_path or handle.output_path
    file_info = host.finish_run(handle, runner.recipe, runner.report)
    runner.recipe.metadata["last_run"] = {"run_id": handle.run_id, "status": runner.report["status"]}
    if file_info:
        for item in file_info.get("files", [file_info]):
            yield {"type": "file", **item}
    note = ""
    if runner.recipe_changed and ctx.save_recipe_requested and runner.report["status"] in {"success", "warning"}:
        saved = ctx.host.store_recipe(runner.recipe, note="执行时自动修复")
        yield {"type": "recipe_saved", **saved, "flow": runner.recipe.flow()}
        note = f"执行中自动修复了第 {runner.report['repaired_steps']} 步，已保存为 v{saved['version']}。"
    elif runner.recipe_changed and ctx.save_recipe_requested:
        note = "本次执行未通过，尚未保存修复后的流程。"
    ok = runner.report["status"] not in {"failed", "escalated"}
    # 只是运行校验没通过时，结果表也放回工作区，方便和用户一起核对差在哪里
    tables = _copy_results(ctx, runner.collect_outputs(), runner.recipe) if has_reviewable_output(runner.report) else []
    text = f"执行流程「{recipe.name}」v{recipe.version}：{_status_label(runner.report['status'])}。{runner.summary()} {note}".strip()
    if file_info:
        text += f"\n输出文件：「{file_info['filename']}」"
    text += _result_tables_hint(tables)
    yield ToolResult(_clip(text), ok=ok, summary=f"执行「{recipe.name}」：{_status_label(runner.report['status'])}", tables=tables)


def _recipe_for_saved_outputs(ctx: AgentContext, targets: list[str]) -> Recipe | None:
    """完整保存已有流程的输出时复用原定义，避免再次编译成第二个流程。"""
    snapshots = []
    refs = []
    for table in targets:
        info = ctx.ws.require(table)
        provenance = info.step or {}
        snapshot = provenance.get("recipe_snapshot")
        if info.role != "result" or not isinstance(snapshot, dict) or not provenance.get("table_ref"):
            return None
        snapshots.append(Recipe.model_validate(snapshot))
        refs.append(provenance["table_ref"])
    recipe = snapshots[0]
    run_id = (recipe.metadata.get("last_run") or {}).get("run_id")
    if any(item.id != recipe.id or (item.metadata.get("last_run") or {}).get("run_id") != run_id for item in snapshots):
        return None
    if len(refs) != len(recipe.output_spec) or set(refs) != {output.source for output in recipe.output_spec}:
        return None
    return recipe


def _tool_save_as_recipe(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    if not ctx.save_recipe_requested:
        raise ValueError("用户没有要求保存重复流程；只完成本次处理，不要主动保存或追问保存。")
    targets = [str(t) for t in (args.get("output_tables") or [])]
    if targets:
        recipe = _recipe_for_saved_outputs(ctx, targets) or compile_from_workspace(
            ctx.ws, targets, ctx.library, name=str(args.get("name") or "分析流程"), description=str(args.get("description") or ""),
            customer_id=(ctx.customer or {}).get("id"), conversation_id=ctx.conversation_id, field_mapping=ctx.host.field_mapping(),
            file_resolver=ctx.host.file_dependency,
        )
    else:
        recipe = (ctx.host.executed_recipe(str(args["run_id"])) if args.get("run_id") else
                  ctx.host.get_recipe(str(args["recipe_id"])) if args.get("recipe_id") else ctx.host.latest_conversation_recipe())
        if recipe is None:
            raise ValueError("没有找到可保存的执行记录；请指定已执行的 run_id 或最终结果表。公式、SQL 和 Python 脚本均可保存。")
    if args.get("name"):
        recipe.name = str(args["name"])
    if args.get("description"):
        recipe.description = str(args["description"])
    saved = ctx.host.store_recipe(recipe, note="按用户要求保存本次处理流程")
    steps = "；".join(f"{s.id}. {s.name}（{s.formula}）" for s in recipe.ordered_steps())
    return ToolResult(f"已保存流程「{recipe.name}」v{saved['version']}（recipe_id={saved['recipe_id']}），共 {len(recipe.steps)} 步：{steps}。"
                      "界面已展示这一个正式流程，无需再调用 get_recipe 展示，或再次保存相同流程。",
                      summary=f"保存流程「{recipe.name}」", events=[{"type": "recipe_saved", **saved, "flow": recipe.flow()}])


def _tool_list_recipes(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    recipes = ctx.host.list_recipes()
    if not recipes:
        return ToolResult("当前客户或会话没有已保存的流程。", summary="没有已保存的流程")
    lines = [f"- recipe_id={item['recipe_id']}「{item['name']}」v{item['version']}"
             for item in recipes]
    return ToolResult("当前可访问的已保存流程：\n" + "\n".join(lines), summary=f"找到 {len(recipes)} 个已保存流程")


def _tool_delete_recipe(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    recipe_id = str(args.get("recipe_id") or "")
    if not recipe_id:
        raise ValueError("请先用 list_recipes 找到要删除的流程 ID。")
    return _delete_recipes([recipe_id], ctx)


def _tool_delete_recipes(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    if args.get("all") is True:
        targets = [item["recipe_id"] for item in ctx.host.list_recipes()]
        if not targets:
            return ToolResult("当前客户或会话没有已保存的流程，无需删除。", summary="没有需要删除的流程")
    else:
        raw = args.get("recipe_ids")
        if not isinstance(raw, list) or not all(isinstance(item, str) and item for item in raw):
            raise ValueError("请提供要删除的 recipe_ids；删除全部流程时使用 all=true。")
        targets = list(dict.fromkeys(raw))
        if not targets:
            raise ValueError("请选择要删除的流程；删除全部流程时使用 all=true。")
    return _delete_recipes(targets, ctx)


def _delete_recipes(recipe_ids: list[str], ctx: AgentContext) -> ToolResult:
    if not set(recipe_ids) <= ctx.delete_recipe_ids:
        confirmation = ctx.host.request_recipe_deletion(recipe_ids)
        question = str(confirmation["question"])
        return ToolResult(question, summary=f"等待确认删除 {len(recipe_ids)} 个流程", end_turn=True,
                          events=[{"type": "clarify", **confirmation}])
    deleted = ctx.host.delete_recipes_confirmed(recipe_ids)
    ctx.delete_recipe_ids.difference_update(recipe_ids)
    names = "、".join(f"「{item['name']}」" for item in deleted)
    return ToolResult(f"已删除流程{names}的全部版本。", summary=f"已删除 {len(deleted)} 个流程",
                      events=[{"type": "recipe_deleted", **item} for item in deleted])


def _tool_get_recipe(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    show_all = args.get("all") is True
    recipe_id = args.get("recipe_id")
    recipe_ids = args.get("recipe_ids")
    if sum((show_all, recipe_id is not None, recipe_ids is not None)) != 1:
        raise ValueError("请选择一种查看范围：单个 recipe_id、多个 recipe_ids，或全部流程 all=true。")
    if show_all:
        targets = [item["recipe_id"] for item in ctx.host.list_recipes()]
    elif recipe_ids is not None:
        if not isinstance(recipe_ids, list) or not recipe_ids or not all(isinstance(item, str) and item.strip() for item in recipe_ids):
            raise ValueError("recipe_ids 必须是非空的流程 ID 列表。")
        targets = list(dict.fromkeys(item.strip() for item in recipe_ids))
    else:
        if not isinstance(recipe_id, str) or not recipe_id.strip():
            raise ValueError("请提供要查看的流程 ID。")
        targets = [recipe_id.strip()]
    if not targets:
        return ToolResult("当前客户或会话没有已保存的流程，没有可展示的流程图。", summary="没有已保存的流程")

    recipes = []
    for target in targets:
        recipe = ctx.host.get_recipe(target)
        if recipe is None:
            raise ValueError("没有找到这个流程" if len(targets) == 1 else "部分流程不存在或不属于当前客户或会话，请重新查看流程列表。")
        recipes.append(recipe)
    lines = [
        f"已向本轮对话界面发送 {len(recipes)} 个流程的原生流程图数据；界面会在正文显示完成后自动渲染客户页同款流程图组件。",
        "回复只需简短说明查看范围或用户关心的结论；不要再画 ASCII/字符框图、Mermaid 或代码块流程图，也不要重复列出完整输入、步骤和输出。",
        "以下详细设置仅供你理解和回答具体问题，不是需要重画的流程图：",
    ]
    events = []
    for recipe in recipes:
        lines.extend([f"流程「{recipe.name}」v{recipe.version}：{recipe.description}", "输入："])
        for inp in recipe.inputs:
            lines.append(f"- {inp.alias}：{inp.description}；文件规则 {inp.file_pattern or '无'}；必需列 {inp.required_columns}")
        lines.append("步骤：")
        for step in recipe.ordered_steps():
            lines.append(f"{step.id}. {step.name}［{step.formula}］{step.description} 参数={json.dumps(step.params, ensure_ascii=False)} → {step.output}")
        lines.append("输出：" + "、".join(f"{o.name}←{o.source}" for o in recipe.output_spec))
        events.append({
            "type": "recipe_view", "recipe_id": recipe.id, "name": recipe.name,
            "description": recipe.description, "version": recipe.version,
            "flow": recipe.flow(),
        })
    summary = f"查看流程「{recipes[0].name}」" if len(recipes) == 1 else f"查看 {len(recipes)} 个流程的流程图"
    return ToolResult(
        _clip("\n".join(lines)), summary=summary, public_text=summary,
        events=events,
    )
