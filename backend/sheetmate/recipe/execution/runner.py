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
import shutil
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import aclosing, contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from tempfile import mkdtemp
from typing import Any, Protocol
from uuid import uuid4

import pandas as pd

from sheetmate.engine.workbooks.copies import FileCopies, temporary_file_copies
from sheetmate.engine.tables.field_mapping import apply_field_mapping, match_columns, normalize_name
from sheetmate.engine.formulas import FormulaLibrary
from sheetmate.engine.workbooks.profile import dtype_label
from sheetmate.engine.workbooks.reader import ReadError, SheetData, read_sheet, read_workbook
from sheetmate.engine.formulas.runner import StepOutcome, apply_formula
from sheetmate.engine.scripts.runner import ScriptResult, execute_script
from sheetmate.engine.scripts.store import resolve_script_code, script_params
from sheetmate.engine.tables.sql import frame_rows, frame_to_text, quote_ident
from sheetmate.engine.workbooks.template_writer import (
    TemplatePreservationError,
    TemplateSource,
    write_like_original,
)
from sheetmate.engine.tables.workspace import Workspace, validated_row_lineage
from sheetmate.engine.workbooks.atomic import atomic_path
from sheetmate.engine.workbooks.writer import SheetSpec, write_excel
from sheetmate.recipe.definition.schema import (
    REMOVED_OUTPUT_REFS,
    OutputSheet,
    Recipe,
    RecipeInput,
    RecipeStep,
    ValidationRule,
    step_file_refs,
    step_output_tables,
    step_table_refs,
)
from sheetmate.recipe.definition.templating import plan_outputs
from sheetmate.engine.workbooks.verify import file_is_openable, verify_template_output
from sheetmate.recipe.validation.rules import summarize_checks, validate_run


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    with atomic_path(path) as staging:
        frame.to_csv(staging, index=False, encoding="utf-8-sig")


def _same_records(frame: pd.DataFrame, original: pd.DataFrame) -> bool:
    """两份数据逐行逐列完全一致（列名按位置对齐，不比较 dtype）。任何比较异常都视为不一致，不能因此中断运行。"""
    try:
        left = frame.reset_index(drop=True)
        right = original.set_axis(frame.columns, axis=1).reset_index(drop=True)
        if left.equals(right):  # 向量化的快速路径；dtype 不同时会返回 False，再做宽松比较
            return True
        pd.testing.assert_frame_equal(left, right, check_dtype=False, check_exact=True)
        return True
    except Exception:
        return False


# 步骤过程文件按 XLSX 保存的单元格上限；超过则存 CSV，避免每一步都为大表付出秒级的序列化开销。
INTERMEDIATE_XLSX_MAX_CELLS = 60_000


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
    script_stderr: str = ""


class StepRepairer(Protocol):
    async def repair(self, ctx: RepairContext) -> RecipeStep | None:
        """返回修好的步骤（可以换公式），修不了返回 None。"""


def _same_step_content(left: RecipeStep, right: RecipeStep) -> bool:
    """两个步骤的执行内容完全一样：同一个公式、同样的参数（脚本按内容登记，内容一样 script_id 就一样）、同样的断言。"""
    return (left.formula == right.formula and left.kind == right.kind and left.params == right.params
            and [a.model_dump() for a in left.assertions] == [a.model_dump() for a in right.assertions])


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
        field_mapping_authoritative: bool = False,
        repairer: StepRepairer | None = None,
        max_repair: int = 3,
        run_id: str = "",
        artifact_dir: Path | None = None,
        delivery_contract: dict[str, Any] | None = None,
    ):
        self.recipe = recipe
        self.library = library
        self.ws = ws
        reserved = {normalize_name(name) for standard, aliases in (field_mapping or {}).items() for name in [standard, *aliases]}
        fallback = {standard: [alias for alias in aliases if normalize_name(alias) not in reserved]
                    for standard, aliases in recipe.field_mapping.items() if normalize_name(standard) not in reserved}
        self.field_mapping = dict(field_mapping or {}) if field_mapping_authoritative else {**fallback, **(field_mapping or {})}
        self.repairer = repairer
        self.max_repair = max_repair
        self.recipe_changed = False
        self.input_frames: dict[str, pd.DataFrame] = {}
        self.output_path: Path | None = None
        # 每个输入对应的原文件位置：输出时「复制原文件、在里面改写」要用它找样式
        self.sources: dict[str, TemplateSource] = {}
        self._files: FileCopies | None = None
        self._input_paths: set[Path] = set()
        self._file_inputs: dict[str, Path] = {}
        self._artifact_dir = artifact_dir or (ws.path.parent / f"{ws.path.stem}_files" if ws.path is not None else None)
        self._cancel_event = threading.Event()
        self._script_results: dict[int, ScriptResult] = {}
        self._pending_python: dict[int, dict[str, Any]] = {}
        self.delivery_contract = delivery_contract or {}
        self.report: dict[str, Any] = {
            "run_id": run_id,
            "recipe_id": recipe.id,
            "recipe_version": recipe.version,
            "recipe_name": recipe.name,
            "status": "running",
            "inputs": [],
            "steps": [],
            "outputs": [],
            "artifacts": [],  # 包含中间文件，供文件链和断点重跑使用
            "files": [],  # 只有全部步骤成功才交付脚本文件
            "validation": None,
            "validation_checkpoints": [],
            "checkpoint_warnings": [],
            "error": "",
            "escalation": "",
            "output_style": "",
            "output_warnings": [],
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
        self._cancel_event.clear()
        self.report["files"] = []
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
            # 断点之后重跑仍须验证已保留的上游结果，不能绕过其原有业务规则。
            checkpoint_ok = True
            for checkpoint in self._checkpoints():
                if checkpoint["after_step"] >= start_step:
                    continue
                validation = await self._in_worker(self._validate_checkpoint, checkpoint, history)
                yield {"type": "recipe_validation", "report": validation, "checkpoint": True,
                       "after_step": checkpoint["after_step"]}
                if not self._record_checkpoint(checkpoint, validation):
                    checkpoint_ok = False
                    break
            steps = [s for s in self.recipe.ordered_steps() if s.id >= start_step and (stop_step is None or s.id <= stop_step)]
            for step in steps if checkpoint_ok else []:
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
                for checkpoint in self._checkpoints():
                    if checkpoint["after_step"] != step.id:
                        continue
                    validation = await self._in_worker(self._validate_checkpoint, checkpoint, history)
                    yield {"type": "recipe_validation", "report": validation, "checkpoint": True,
                           "after_step": checkpoint["after_step"]}
                    if not self._record_checkpoint(checkpoint, validation):
                        checkpoint_ok = False
                        break
                if not checkpoint_ok:
                    break
            finished_all = stop_step is None or stop_step >= max((s.id for s in self.recipe.ordered_steps()), default=0)
            if self.report["status"] == "running" and finished_all and output_path is not None:
                outputs = await self._in_worker(self.collect_outputs)
                written = await self._in_worker(self.write_outputs, output_path, outputs) if outputs else []
                self.report["outputs"] = written
                contract_checks = self._delivery_contract_checks()
                intentionally_empty = not outputs and bool(self.recipe.metadata.get("explicit_outputs")) and not self.recipe.flow()["output_files"]
                if not outputs:
                    deliveries = self._delivery_files()
                    if not deliveries and not intentionally_empty and not contract_checks:
                        raise ValueError("没有可以输出的结果：流程没有生成结果表或交付文件")
                    if deliveries:
                        self.output_path = Path(deliveries[-1]["path"])
                        self.report["output_style"] = "script"
                if self.output_path is None and not intentionally_empty and not contract_checks:
                    raise ValueError("没有可以输出的结果：流程没有生成结果表或交付文件")
                if self.output_path is not None:
                    yield {"type": "recipe_output", "path": str(self.output_path), "sheets": written}
                validation = await self._in_worker(self._validate_outputs, outputs, history)
                verification = self.report.pop("output_verification", [])
                if verification:  # 回读实际生成的文件得到的核对结果，与业务校验一起汇总，失败不能当成通过
                    validation = self._validation_summary([*validation["checks"], *verification])
                if contract_checks:
                    validation = self._validation_summary([*validation["checks"], *contract_checks])
                    self.report["output_contract_failed"] = True
                self.report["validation"] = validation
                yield {"type": "recipe_validation", "report": validation}
            if self.report["status"] == "running":
                self.report["output_warnings"] = list(dict.fromkeys(
                    self.report["output_warnings"] + self.report["checkpoint_warnings"]))
                failed = self.report["validation"] and self.report["validation"]["status"] in {"fail", "not_evaluated"}
                if failed:
                    self.report["failed_stage"] = "output_contract" if self.report.get("output_contract_failed") else "validation"
                    self.report["escalation"] = ("实际生成的文件不符合本次输出修改要求，尚未交付：" + "；".join(
                        item["message"] for item in self.report["validation"]["checks"]
                        if item["status"] == "fail" and item.get("rule_type") == "output_contract")
                        if self.report.get("output_contract_failed") else validation_report_text(self.report))
                warned = bool(self.report.get("output_warnings")) or any(s.get("warnings") for s in self.report["steps"]) or (
                    self.report["validation"] and self.report["validation"]["status"] == "warn"
                )
                self.report["status"] = "failed" if failed else "warning" if warned else "success"
            if self.report["status"] in {"success", "warning"} and finished_all:
                self.report["files"] = self._delivery_files()
        except asyncio.CancelledError:
            self._cancel_event.set()
            for step_id in list(self._pending_python):
                self._discard_python(step_id)
            self.report.update(status="cancelled", files=[], failed_stage="step", error="流程执行已停止")
            raise
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
        if src.path is not None:
            self._file_inputs[inp.alias] = Path(src.path)
        original_path = self._files.original_path(src.path) if src.path is not None and self._files is not None else src.path
        file_info = {"kind": inp.kind, "file_id": src.file_id, "path": str(original_path) if original_path is not None else ""}
        if inp.kind == "file":
            if src.path is None or not src.path.is_file():
                raise RecipeInputError(f"输入「{inp.alias}」没有提供可读取的文件")
            return {"alias": inp.alias, "filename": src.filename or src.path.name, "sheet": "", "rows": 0,
                    "columns": [], "renames": {}, "warnings": [], "source": None, **file_info}
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
        if sheet is not None and sheet.quality.get("missing_formula_cache"):
            missing_positions = set(sheet.quality.get("missing_formula_cache_columns", []))
            affected = [renames.get(str(column), str(column)) for column, position in
                        zip(sheet.df.columns, sheet.header.col_index or []) if position in missing_positions]
            required_affected = [column for column in affected if column in inp.required_columns]
            notice = (f"工作表「{sheet.name}」有 {sheet.quality['missing_formula_cache']} 个公式缺少计算结果"
                      f"，涉及列 {affected}；请用表格软件重新计算并保存")
            if required_affected:
                raise RecipeInputError(notice + f"，本次必需列 {required_affected} 不可可靠读取")
            warnings.append(notice)
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
                "source": source.to_persist() if source else None, **file_info}

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
        # 现成 DataFrame 可能已经重排或改值，只有实际数据与原件逐行一致才能播种身份。
        if tpl.usable() and df.shape == sheet.df.shape and _same_records(df, sheet.df):
            tpl.lineage_id = uuid4().hex
            self.ws.update_meta(inp.alias, row_lineage={"source": tpl.lineage_id, "identity": len(df)})
        self.sources[inp.alias] = tpl
        return tpl

    def restore_sources(self, infos: list[dict[str, Any]], path_of: Callable[[str], Path | None]) -> None:
        """从断点重跑时没有重新读输入文件：用上次运行记下的位置信息恢复（文件路径靠 file_id 再查）。"""
        for info in infos:
            data = info.get("source")
            file_id = info.get("file_id") or (data or {}).get("file_id")
            path = path_of(file_id) if file_id else None
            if path is None and info.get("path"):
                candidate = Path(info["path"])
                path = candidate if candidate.is_file() else None
            if path is not None:
                self._file_inputs[info["alias"]] = path
                self._input_paths.add(path.resolve())
            if not data or not data.get("file_id"):
                continue
            if path is not None:
                self.sources[info["alias"]] = TemplateSource.from_persist(info["alias"], data, path)
                self._input_paths.add(path.resolve())

    def restore_artifacts(self, artifacts: list[dict[str, Any]]) -> None:
        """恢复已成功步骤的文件；服务层先按 start_step 过滤掉需要重跑的步骤。"""
        self.report["artifacts"] = [dict(item) for item in artifacts]
        available = {item["ref"]: Path(item["path"]) for item in artifacts}
        for step in self.recipe.ordered_steps():
            for target, spec in step.params.get("file_tables", {}).items():
                path = available.get(spec["ref"])
                if path is not None and path.is_file() and self.ws.exists(target):
                    sheet = read_sheet(path, spec.get("sheet"), header_hint=spec.get("header"))
                    self._record_file_table_source(target, path, sheet)

    def _delivery_files(self) -> list[dict[str, Any]]:
        steps = self.recipe.ordered_steps()
        consumed = {ref for step in steps for ref in step_file_refs(step)}
        for index, step in enumerate(steps):
            downstream = {ref for later in steps[index + 1:] for ref in step_table_refs(later, self.library)}
            consumed.update(spec["ref"] for table, spec in step.params.get("file_tables", {}).items() if table in downstream)
        intermediate_exports = {step.id for step in steps[:-1] if step.params.get("workbook_export") is not None}
        consumed.update(self.recipe.metadata.get(REMOVED_OUTPUT_REFS) or [])  # 用户已从流程中删除的输出
        return [dict(item) for item in self.report["artifacts"]
                if item["ref"] not in consumed and item["step_id"] not in intermediate_exports]

    def _delivery_contract_checks(self) -> list[dict[str, Any]]:
        """Check output edits against real final artifacts in the current attempt."""
        deliveries = self._delivery_files()
        checks = []
        if self.delivery_contract.get("validate_declared_files"):
            # flow() excludes files consumed by later steps. Read the current
            # recipe each round because refinement may change its declarations.
            generated = {item["ref"] for item in deliveries}
            missing = [item for item in self.recipe.flow()["output_files"]
                       if item.get("ref") and item["ref"] not in generated]
            if missing:
                checks.append({"rule_id": "revision_declared_files", "rule_type": "output_contract",
                               "description": "实际生成声明的输出文件", "status": "fail", "severity": "error",
                               "actual": [item["ref"] for item in missing],
                               "message": "本次新增或修改的流程声明了尚未实际生成的最终输出文件："
                                          + "、".join(item["ref"] for item in missing)
                                          + "；请修改生成代码和 deliver_files，并完整重跑后交付。"})
        for selected in self.delivery_contract.get("removed_files", []):
            retained = set(selected.get("retained_refs", []))
            unexpected = [item for item in deliveries
                          if item["name"] == selected["name"] and item["ref"] not in retained]
            if unexpected:
                checks.append({"rule_id": "revision_removed_file", "rule_type": "output_contract",
                               "description": "移除所选输出文件", "status": "fail", "severity": "error",
                               "actual": [item["ref"] for item in unexpected],
                               "message": f"本次要求移除的输出文件「{selected['name']}」仍被脚本实际生成并保留为最终产物；"
                                          "请修改生成代码并完整重跑，不能只删除 output_files 声明。"})
        missing = set(self.delivery_contract.get("protected_files", [])) - {item["name"] for item in deliveries}
        if missing:
            checks.append({"rule_id": "revision_protected_files", "rule_type": "output_contract",
                           "description": "保留未修改的输出文件", "status": "fail", "severity": "error",
                           "actual": sorted(missing),
                           "message": "本次未要求删除的输出文件没有实际生成：" + "、".join(sorted(missing))})
        return checks

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
                self._file_inputs = {alias: files.original_path(path) for alias, path in self._file_inputs.items()}
                self._files = None

    def _copy_templates(self) -> None:
        assert self._files is not None
        for alias, source in list(self.sources.items()):
            self._input_paths.add(self._files.original_path(source.path).resolve())
            self.sources[alias] = replace(source, path=self._files.file_copy(source.path))
        for alias, path in list(self._file_inputs.items()):
            self._input_paths.add(self._files.original_path(path).resolve())
            self._file_inputs[alias] = self._files.file_copy(path)

    async def _in_worker(self, action: Callable[..., Any], *args: Any) -> Any:
        worker = asyncio.create_task(asyncio.to_thread(action, *args))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            self._cancel_event.set()
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

        # 自动表头失败时先尝试已记录的位置，再判断候选歧义。
        if expected and inp.header:
            for index, sheet in enumerate(sheets):
                if score(sheet) >= 0.5:
                    continue
                try:
                    retry = read_sheet(src.path, sheet.name, header_hint=inp.header)
                    if score(retry) > score(sheet):
                        sheets[index] = retry
                except ReadError:
                    pass

        wanted = src.sheet or inp.sheet
        chosen = next((s for s in sheets if s.name == wanted), None) if wanted else None
        if chosen is None:
            scored = sorted(((score(s), s) for s in sheets), key=lambda item: item[0], reverse=True)
            best_score, chosen = scored[0]
            if expected and best_score < 0.5:
                raise RecipeInputError(
                    f"无法可靠匹配输入「{inp.alias}」的工作表：最高字段匹配率 {best_score:.0%}；"
                    f"现有工作表：{'、'.join(s.name for s in sheets)}"
                )
            if len(scored) > 1 and scored[1][0] == best_score:
                candidates = [s.name for candidate_score, s in scored if candidate_score == best_score]
                raise RecipeInputError(
                    f"输入「{inp.alias}」的工作表匹配存在歧义：{'、'.join(candidates)} 字段匹配率相同，"
                    "请明确指定本次工作表"
                )
        if expected and score(chosen) < 0.5:
            raise RecipeInputError(
                f"无法可靠匹配输入「{inp.alias}」的工作表「{chosen.name}」：字段匹配率 {score(chosen):.0%}"
            )
        quality = chosen.quality
        if quality.get("truncated"):
            raise RecipeInputError(
                f"工作表「{chosen.name}」有 {quality.get('physical_rows')} 行，超过当前读取上限 "
                f"{quality.get('read_rows')} 行，未执行不完整数据的处理"
            )
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
        if step.kind == "python":
            step.params = script_params(step.params, title=step.name[:20])
        formula_name = self._formula_name(step.formula)
        yield {"type": "recipe_step_start", "step_id": step.id, "name": step.name, "formula": step.formula,
               "kind": step.kind, "formula_name": formula_name, "description": step.description,
               **({key: step.params[key] for key in ("script_id", "script_title", "script_filename") if key in step.params}
                  if step.kind == "python" else {})}
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
                if current.kind == "python":
                    await self._in_worker(self._discard_python, current.id)
                error = str(exc) or type(exc).__name__
                script_result = self._script_results.get(current.id) if current.kind == "python" else None
                script_stderr = script_result.stderr[-8000:] if script_result is not None else ""
                attempts.append({"attempt": len(attempts) + 1, "formula": current.formula, "params": current.params, "error": error,
                                 **({"stderr": script_stderr} if script_stderr else {})})
                yield {"type": "recipe_step_error", "step_id": step.id, "error": error, "attempt": len(attempts)}
                fixed = None
                if self.repairer is not None and len(attempts) <= self.max_repair:
                    yield {"type": "recipe_repair", "step_id": step.id, "attempt": len(attempts), "status": "trying"}
                    ctx = RepairContext(self.recipe, current, error, len(attempts), attempts, self._tables_text(current), self.library,
                                        script_stderr=script_stderr)
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
                if fixed.kind == "python":
                    fixed.params = script_params(fixed.params, title=fixed.name[:20])
                if _same_step_content(fixed, current):
                    # 修复器交回来的步骤和刚才失败的完全一样：再执行一次只会得到同样的错误。
                    # 继续循环是在白白烧模型调用（以前同一个错误会重复到上限 30 次）。
                    attempts[-1]["repair_error"] = "修复没有改变这一步的内容，再试也会得到同样的错误"
                    self._record_failure(step, current, attempts, started)
                    yield {"type": "recipe_repair", "step_id": step.id, "attempt": len(attempts), "status": "gave_up"}
                    yield {"type": "_step_failed"}
                    return
                attempts[-1]["explanation"] = fixed.explanation
                yield {"type": "recipe_repair", "step_id": step.id, "attempt": len(attempts), "status": "retry",
                       "explanation": fixed.explanation, "formula": fixed.formula, "params": fixed.params}
                current = fixed

        if current.kind == "python":
            self._commit_python(current.id)
        repaired = current is not step
        if repaired:
            self._replace_step(step, current)
            self.report["repaired_steps"].append(step.id)
        current.sql = outcome.sql
        item = self._step_report(current, outcome, assertions, attempts, started, repaired)
        try:
            item["output_files"] = [*item.get("output_files", []),
                                    *await self._in_worker(self._snapshot_step_tables, current)]
        except Exception as exc:
            item["warnings"].append(f"过程文件归档失败：{exc}")
            item["status"] = "warning" if not repaired else item["status"]
        self.report["steps"].append(item)
        yield {"type": "recipe_step_done", **item}

    def _snapshot_step_tables(self, step: RecipeStep) -> list[dict[str, Any]]:
        """保留每一步当时的完整结果，后续覆盖同名表或重跑也不改变旧文件。"""
        tables = [name for name in step_output_tables(step) if self.ws.exists(name)]
        if not tables:
            return []
        if self._artifact_dir is None:
            self._artifact_dir = Path(mkdtemp(prefix="sheetmate_recipe_files_"))
        self._artifact_dir.mkdir(parents=True, exist_ok=True)
        directory = Path(mkdtemp(prefix=f"snapshot_{step.id}_", dir=self._artifact_dir))
        snapshots = []
        for index, table in enumerate(tables):
            path = directory / f"step_{step.id}_{index}.xlsx"
            frame = self.ws.df(table)
            if frame.size > INTERMEDIATE_XLSX_MAX_CELLS:
                # 写 XLSX 比计算本身慢一到两个数量级；大结果的过程文件直接存 CSV（同样可查看、下载）。
                path = path.with_suffix(".csv")
                _write_csv(path, frame)
            else:
                try:
                    write_excel(path, [SheetSpec(name=table, df=frame)])
                except Exception:
                    path.unlink(missing_ok=True)
                    path = path.with_suffix(".csv")
                    _write_csv(path, frame)
            # 一步只有一张表时，文件名只用步骤名；表名常常和步骤名一样，写两遍没有意义
            label = step.name if len(tables) == 1 or table == step.name else f"{step.name}_{table}"
            snapshots.append({"step_id": step.id, "step_name": step.name, "table": table,
                              "name": f"{step.id:02d}_{label}{path.suffix}", "path": str(path.resolve()),
                              "intermediate": True})
        return snapshots

    def _execute(self, step: RecipeStep) -> StepOutcome:
        if step.kind == "python":
            return self._execute_python(step)
        return apply_formula(
            self.ws, self.library, step.formula, step.params, step.output,
            label=step.name, created_by=f"step_{step.id}", description=step.description,
        )

    def _execute_python(self, step: RecipeStep) -> StepOutcome:
        """执行已保存脚本；成功后先暂存，断言通过才把文件加入可恢复的产物清单。"""
        self._script_results.pop(step.id, None)
        refs = step_table_refs(step, self.library)
        frames = {name: self.ws.df(name) for name in refs}
        bindings = step.params.get("table_bindings", {})
        bound_sources = set(bindings.values())
        tables = {name: frames[name] for name in step.params.get("input_tables", []) if name not in bound_sources}
        tables.update({alias: frames[name] for alias, name in bindings.items()})
        available = {**self._file_inputs, **{item["ref"]: Path(item["path"]) for item in self.report["artifacts"]}}
        files: dict[str, Path] = {}
        for alias, ref in step.params.get("input_files", {}).items():
            path = available.get(ref)
            if path is None or not path.is_file():
                raise RecipeInputError(f"脚本输入文件「{ref}」不可用，请从产生该文件的步骤重新运行")
            files[alias] = path
        if self._artifact_dir is None:
            self._artifact_dir = Path(mkdtemp(prefix="sheetmate_recipe_files_"))
        self._artifact_dir.mkdir(parents=True, exist_ok=True)
        directory = Path(mkdtemp(prefix=f"step_{step.id}_", dir=self._artifact_dir))
        pending = {"directory": directory, "tables": [], "backups": {}, "sources": {}, "artifacts": []}
        self._pending_python[step.id] = pending
        try:
            if step.params.get("workbook_export") is not None:
                result = self._export_workbook(step, directory)
            else:
                result = execute_script(resolve_script_code(step.params), tables, files, directory, cancel_event=self._cancel_event)
            self._script_results[step.id] = result
            if not result.ok:
                raise ValueError(result.error or "Python 脚本执行失败")
            mapping = step.params.get("output_tables")
            if mapping is None:
                if len(result.result_tables) > 1:
                    raise ValueError("脚本返回多个结果表时必须保存 output_tables 映射")
                mapping = {name: step.output for name in result.result_tables}
            if not isinstance(mapping, dict) or any(not isinstance(name, str) or not name.strip() for name in mapping.values()):
                raise ValueError("脚本 output_tables 映射无效")
            if len(set(mapping.values())) != len(mapping):
                raise ValueError("脚本输出表名不能重复")
            missing = set(mapping) - set(result.result_tables)
            if missing:
                raise ValueError(f"脚本没有返回流程要求的结果表：{sorted(missing)}")
            pending["artifacts"] = [{"step_id": step.id, "ref": f"{step.output}/{path.name}",
                                      "name": path.name, "path": str(path.resolve())} for path in result.output_files]
            available.update({item["ref"]: Path(item["path"]) for item in pending["artifacts"]})
            outputs = {target: result.result_tables[name] for name, target in mapping.items()}
            file_sources: dict[str, tuple[Path, SheetData]] = {}
            for target, source in step.params.get("file_tables", {}).items():
                if target in outputs:
                    raise ValueError(f"文件读取表「{target}」与脚本结果表重名")
                path = available.get(source.get("ref"))
                if path is None or not path.is_file():
                    raise ValueError(f"脚本没有生成后续步骤要求的文件：{source.get('ref')}")
                sheet = read_sheet(path, source.get("sheet"), header_hint=source.get("header"))
                outputs[target] = sheet.df
                file_sources[target] = (path, sheet)
            input_aliases = set(self.recipe.input_aliases())
            if input_aliases & set(outputs):
                raise ValueError("脚本输出不能覆盖流程输入表")
            for name, frame in outputs.items():
                if self.ws.exists(name):
                    pending["backups"][name] = (self.ws.df(name), self.ws.require(name).to_dict())
                pending["sources"][name] = self.sources.get(name)
                pending["tables"].append(name)
                self.ws.put_dataframe(name, frame, label=name, source="computed", role="result",
                                      created_by=f"step_{step.id}", description=step.description,
                                      step={"kind": "python", "formula": "python_script", "params": step.params})
                if name in file_sources:
                    path, sheet = file_sources[name]
                    self._record_file_table_source(name, path, sheet)
                else:
                    self.sources.pop(name, None)
            primary = step.output if step.output in outputs else next(iter(outputs), None)
            primary_frame = outputs.get(primary) if primary is not None else None
            preview = ({"columns": self.ws.require(primary).column_names,
                        "rows": frame_rows(self.ws.df(primary, limit=20)), "total": len(primary_frame)}
                       if primary is not None else {})
            return StepOutcome(
                formula="python_script", formula_kind="builtin", output=primary or step.output, params=step.params,
                sql="", inputs=refs, rows_in={name: len(frame) for name, frame in frames.items()},
                rows_out=len(primary_frame) if primary_frame is not None else 0,
                columns_out=[str(c) for c in primary_frame.columns] if primary_frame is not None else [],
                description=step.description, notes={"output_tables": list(outputs), "file_only": not outputs},
                warnings=list(result.input_notes), duration_ms=result.duration_ms, preview=preview,
            )
        except BaseException:
            self._discard_python(step.id)
            raise

    def _export_workbook(self, step: RecipeStep, directory: Path) -> ScriptResult:
        """物化保存流程中的工作簿快照，沿用普通回放的原表样式写出器。"""
        export = step.params["workbook_export"]
        filename = export.get("filename", "")
        if not isinstance(filename, str) or not filename or "/" in filename or "\\" in filename or filename in {".", ".."}:
            raise ValueError("工作簿导出的 filename 必须是文件名")
        original_recipe, original_path = self.recipe, self.output_path
        original_style = self.report["output_style"]
        original_warnings = list(self.report["output_warnings"])
        try:
            specs = [OutputSheet.model_validate(spec) for spec in export["output_spec"]]
            self.recipe = self.recipe.model_copy(update={"output_spec": specs})
            outputs = self.collect_outputs()
            if len(outputs) != len(specs):
                raise ValueError("工作簿快照的部分来源表不存在")
            self.write_outputs(directory / filename, outputs)
            assert self.output_path is not None
            return ScriptResult(ok=True, status="success", output_files=[self.output_path],
                                checks=[{"ok": True, "message": "工作簿快照已按保存的输出规则生成"}],
                                result={"warnings": list(self.report["output_warnings"])})
        finally:
            self.recipe, self.output_path = original_recipe, original_path
            self.report["output_style"] = original_style
            self.report["output_warnings"] = original_warnings

    def _discard_python(self, step_id: int) -> None:
        pending = self._pending_python.pop(step_id, None)
        if pending is None:
            return
        for name in pending["tables"]:
            self.ws.drop(name)
            if pending["sources"][name] is None:
                self.sources.pop(name, None)
            else:
                self.sources[name] = pending["sources"][name]
            if name in pending["backups"]:
                frame, metadata = pending["backups"][name]
                self.ws.put_dataframe(name, frame, **{key: value for key, value in metadata.items()
                                                      if key not in {"name", "columns", "rows"}})
        shutil.rmtree(pending["directory"], ignore_errors=True)

    def _record_file_table_source(self, target: str, path: Path, sheet: SheetData) -> None:
        """文件回表仍保留脚本工作簿的模板位置，后续公式和 SQL 可继续沿用其排版。"""
        self._record_source(RecipeInput(alias=target), InputSource(target, path=path), sheet.name, sheet.df, sheet)

    def _commit_python(self, step_id: int) -> None:
        pending = self._pending_python.pop(step_id, None)
        if pending is not None:
            kept = [item for item in self.report["artifacts"] if item["step_id"] != step_id]
            self.report["artifacts"] = kept + pending["artifacts"]

    def _script_report(self, step: RecipeStep) -> dict[str, Any]:
        if step.kind != "python":
            return {}
        reference = {key: step.params[key] for key in ("script_id", "script_title", "script_filename") if key in step.params}
        result = self._script_results.get(step.id)
        if result is None:
            return reference
        return {**reference, "stdout": result.stdout, "stderr": result.stderr, "checks": result.checks, "result": result.result,
                "input_notes": list(result.input_notes),
                "output_files": [dict(item) for item in self.report["artifacts"] if item["step_id"] == step.id]}

    def _validate_outputs(self, outputs: dict[str, pd.DataFrame], history: list[dict[str, Any]] | None) -> dict[str, Any]:
        validation = validate_run(self.recipe, self.input_frames or self._input_frames(), outputs, history)
        if not outputs and self.recipe.metadata.get("explicit_outputs") and not self.recipe.flow()["output_files"]:
            checks = [item for item in validation["checks"] if item["rule_id"] != "auto_not_empty"]
            checks.append({"rule_id": "auto_output_contract", "rule_type": "output_contract",
                           "description": "按要求取消最终输出", "status": "pass", "severity": "error", "actual": 0,
                           "message": "完整执行流程，当前输出声明为空"})
            return self._validation_summary(checks)
        if outputs or not self.report["artifacts"] or self.recipe.output_spec:
            return validation
        # 文件脚本的交付物已经过沙箱文件检查；不能要求它额外伪造一张结果表。
        checks = [item for item in validation["checks"] if item["rule_id"] != "auto_not_empty"]
        paths = [Path(item["path"]) for item in self._delivery_files()]
        valid = bool(paths) and all(path.is_file() and path.stat().st_size > 0 for path in paths)
        checks.append({"rule_id": "auto_files_exist", "rule_type": "files_exist", "description": "脚本交付文件完整",
                       "status": "pass" if valid else "fail", "severity": "error", "actual": len(paths),
                       "message": "" if valid else "部分脚本交付文件不存在或为空"})
        if valid:  # 脚本直接生成的 Excel 文件也要能被重新打开，与其他交付路径同一标准
            checks.extend(file_is_openable(path) for path in paths)
        for step in self.report["steps"]:
            for index, item in enumerate(step.get("checks", [])):
                checks.append({"rule_id": f"script_{step['step_id']}_{index}", "rule_type": "script_check",
                               "description": item["message"], "status": "pass" if item["ok"] else "fail",
                               "severity": "error", "message": "" if item["ok"] else item["message"]})
        return self._validation_summary(checks)

    def _checkpoints(self) -> list[dict[str, Any]]:
        checkpoints = self.recipe.metadata.get("validation_checkpoints", [])
        if not isinstance(checkpoints, list):
            raise ValueError("流程校验检查点格式无效")
        for checkpoint in checkpoints:
            if not isinstance(checkpoint, dict) or not isinstance(checkpoint.get("after_step"), int):
                raise ValueError("流程校验检查点缺少 after_step")
            if checkpoint["after_step"] not in {step.id for step in self.recipe.ordered_steps()}:
                raise ValueError(f"流程校验检查点的第 {checkpoint['after_step']} 步不存在或已停用")
        return sorted(checkpoints, key=lambda checkpoint: checkpoint["after_step"])

    def _validate_checkpoint(self, checkpoint: dict[str, Any], history: list[dict[str, Any]] | None) -> dict[str, Any]:
        specs = [OutputSheet.model_validate(value) for value in checkpoint.get("output_spec", [])]
        rules = [ValidationRule.model_validate(value) for value in checkpoint.get("validation_rules", [])]
        checkpoint_recipe = Recipe(name="原流程校验", output_spec=specs, validation_rules=rules)
        inputs = {}
        for alias in checkpoint.get("input_aliases", []):
            if alias in self.input_frames:
                inputs[alias] = self.input_frames[alias]
            elif self.ws.exists(alias):
                inputs[alias] = self.ws.df(alias)
        outputs = {}
        for spec in specs:
            if self.ws.exists(spec.source):
                frame = self.ws.df(spec.source)
                if spec.columns:
                    frame = frame[[column for column in spec.columns if column in frame.columns]]
                outputs[spec.name] = frame
        validation = validate_run(checkpoint_recipe, inputs, outputs, history)
        if specs:
            return validation
        # 文件流程的完整性由沙箱与脚本 check 保证；无结果表时仅执行显式业务规则。
        checks = [check for check in validation["checks"] if check["rule_id"] != "auto_not_empty"]
        return self._validation_summary(checks)

    @staticmethod
    def _validation_summary(checks: list[dict[str, Any]]) -> dict[str, Any]:
        return summarize_checks(checks)

    def _record_checkpoint(self, checkpoint: dict[str, Any], validation: dict[str, Any]) -> bool:
        after_step = checkpoint["after_step"]
        self.report["validation_checkpoints"].append({"after_step": after_step, "report": validation})
        self.report["checkpoint_warnings"].extend(
            f"第 {after_step} 步后原流程校验提示：{item.get('message') or item['description']}"
            for item in validation["checks"] if item["status"] == "warn")
        if validation["status"] not in {"fail", "not_evaluated"}:
            return True
        failures = [item.get("message") or item["description"] for item in validation["checks"]
                    if item["status"] in {"fail", "not_evaluated"}]
        message = f"第 {after_step} 步后原流程校验未通过：" + "；".join(failures)
        self.report.update(status="failed", failed_stage="checkpoint", validation=validation,
                           files=[], error=message, escalation=message)
        return False

    def _check_assertions(self, step: RecipeStep, outcome: StepOutcome) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        if step.kind == "python" and outcome.notes.get("file_only"):
            if step.assertions:
                # 纯文件脚本没有表格输出，行列断言无从检查。以前这里直接报错交给修复器，但修复器只会改脚本，
                # 改不掉步骤上的断言：同一个错误重复 30 次（cv_7fdd9f0335，6 分钟）。这条断言本来就没有意义，
                # 而且是 AI 自己加的、不是用户配的：忽略它，不让它把成功的步骤变成警告，只在诊断信息里留个记录。
                outcome.notes["ignored_assertions"] = [a.type for a in step.assertions]
            return results
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
                ok = rate is not None and rate >= float(p.get("min", 0.9))
                msg = (f"匹配率 {rate:.1%} 低于 {float(p.get('min', 0.9)):.0%}"
                       if rate is not None else "流程没有产生可用的匹配率，校验未执行")
            elif a.type == "no_duplicate_keys":
                requested = list(p.get("columns", []))
                cols = [c for c in requested if c in outcome.columns_out]
                missing = [c for c in requested if c not in outcome.columns_out]
                if missing:
                    ok, msg = False, f"缺少列 {missing}，重复键校验未执行"
                elif cols:
                    key = ", ".join(quote_ident(c) for c in cols)
                    dups = self.ws.fetch(
                        f"SELECT count(*) FROM (SELECT {key} FROM {quote_ident(outcome.output)} GROUP BY {key} HAVING count(*) > 1)"
                    )[0][0]
                    ok, msg = dups == 0, f"{cols} 有 {dups} 个重复值"
                else:
                    ok, msg = False, "没有指定重复键列，校验未执行"
            elif a.type == "no_nulls":
                requested = list(p.get("columns", []))
                cols = [c for c in requested if c in outcome.columns_out]
                missing = [c for c in requested if c not in outcome.columns_out]
                if missing:
                    ok, msg = False, f"缺少列 {missing}，空值校验未执行"
                elif cols:
                    nulls = {c: self.ws.fetch(f"SELECT count(*) FROM {quote_ident(outcome.output)} WHERE {quote_ident(c)} IS NULL")[0][0] for c in cols}
                    bad = {c: n for c, n in nulls.items() if n}
                    ok, msg = not bad, f"有空值：{bad}"
                else:
                    ok, msg = False, "没有指定空值校验列，校验未执行"
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
            "kind": step.kind,
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
            **self._script_report(step),
        }

    def _record_failure(self, original: RecipeStep, current: RecipeStep, attempts: list[dict[str, Any]], started: float) -> None:
        last = attempts[-1]["error"] if attempts else "未知错误"
        self.report["steps"].append({
            "step_id": original.id, "name": original.name, "formula": current.formula,
            "kind": current.kind,
            "formula_name": self._formula_name(current.formula), "description": original.description,
            "status": "failed", "output": original.output, "error": last, "attempts": attempts,
            "duration_ms": int((time.perf_counter() - started) * 1000), "params": current.params,
            **self._script_report(current),
        })
        self.report["error"] = f"第 {original.id} 步「{original.name}」失败：{last}"
        self.report["escalation"] = escalation_report(original, attempts, self.max_repair, self.repairer is not None)

    def _replace_step(self, original: RecipeStep, fixed: RecipeStep) -> None:
        fixed.id, fixed.output, fixed.name = original.id, original.output, original.name
        fixed.assertions = fixed.assertions or original.assertions
        fixed.confidence = round(min(original.confidence, 0.95) * 0.85, 2)
        fixed.formula_kind = "builtin" if fixed.kind == "python" else self.library.resolve(fixed.formula).kind
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
            if self.recipe.metadata.get("explicit_outputs"):
                return outputs
            steps = self.recipe.ordered_steps()
            if steps:
                names = step_output_tables(steps[-1])
                for name in names:
                    if self.ws.exists(name):
                        outputs["结果" if len(names) == 1 else name] = self.ws.df(name)
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

        优先沿用原工作簿；格式不兼容时继续输出副本并记录提示。CSV 和没有原工作簿的数据使用新表。
        """
        sources = self.sources
        with temporary_file_copies() as files:
            try:
                self.sources = {}
                for alias, source in sources.items():
                    original = self._files.original_path(source.path) if self._files is not None else source.path
                    self._input_paths.add(original.resolve())
                    self.sources[alias] = replace(source, path=files.file_copy(original))
                return self._write_outputs(path, outputs)
            finally:
                self.sources = sources

    @staticmethod
    def _verify_written(path: Path, plans: list[Any], written: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """回读写出的文件并与计划比对。验收器自身出错只记为未核对的提示，不能因此让一次正确的输出失败。"""
        try:
            return verify_template_output(path, plans, written)
        except Exception as exc:
            return [{"rule_id": "verify_output", "rule_type": "output_verify", "description": "回读核对输出文件",
                     "status": "warn", "severity": "warning", "expected": None, "actual": None,
                     "message": f"没有完成回读核对：{exc}"}]

    def _check_output_path(self, path: Path) -> None:
        if path.resolve() in self._input_paths or (
            path.exists() and any(source.exists() and path.samefile(source) for source in self._input_paths)
        ):
            raise TemplatePreservationError("编辑结果必须另存副本，不能覆盖原文件")

    def _write_outputs(self, path: Path, outputs: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
        self._check_output_path(path)
        self.report["output_warnings"] = []
        if not outputs:
            raise ValueError("没有可以输出的结果：所有步骤都失败了，或 output_spec 指向的表不存在")
        if self.sources:
            output_tables = {spec.name: spec.source for spec in self.recipe.output_spec}
            if not self.recipe.output_spec and self.recipe.steps:
                names = step_output_tables(self.recipe.ordered_steps()[-1])
                output_tables = {"结果" if len(names) == 1 else name: name for name in names}
            lineages = {name: validated_row_lineage(self.ws.require(table)) for name, table in output_tables.items()
                        if name in outputs and self.ws.exists(table)}
            plans = plan_outputs(self.recipe, self.library, self.sources, outputs, row_lineages=lineages)
            base = next((plan.template for plan in plans if plan.template and plan.template.usable()), None)
            if base is not None:
                path = path.with_suffix(base.path.suffix.lower())
            self._check_output_path(path)
            written = write_like_original(path, plans)
            if written is not None:
                self.report["output_verification"] = self._verify_written(path, plans, written)
                self.report["output_warnings"] = list(dict.fromkeys(
                    notice for sheet in written for notice in sheet.get("warnings", [])))
                self.report["output_style"] = "best_effort" if self.report["output_warnings"] else "original"
                self.output_path = path
                return written
            if any(source.path.suffix.lower() in {".xlsx", ".xlsm", ".xls"} for source in self.sources.values()):
                raise TemplatePreservationError("无法读取结果对应的原工作簿副本，请重新载入原始文件后重试")
        self.report["output_style"] = "default"
        split = {o.name: o.split_by for o in self.recipe.output_spec}
        sheets = [SheetSpec(name=name, df=df, split_by=split.get(name)) for name, df in outputs.items()]
        written = write_excel(path, sheets)
        self.output_path = path
        return written

    # ------------------------------------------------------------------

    def _formula_name(self, formula_id: str) -> str:
        if formula_id == "python_script":
            return "Python 脚本"
        try:
            return self.library.resolve(formula_id).spec.name
        except ValueError:
            return formula_id

    def _recipe_brief(self) -> dict[str, Any]:
        public_fields = {"table", "lookup_table", "left", "right", "tables", "input_tables", "input_files",
                         "key", "lookup_key", "column", "columns", "target", "left_on", "right_on",
                         "group_by", "by", "carry_columns", "mapping", "types"}
        return {
            "id": self.recipe.id, "version": self.recipe.version, "name": self.recipe.name,
            "inputs": [{"alias": item.alias, "kind": item.kind, "source_file": item.source_file, "sheet": item.sheet}
                       for item in self.recipe.inputs],
            "steps": [
                {"id": s.id, "name": s.name, "kind": s.kind, "formula": s.formula, "formula_name": self._formula_name(s.formula),
                 "formula_kind": s.formula_kind, "description": s.description, "output": s.output,
                 "params": {key: value for key, value in s.params.items() if key in public_fields},
                 **({key: s.params[key] for key in ("script_id", "script_title", "script_filename") if key in s.params}
                    if s.kind == "python" else {})}
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
        parts.extend(f"输出文件「{item['name']}」" for item in r.get("files", []))
        if r["validation"]:
            parts.append(r["validation"]["summary"])
        return "，".join(parts)


def has_reviewable_output(report: dict[str, Any]) -> bool:
    """是否生成了可以下载的结果文件：全部步骤都执行完了（即使运行校验没通过，也要让用户能下载核对）。"""
    return report.get("status") in {"success", "warning"} or report.get("failed_stage") == "validation"


def validation_report_text(report: dict[str, Any]) -> str:
    """运行校验没通过时的人话报告：哪些检查没过 + 执行过程中可能的原因 + 建议。"""
    checks = [c for c in (report.get("validation") or {}).get("checks", []) if c["status"] in {"fail", "not_evaluated"}]
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
