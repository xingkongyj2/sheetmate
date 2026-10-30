"""按 Recipe 执行（SheetAgent executor 的流程，执行换成 SheetMate 的 DuckDB 引擎）。

流程：
1. 输入映射：每个 RecipeInput 对应一个文件（或已有表）→ 自动选 sheet、识别表头 → 字段映射 → 模糊列名匹配
   → 检查必需列、行数范围 → 存进运行工作区，表名 = alias
2. 按 id 顺序执行每一步：公式 + 参数 → SQL → DuckDB，结果存成 output 表（中间表全部保留，可单步查看）
3. 每步检查断言；出错或断言不通过 → 调用修复器（内层 ReAct，由 agent/repair.py 提供）→ 最多 N 次
4. 超过次数 → 生成人话错误报告（卡在哪步、为什么、试过什么、建议），停止执行
5. 按 output_spec 写出 Excel，跑校验规则

run() 是异步生成器，边执行边产出事件（前端右侧「执行明细」实时显示）；最终结果在 runner.report。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import aclosing, contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from sheetmate.engine.file_copies import FileCopies, temporary_file_copies
from sheetmate.engine.field_mapping import apply_field_mapping, match_columns
from sheetmate.engine.formulas import FormulaLibrary
from sheetmate.engine.profile import dtype_label
from sheetmate.engine.reader import ReadError, SheetData, read_sheet, read_workbook
from sheetmate.engine.runner import StepOutcome, apply_formula
from sheetmate.engine.sqlutil import frame_to_text, quote_ident
from sheetmate.engine.template_writer import TemplatePreservationError, TemplateSource, write_like_original
from sheetmate.engine.workspace import Workspace
from sheetmate.engine.writer import SheetSpec, write_excel
from sheetmate.recipe.schema import Recipe, RecipeInput, RecipeStep, step_table_refs
from sheetmate.recipe.templating import plan_outputs
from sheetmate.recipe.validator import validate_run


class RecipeInputError(ValueError):
    """输入文件不满足 Recipe 的要求（缺必需列等）。"""


@dataclass
class InputSource:
    """一个输入的来源：文件（路径 + 可选 sheet）或现成的 DataFrame（例如会话里的表）。"""

    alias: str
    path: Path | None = None
    sheet: str | None = None
    df: pd.DataFrame | None = None
    file_id: str | None = None
    filename: str = ""
    header: dict[str, Any] | None = None  # 读取时识别出的表头位置；df 来源时用它重新定位原表


@dataclass
class RepairContext:
    """交给修复器的结构化错误上下文（SheetMate 技术方案 7.2）。"""

    recipe: Recipe
    step: RecipeStep
    error: str
    attempt: int
    history: list[dict[str, Any]]
    tables_text: str  # 这一步能用的表：结构 + 样本
    library: FormulaLibrary


class StepRepairer(Protocol):
    async def repair(self, ctx: RepairContext) -> RecipeStep | None:
        """返回修好的步骤（可以换公式），修不了返回 None。"""


@dataclass
class _AssertionFailure(Exception):
    messages: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return "断言未通过：" + "；".join(self.messages)


class RecipeRunner:
    def __init__(
        self,
        recipe: Recipe,
        library: FormulaLibrary,
        ws: Workspace,
        *,
        field_mapping: dict[str, list[str]] | None = None,
        repairer: StepRepairer | None = None,
        max_repair: int = 3,
        run_id: str = "",
    ):
        self.recipe = recipe
        self.library = library
        self.ws = ws
        self.field_mapping = {**(field_mapping or {}), **recipe.field_mapping}
        self.repairer = repairer
        self.max_repair = max_repair
        self.recipe_changed = False
        self.input_frames: dict[str, pd.DataFrame] = {}
        self.output_path: Path | None = None
        # 每个输入对应的原文件位置：输出时「复制原文件、在里面改写」要用它找样式
        self.sources: dict[str, TemplateSource] = {}
        self._files: FileCopies | None = None
        self._input_paths: set[Path] = set()
        self.report: dict[str, Any] = {
            "run_id": run_id,
            "recipe_id": recipe.id,
            "recipe_version": recipe.version,
            "recipe_name": recipe.name,
            "status": "running",
            "inputs": [],
            "steps": [],
            "outputs": [],
            "validation": None,
            "error": "",
            "escalation": "",
            "output_style": "",
            "failed_stage": "",  # input 输入不符 / step 某步修不好 / validation 运行校验不通过 / error 其他错误
            "repaired_steps": [],
            "duration_ms": 0,
        }

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    async def run(
        self,
        sources: dict[str, InputSource] | None = None,
        *,
        start_step: int = 1,
        stop_step: int | None = None,
        output_path: Path | None = None,
        history: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        with self._working_files():
            async with aclosing(self._run(sources, start_step=start_step, stop_step=stop_step,
                                          output_path=output_path, history=history)) as stream:
                async for event in stream:
                    yield event

    async def _run(
        self,
        sources: dict[str, InputSource] | None = None,
        *,
        start_step: int = 1,
        stop_step: int | None = None,
        output_path: Path | None = None,
        history: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        started = time.perf_counter()
        yield {"type": "recipe_run_start", "run_id": self.report["run_id"], "recipe": self._recipe_brief(),
               "start_step": start_step, "stop_step": stop_step}
        try:
            await self._in_worker(self._copy_templates)
            if sources is not None:
                self._input_paths.update(source.path.resolve() for source in sources.values() if source.path is not None)
                for event in await self._in_worker(self._load_inputs, sources):
                    yield event
            steps = [s for s in self.recipe.ordered_steps() if s.id >= start_step and (stop_step is None or s.id <= stop_step)]
            for step in steps:
                ok = True
                async for event in self._run_step(step):
                    if event.get("type") == "_step_failed":
                        ok = False
                        continue
                    yield event
                if not ok:
                    self.report["status"] = "escalated"
                    self.report["failed_stage"] = "step"
                    break
            finished_all = stop_step is None or stop_step >= max((s.id for s in self.recipe.ordered_steps()), default=0)
            if self.report["status"] == "running" and finished_all and output_path is not None:
                outputs = await self._in_worker(self.collect_outputs)
                written = await self._in_worker(self.write_outputs, output_path, outputs)
                self.report["outputs"] = written
                yield {"type": "recipe_output", "path": str(self.output_path), "sheets": written}
                validation = await self._in_worker(validate_run, self.recipe, self.input_frames or self._input_frames(), outputs, history)
                self.report["validation"] = validation
                yield {"type": "recipe_validation", "report": validation}
            if self.report["status"] == "running":
                failed = self.report["validation"] and self.report["validation"]["status"] == "fail"
                if failed:
                    self.report["failed_stage"] = "validation"
                    self.report["escalation"] = validation_report_text(self.report)
                warned = any(s.get("warnings") for s in self.report["steps"]) or (
                    self.report["validation"] and self.report["validation"]["status"] == "warn"
                )
                self.report["status"] = "failed" if failed else "warning" if warned else "success"
        except (RecipeInputError, ReadError) as exc:
            self.report["status"] = "failed"
            self.report["failed_stage"] = "input"
            self.report["error"] = str(exc)
            self.report["escalation"] = f"输入文件不符合流程要求：{exc}\n建议：确认上传的是正确的文件；如果只是列名变了，可以在客户的「字段映射」里加上别名。"
        except Exception as exc:  # 兜底：不让异常打断事件流
            self.report["status"] = "failed"
            self.report["failed_stage"] = "error"
            self.report["error"] = f"{type(exc).__name__}: {exc}"
        self.report["duration_ms"] = int((time.perf_counter() - started) * 1000)
        yield {"type": "recipe_run_done", "run_id": self.report["run_id"], "status": self.report["status"],
               "summary": self.summary(), "report": self.report}

    # ------------------------------------------------------------------
    # 输入
    # ------------------------------------------------------------------

    def _load_inputs(self, sources: dict[str, InputSource]) -> list[dict[str, Any]]:
        events = []
        missing = [i.alias for i in self.recipe.inputs if i.alias not in sources]
        if missing:
            raise RecipeInputError(f"缺少输入：{missing}（这个流程需要 {len(self.recipe.inputs)} 个输入）")
        for inp in self.recipe.inputs:
            info = self._load_input(inp, sources[inp.alias])
            self.report["inputs"].append(info)
            events.append({"type": "recipe_input", **info})
        return events

    def _load_input(self, inp: RecipeInput, src: InputSource) -> dict[str, Any]:
        warnings: list[str] = []
        sheet: SheetData | None = None
        if src.path is not None and self._files is not None:
            src = replace(src, path=self._files.file_copy(src.path))
        if src.df is not None:
            df, sheet_name = src.df.copy(), src.sheet or inp.sheet or ""
        else:
            if src.path is None:
                raise RecipeInputError(f"输入「{inp.alias}」没有提供文件")
            sheet = self._pick_sheet(inp, src)
            df, sheet_name = sheet.df, sheet.name
        mapping = self._scoped_mapping(inp)
        df, renames = apply_field_mapping(df, mapping)
        if inp.column_matching == "fuzzy" and inp.expected_columns:
            colmap, absent = match_columns(inp.expected_columns, [str(c) for c in df.columns], mapping)
            fuzzy = {act: exp for exp, act in colmap.items() if act != exp}
            if fuzzy:
                df = df.rename(columns=fuzzy)
                renames.update(fuzzy)
            optional_absent = [c for c in absent if c not in inp.required_columns]
            if optional_absent:
                warnings.append(f"新文件里没有这些列（非必需）：{optional_absent}")
        required_missing = [c for c in inp.required_columns if c not in df.columns]
        if required_missing:
            raise RecipeInputError(
                f"「{src.filename or inp.alias}」缺少必需的列 {required_missing}；文件里的列是：{[str(c) for c in df.columns]}"
            )
        if inp.expected_row_range and len(inp.expected_row_range) == 2:
            lo, hi = inp.expected_row_range
            if not lo <= len(df) <= hi:
                warnings.append(f"行数 {len(df)} 超出以往范围 {lo}~{hi}，请确认文件是否完整")
        self.ws.put_dataframe(
            inp.alias, df, label=f"{src.filename or inp.alias} · {sheet_name}".strip(" ·"),
            source="uploaded", file_id=src.file_id, sheet=sheet_name, description=inp.description,
        )
        self.input_frames[inp.alias] = df
        source = self._record_source(inp, src, sheet_name, df, sheet)
        return {"alias": inp.alias, "filename": src.filename, "sheet": sheet_name, "rows": int(len(df)),
                "columns": [str(c) for c in df.columns], "renames": renames, "warnings": warnings,
                "source": source.to_persist() if source else None}

    def _record_source(self, inp: RecipeInput, src: InputSource, sheet_name: str, df: pd.DataFrame,
                       sheet: SheetData | None) -> TemplateSource | None:
        """记下这个输入在原文件里的位置（哪个 sheet、表头在哪、每一列在第几列），输出时用来沿用原样式。"""
        if src.path is None or not Path(src.path).exists():
            if src.file_id and Path(src.filename).suffix.lower() in {".xlsx", ".xlsm", ".xls"}:
                raise RecipeInputError(f"无法找到「{src.filename}」的原文件，不能保留原表格式")
            return None
        try:
            if sheet is None:  # DataFrame 来源（会话里已加载的表）：回原文件重新定位一次
                sheet = read_sheet(Path(src.path), sheet_name or None, header_hint=src.header or inp.header)
        except ReadError as exc:
            raise RecipeInputError(f"无法定位原工作表，不能保留原表格式：{exc}") from exc
        col_index = sheet.header.col_index
        if col_index is None or len(col_index) != len(df.columns):
            raise RecipeInputError("原表列位置与输入数据不一致，无法安全保留原表格式")
        columns = [str(c) for c in df.columns]
        tpl = TemplateSource(
            alias=inp.alias, path=Path(src.path), sheet=sheet.name, header_row=sheet.header.header_row,
            header_rows=sheet.header.header_rows, col_index=list(col_index), columns=columns,
            dtypes={c: dtype_label(df[orig]) for c, orig in zip(columns, df.columns)}, file_id=src.file_id,
        )
        self.sources[inp.alias] = tpl
        return tpl

    def restore_sources(self, infos: list[dict[str, Any]], path_of: Callable[[str], Path | None]) -> None:
        """从断点重跑时没有重新读输入文件：用上次运行记下的位置信息恢复（文件路径靠 file_id 再查）。"""
        for info in infos:
            data = info.get("source")
            if not data or not data.get("file_id"):
                continue
            path = path_of(data["file_id"])
            if path is not None:
                self.sources[info["alias"]] = TemplateSource.from_persist(info["alias"], data, path)
                self._input_paths.add(path.resolve())

    @contextmanager
    def _working_files(self) -> Iterator[FileCopies]:
        if self._files is not None:
            yield self._files
            return
        with temporary_file_copies() as files:
            self._files = files
            try:
                yield files
            finally:
                self.sources = {alias: replace(source, path=files.original_path(source.path))
                                for alias, source in self.sources.items()}
                self._files = None

    def _copy_templates(self) -> None:
        assert self._files is not None
        for alias, source in list(self.sources.items()):
            self._input_paths.add(self._files.original_path(source.path).resolve())
            self.sources[alias] = replace(source, path=self._files.file_copy(source.path))

    async def _in_worker(self, action: Callable[..., Any], *args: Any) -> Any:
        worker = asyncio.create_task(asyncio.to_thread(action, *args))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            self.ws.interrupt()
            try:
                await worker
            except Exception:
                pass
            raise

    def _pick_sheet(self, inp: RecipeInput, src: InputSource) -> SheetData:
        """选 sheet：指定的 > Recipe 记录的同名 sheet > 列重合度最高的；表头位置自动识别，不理想时用 Recipe 记录的位置再试。"""
        assert src.path is not None
        sheets = read_workbook(src.path)
        expected = set(inp.expected_columns)

        mapping = self._scoped_mapping(inp)

        def score(sheet: SheetData) -> float:
            if not expected:
                return 0.0
            mapped, _ = apply_field_mapping(sheet.df, mapping)
            colmap, _ = match_columns(inp.expected_columns, [str(c) for c in mapped.columns], mapping)
            return len(colmap) / len(expected)

        wanted = src.sheet or inp.sheet
        chosen = next((s for s in sheets if s.name == wanted), None) if wanted else None
        if chosen is None:
            chosen = max(sheets, key=score)
        if expected and score(chosen) < 0.5 and inp.header:
            try:
                retry = read_sheet(src.path, chosen.name, header_hint=inp.header)
                if score(retry) > score(chosen):
                    chosen = retry
            except ReadError:
                pass
        return chosen

    def _scoped_mapping(self, inp: RecipeInput) -> dict[str, list[str]]:
        """字段映射只作用于这个输入期望的列：否则对照表里本来就叫「店铺」的列也会被误改成「店铺名称」。"""
        if not inp.expected_columns:
            return self.field_mapping
        wanted = set(inp.expected_columns)
        return {std: aliases for std, aliases in self.field_mapping.items() if std in wanted}

    def _input_frames(self) -> dict[str, pd.DataFrame]:
        return {i.alias: self.ws.df(i.alias) for i in self.recipe.inputs if self.ws.exists(i.alias)}

    # ------------------------------------------------------------------
    # 步骤
    # ------------------------------------------------------------------

    async def _run_step(self, step: RecipeStep) -> AsyncIterator[dict[str, Any]]:
        formula_name = self._formula_name(step.formula)
        yield {"type": "recipe_step_start", "step_id": step.id, "name": step.name, "formula": step.formula,
               "formula_name": formula_name, "description": step.description}
        current = step
        attempts: list[dict[str, Any]] = []
        started = time.perf_counter()
        while True:
            try:
                outcome = await self._in_worker(self._execute, current)
                assertions = self._check_assertions(current, outcome)
                failed = [a for a in assertions if a["status"] == "fail" and a["severity"] == "error"]
                if failed:
                    raise _AssertionFailure([a["message"] for a in failed])
                break
            except Exception as exc:  # 任何错误都进入修复流程（参数错、SQL 错、断言不通过…）
                error = str(exc) or type(exc).__name__
                attempts.append({"attempt": len(attempts) + 1, "formula": current.formula, "params": current.params, "error": error})
                yield {"type": "recipe_step_error", "step_id": step.id, "error": error, "attempt": len(attempts)}
                fixed = None
                if self.repairer is not None and len(attempts) <= self.max_repair:
                    yield {"type": "recipe_repair", "step_id": step.id, "attempt": len(attempts), "status": "trying"}
                    ctx = RepairContext(self.recipe, current, error, len(attempts), attempts, self._tables_text(current), self.library)
                    try:
                        fixed = await self.repairer.repair(ctx)
                    except Exception as repair_exc:  # 修复器本身出错（LLM 不可用等）
                        attempts[-1]["repair_error"] = str(repair_exc)
                        fixed = None
                if fixed is None:
                    self._record_failure(step, current, attempts, started)
                    yield {"type": "recipe_repair", "step_id": step.id, "attempt": len(attempts), "status": "gave_up"}
                    yield {"type": "_step_failed"}
                    return
                attempts[-1]["explanation"] = fixed.explanation
                yield {"type": "recipe_repair", "step_id": step.id, "attempt": len(attempts), "status": "retry",
                       "explanation": fixed.explanation, "formula": fixed.formula, "params": fixed.params}
                current = fixed

        repaired = current is not step
        if repaired:
            self._replace_step(step, current)
            self.report["repaired_steps"].append(step.id)
        current.sql = outcome.sql
        item = self._step_report(current, outcome, assertions, attempts, started, repaired)
        self.report["steps"].append(item)
        yield {"type": "recipe_step_done", **item}

    def _execute(self, step: RecipeStep) -> StepOutcome:
        return apply_formula(
            self.ws, self.library, step.formula, step.params, step.output,
            label=step.name, created_by=f"step_{step.id}", description=step.description,
        )

    def _check_assertions(self, step: RecipeStep, outcome: StepOutcome) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        if outcome.rows_out == 0:
            results.append({"type": "not_empty", "status": "fail", "severity": "warning", "message": "这一步的结果是空表"})
        main_rows = next(iter(outcome.rows_in.values()), 0)
        for a in step.assertions:
            p, ok, msg = a.params, True, ""
            if a.type == "not_empty":
                ok, msg = outcome.rows_out > 0, "结果为空"
            elif a.type == "row_count_range":
                lo, hi = p.get("min", 0), p.get("max", float("inf"))
                ok, msg = lo <= outcome.rows_out <= hi, f"行数 {outcome.rows_out} 不在 {lo}~{hi} 之间"
            elif a.type == "row_count_ratio":
                ratio = outcome.rows_out / main_rows if main_rows else 0
                lo, hi = p.get("min", 0), p.get("max", float("inf"))
                ok, msg = lo <= ratio <= hi, f"行数比 {ratio:.2f} 不在 {lo}~{hi} 之间"
            elif a.type == "match_rate_min":
                rate = outcome.metrics.get("match_rate")
                ok = rate is None or rate >= float(p.get("min", 0.9))
                msg = f"匹配率 {rate:.1%} 低于 {float(p.get('min', 0.9)):.0%}" if rate is not None else ""
            elif a.type == "no_duplicate_keys":
                cols = [c for c in p.get("columns", []) if c in outcome.columns_out]
                if cols:
                    key = ", ".join(quote_ident(c) for c in cols)
                    dups = self.ws.fetch(
                        f"SELECT count(*) FROM (SELECT {key} FROM {quote_ident(outcome.output)} GROUP BY {key} HAVING count(*) > 1)"
                    )[0][0]
                    ok, msg = dups == 0, f"{cols} 有 {dups} 个重复值"
            elif a.type == "no_nulls":
                cols = [c for c in p.get("columns", []) if c in outcome.columns_out]
                nulls = {c: self.ws.fetch(f"SELECT count(*) FROM {quote_ident(outcome.output)} WHERE {quote_ident(c)} IS NULL")[0][0] for c in cols}
                bad = {c: n for c, n in nulls.items() if n}
                ok, msg = not bad, f"有空值：{bad}"
            elif a.type == "columns_exist":
                missing = [c for c in p.get("columns", []) if c not in outcome.columns_out]
                ok, msg = not missing, f"缺少列 {missing}"
            elif a.type == "no_dropped_values":
                dropped = outcome.metrics.get("dropped_values")
                ok = not dropped
                msg = (f"出现了流程里没有的新取值 {dropped}，对应金额 {outcome.metrics.get('dropped_amount'):,.2f} 没有算进结果"
                       if dropped else "")
            results.append({"type": a.type, "status": "pass" if ok else "fail", "severity": a.severity,
                            "message": "" if ok else (a.description or msg)})
        return results

    def _step_report(self, step: RecipeStep, outcome: StepOutcome, assertions: list[dict[str, Any]],
                     attempts: list[dict[str, Any]], started: float, repaired: bool) -> dict[str, Any]:
        warnings = list(outcome.warnings) + [a["message"] for a in assertions if a["status"] == "fail"]
        return {
            "step_id": step.id,
            "name": step.name,
            "formula": step.formula,
            "formula_name": self._formula_name(step.formula),
            "formula_kind": outcome.formula_kind,
            "description": step.description or outcome.description,
            "status": "repaired" if repaired else ("warning" if warnings else "success"),
            "output": step.output,
            "rows_in": outcome.rows_in,
            "rows_out": outcome.rows_out,
            "columns": outcome.columns_out,
            "metrics": outcome.metrics,
            "notes": outcome.notes,
            "warnings": warnings,
            "assertions": assertions,
            "attempts": attempts,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "preview": outcome.preview,
            "sql": outcome.sql,
            "params": step.params,
        }

    def _record_failure(self, original: RecipeStep, current: RecipeStep, attempts: list[dict[str, Any]], started: float) -> None:
        last = attempts[-1]["error"] if attempts else "未知错误"
        self.report["steps"].append({
            "step_id": original.id, "name": original.name, "formula": current.formula,
            "formula_name": self._formula_name(current.formula), "description": original.description,
            "status": "failed", "output": original.output, "error": last, "attempts": attempts,
            "duration_ms": int((time.perf_counter() - started) * 1000), "params": current.params,
        })
        self.report["error"] = f"第 {original.id} 步「{original.name}」失败：{last}"
        self.report["escalation"] = escalation_report(original, attempts, self.max_repair, self.repairer is not None)

    def _replace_step(self, original: RecipeStep, fixed: RecipeStep) -> None:
        fixed.id, fixed.output, fixed.name = original.id, original.output, original.name
        fixed.assertions = fixed.assertions or original.assertions
        fixed.confidence = round(min(original.confidence, 0.95) * 0.85, 2)
        fixed.formula_kind = self.library.resolve(fixed.formula).kind
        self.recipe.steps = [fixed if s.id == original.id else s for s in self.recipe.steps]
        self.recipe_changed = True

    def _tables_text(self, step: RecipeStep) -> str:
        """修复上下文：这一步引用的表 + 所有可用表的结构和样本。"""
        refs = step_table_refs(step, self.library)
        lines = []
        for info in self.ws.list_tables():
            if refs and info.name not in refs:
                lines.append(f"- 表「{info.name}」{info.rows} 行，列：{info.column_names}")
                continue
            sample = self.ws.df(info.name, limit=5)
            cols = "、".join(f"{c['name']}({c['type']})" for c in info.columns)
            lines.append(f"- 表「{info.name}」{info.rows} 行，列：{cols}\n{frame_to_text(sample, limit=5)}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 输出
    # ------------------------------------------------------------------

    def collect_outputs(self) -> dict[str, pd.DataFrame]:
        """按 output_spec 取出要交付的表；没有 output_spec 时交付最后一步（SheetAgent 的兜底规则）。"""
        outputs: dict[str, pd.DataFrame] = {}
        specs = self.recipe.output_spec
        if not specs:
            steps = self.recipe.ordered_steps()
            if steps and self.ws.exists(steps[-1].output):
                outputs["结果"] = self.ws.df(steps[-1].output)
            return outputs
        for spec in specs:
            if not self.ws.exists(spec.source):
                continue
            df = self.ws.df(spec.source)
            if spec.columns:
                keep = [c for c in spec.columns if c in df.columns]
                df = df[keep]
            outputs[spec.name] = df
        return outputs

    def write_outputs(self, path: Path, outputs: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
        """写出 Excel（可以在调整 output_spec 后重新写一次）。

        Excel 必须沿用原工作簿；无法保留时停止输出。CSV 和没有原工作簿的数据使用新表。
        """
        with self._working_files():
            self._copy_templates()
            return self._write_outputs(path, outputs)

    def _check_output_path(self, path: Path) -> None:
        if path.resolve() in self._input_paths or (
            path.exists() and any(source.exists() and path.samefile(source) for source in self._input_paths)
        ):
            raise TemplatePreservationError("编辑结果必须另存副本，不能覆盖原文件")

    def _write_outputs(self, path: Path, outputs: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
        self._check_output_path(path)
        if not outputs:
            raise ValueError("没有可以输出的结果：所有步骤都失败了，或 output_spec 指向的表不存在")
        if self.sources:
            if any(source.path.suffix.lower() == ".xls" for source in self.sources.values()):
                raise TemplatePreservationError("暂不支持保留旧版 .xls 的原格式，请先用 Excel 另存为 .xlsx 后再编辑")
            plans = plan_outputs(self.recipe, self.library, self.sources, outputs)
            base = next((plan.template for plan in plans if plan.template and plan.template.usable()), None)
            if base is not None:
                path = path.with_suffix(base.path.suffix.lower())
            self._check_output_path(path)
            written = write_like_original(path, plans)
            if written is not None:
                self.report["output_style"] = "original"
                self.output_path = path
                return written
            if any(source.path.suffix.lower() in {".xlsx", ".xlsm"} for source in self.sources.values()):
                raise TemplatePreservationError("无法确定结果对应的原工作簿，已停止输出以避免丢失格式")
        self.report["output_style"] = "default"
        split = {o.name: o.split_by for o in self.recipe.output_spec}
        sheets = [SheetSpec(name=name, df=df, split_by=split.get(name)) for name, df in outputs.items()]
        written = write_excel(path, sheets)
        self.output_path = path
        return written

    # ------------------------------------------------------------------

    def _formula_name(self, formula_id: str) -> str:
        try:
            return self.library.resolve(formula_id).spec.name
        except ValueError:
            return formula_id

    def _recipe_brief(self) -> dict[str, Any]:
        return {
            "id": self.recipe.id, "version": self.recipe.version, "name": self.recipe.name,
            "steps": [
                {"id": s.id, "name": s.name, "formula": s.formula, "formula_name": self._formula_name(s.formula),
                 "formula_kind": s.formula_kind, "description": s.description, "output": s.output}
                for s in self.recipe.ordered_steps()
            ],
        }

    def summary(self) -> str:
        r = self.report
        done = [s for s in r["steps"] if s["status"] != "failed"]
        if r["status"] in {"failed", "escalated"}:
            return r["escalation"] or r["error"] or "执行失败"
        parts = [f"完成 {len(done)} 步"]
        if r["repaired_steps"]:
            parts.append(f"自动修复了第 {r['repaired_steps']} 步")
        for out in r["outputs"]:
            cols = out.get("columns") or []
            parts.append(f"输出「{out['sheet']}」{out['rows']} 行 × {len(cols)} 列" + (f"（{'、'.join(cols)}）" if cols else ""))
        if r["validation"]:
            parts.append(r["validation"]["summary"])
        return "，".join(parts)


def has_reviewable_output(report: dict[str, Any]) -> bool:
    """是否生成了可以下载的结果文件：全部步骤都执行完了（即使运行校验没通过，也要让用户能下载核对）。"""
    return report.get("status") in {"success", "warning"} or report.get("failed_stage") == "validation"


def validation_report_text(report: dict[str, Any]) -> str:
    """运行校验没通过时的人话报告：哪些检查没过 + 执行过程中可能的原因 + 建议。"""
    checks = [c for c in (report.get("validation") or {}).get("checks", []) if c["status"] == "fail"]
    lines = ["结果没有通过校验，为了避免交付错误的数据，已标记为失败（文件仍可下载核对）。"]
    for c in checks:
        lines.append(f"· {c['description']}：{c.get('message') or c.get('actual')}")
    hints = [w for s in report.get("steps", []) for w in s.get("warnings", []) if "新的取值" in w or "重复" in w or "一对多" in w]
    if hints:
        lines.append("可能的原因：")
        lines.extend(f"· 第 {s['step_id']} 步「{s['name']}」：{w}" for s in report.get("steps", []) for w in s.get("warnings", [])
                     if w in hints)
    if any("新的取值" in h for h in hints):
        lines.append("建议：这个月出现了流程里没有的类别。在对话里告诉 AI「把新的类别也拆成列」，它会修订流程并生成新版本。")
    elif any("合计" in (c.get("message") or "") for c in checks):
        lines.append("建议：核对输入文件是否完整，或者在对话里让 AI 按这次的数据修订流程。")
    return "\n".join(lines)


def escalation_report(step: RecipeStep, attempts: list[dict[str, Any]], max_repair: int, had_repairer: bool) -> str:
    """超过修复次数后的人话错误报告：卡在哪一步、为什么、试过什么、建议怎么办。"""
    last = attempts[-1]["error"] if attempts else "未知错误"
    lines = [f"卡在第 {step.id} 步「{step.name}」。", f"原因：{last}"]
    tried = [a.get("explanation") for a in attempts if a.get("explanation")]
    if tried:
        lines.append("已自动尝试：" + "；".join(tried))
    elif not had_repairer:
        lines.append("这次是直接按流程执行，没有调用 AI 修复。")
    if len(attempts) > max_repair:
        lines.append(f"已经自动修复 {max_repair} 次仍未成功。")
    lower = last.lower()
    if "不在表" in last or "not found" in lower or "缺少" in last:
        hint = "输入文件的列名可能变了：在客户的「字段映射」里加上新列名，或在对话里让 AI 按新文件修订流程。"
    elif "匹配率" in last:
        hint = "匹配键可能有格式差异或对照表不完整：检查对照表，必要时开启模糊匹配。"
    elif "重复" in last:
        hint = "关键字段出现重复，可能是一单多行：先去重或汇总，再做匹配。"
    else:
        hint = "可以把这条报告发给 AI，让它按新的数据情况修订这一步。"
    lines.append(f"建议：{hint}")
    return "\n".join(lines)
