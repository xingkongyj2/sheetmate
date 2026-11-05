"""Recipe 数据结构。

总体结构沿用 SheetAgent 的 Recipe（inputs / steps / output_spec / validation_rules / learned_from / metadata），
并加入 SheetMate 的逻辑：
- 公式、SQL 和沙箱 Python 都保存为可重复执行的步骤，可以混合编排
- formula_kind 标明这一步用的是内置公式还是客户独有公式
- 步骤可以挂断言（row_count_range / match_rate_min / no_duplicate_keys …），不通过就触发修复
- field_mapping 记录这个流程依赖的字段别名（标准字段 → 别名）
- 带 scope / customer_id / version，客户专属覆盖公共，版本只升不改
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from sheetmate.engine.formulas import FormulaLibrary
from sheetmate.engine.script_store import resolve_script_code, script_params
from sheetmate.engine.sqlutil import sql_table_refs


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def new_recipe_id() -> str:
    return f"rcp_{uuid.uuid4().hex[:10]}"


class RecipeInput(BaseModel):
    """运行时怎样识别输入（SheetAgent RecipeInput + 文件匹配规则 + 表头位置）。"""

    model_config = {"extra": "ignore"}

    alias: str  # 表名：步骤参数里引用它
    kind: Literal["table", "file"] = "table"
    description: str = ""
    file_pattern: str = ""  # 文件名规则，例如「运费明细*」，重复执行时自动匹配文件
    sheet: str | None = None
    source_file: str = ""  # 学习时用的文件名
    expected_columns: list[str] = Field(default_factory=list)
    required_columns: list[str] = Field(default_factory=list)
    key_columns: list[str] = Field(default_factory=list)  # 业务主键（运单号、客户编码…）
    column_matching: Literal["exact", "fuzzy"] = "fuzzy"
    expected_row_range: list[int] | None = None
    header: dict[str, Any] | None = None  # {"header_row": 2, "header_rows": 1}


class StepAssertion(BaseModel):
    """步骤断言：硬错误直接抛，软错误（匹配率低、行数异常、重复键）靠断言发现。"""

    model_config = {"extra": "ignore"}

    type: Literal[
        "not_empty", "row_count_range", "row_count_ratio", "match_rate_min", "no_duplicate_keys", "no_nulls", "columns_exist",
        "no_dropped_values",  # 长转宽时不允许出现没拆成列的新取值（金额会丢）
    ]
    params: dict[str, Any] = Field(default_factory=dict)
    severity: Literal["error", "warning"] = "error"
    description: str = ""


class RecipeStep(BaseModel):
    """Recipe 的一步：公式、SQL 或沙箱 Python；output 同时作为文件产物的逻辑命名空间。"""

    model_config = {"extra": "ignore"}

    id: int
    name: str
    kind: Literal["formula", "sql", "python"] = "formula"
    action: str = "custom"  # filter / merge / pivot / aggregate / calculate / clean / output / custom
    formula: str
    formula_kind: Literal["builtin", "customer"] = "builtin"
    params: dict[str, Any] = Field(default_factory=dict)
    output: str
    description: str = ""  # 人话说明（流程图上展示）
    explanation: str = ""  # 推断依据：diff 证据 / 处理说明（SheetAgent）
    confidence: float = 0.8
    sql: str = ""  # 最近一次执行时编译出的 SQL，仅供审计
    assertions: list[StepAssertion] = Field(default_factory=list)
    enabled: bool = True

    @model_validator(mode="before")
    @classmethod
    def _infer_kind(cls, value: Any) -> Any:
        if isinstance(value, dict):
            value = dict(value)
            if "kind" not in value:
                value["kind"] = {"custom_sql": "sql", "python_script": "python"}.get(value.get("formula"), "formula")
            params = value.get("params")
            if value["kind"] == "python" and isinstance(params, dict):
                code = params.get("code")
                # 旧流程和模型新生成的代码只物化一次；无效代码仍交给 check_recipe 汇总错误。
                if isinstance(code, str) and code.strip() and len(code.encode("utf-8")) <= 100_000:
                    value["params"] = script_params(params, title=str(value.get("name") or "Python 脚本")[:20])
        return value

    @field_validator("output")
    @classmethod
    def _clean_output(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("步骤必须有 output（结果表名）")
        return text[:60]


class OutputSheet(BaseModel):
    """哪个结果表写成输出文件里的哪个 sheet。"""

    model_config = {"extra": "ignore"}

    name: str
    source: str
    description: str = ""
    columns: list[str] | None = None  # 可选：只输出这些列（按顺序）
    split_by: str | None = None  # 可选：按这一列的取值拆成多个 sheet


class ValidationRule(BaseModel):
    """运行后的校验规则（SheetAgent ValidationRule）。"""

    model_config = {"extra": "ignore"}

    id: str = Field(default_factory=lambda: f"v_{uuid.uuid4().hex[:8]}")
    type: str
    description: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    severity: Literal["error", "warning", "info"] = "warning"
    requires_history: bool = False


class Recipe(BaseModel):
    model_config = {"extra": "ignore"}

    id: str = Field(default_factory=new_recipe_id)
    version: int = 1
    name: str
    description: str = ""
    scope: Literal["customer", "global"] = "customer"
    customer_id: str | None = None
    created_by: Literal["ai", "human"] = "ai"
    learned_from: dict[str, Any] = Field(default_factory=dict)
    inputs: list[RecipeInput] = Field(default_factory=list)
    field_mapping: dict[str, list[str]] = Field(default_factory=dict)
    steps: list[RecipeStep] = Field(default_factory=list)
    output_spec: list[OutputSheet] = Field(default_factory=list)
    validation_rules: list[ValidationRule] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)

    # ------------------------------------------------------------------

    def ordered_steps(self) -> list[RecipeStep]:
        return sorted((s for s in self.steps if s.enabled), key=lambda s: s.id)

    def step(self, step_id: int) -> RecipeStep:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise KeyError(f"Recipe 没有第 {step_id} 步")

    def input_aliases(self) -> list[str]:
        return [i.alias for i in self.inputs]

    def final_outputs(self) -> list[str]:
        if self.output_spec:
            return [o.source for o in self.output_spec]
        steps = self.ordered_steps()
        return step_output_tables(steps[-1]) if steps else []

    def to_dict(self) -> dict[str, Any]:
        # model_copy(update=...) 和断点重跑允许直接改参数，保存前同样转换为不可变脚本引用。
        for step in self.steps:
            if step.kind == "python":
                step.params = script_params(step.params, title=step.name[:20])
        return self.model_dump(mode="json")

    def flow(self) -> dict[str, Any]:
        """公开流程图数据，同时保留表依赖和文件交付边界。"""
        input_files: dict[tuple[str, str], dict[str, Any]] = {}
        for inp in self.inputs:
            # 一个工作簿的多个 sheet 仍是同一个文件；没有来源文件时不猜合并。
            key = ("file", inp.source_file) if inp.source_file else ("alias", inp.alias)
            if key not in input_files:
                input_files[key] = {"name": inp.source_file or inp.file_pattern or inp.alias,
                                    "aliases": [], "file_pattern": inp.file_pattern}
            input_files[key]["aliases"].append(inp.alias)
        return {
            "inputs": [{"alias": i.alias, "kind": i.kind, "description": i.description, "file_pattern": i.file_pattern,
                        "source_file": i.source_file, "sheet": i.sheet} for i in self.inputs],
            "input_files": list(input_files.values()),
            "steps": [
                {
                    "id": s.id,
                    "name": s.name,
                    "kind": s.kind,
                    "action": s.action,
                    "formula": s.formula,
                    "formula_kind": s.formula_kind,
                    "description": s.description,
                    "output": s.output,
                    "inputs": list(dict.fromkeys(step_table_refs(s) + step_file_refs(s))),
                    **({key: s.params[key] for key in ("script_id", "script_title", "script_filename") if key in s.params}
                       if s.kind == "python" else {}),
                }
                for s in self.ordered_steps()
            ],
            "outputs": [{"name": o.name, "source": o.source} for o in self.output_spec],
            "output_files": self._flow_output_files(),
        }

    def _flow_output_files(self) -> list[dict[str, Any]]:
        steps = self.ordered_steps()
        consumed = {ref for step in steps for ref in step_file_refs(step)}
        for index, step in enumerate(steps):
            downstream = {ref for later in steps[index + 1:] for ref in step_table_refs(later)}
            consumed.update(spec["ref"] for table, spec in step.params.get("file_tables", {}).items()
                            if table in downstream and isinstance(spec, dict) and isinstance(spec.get("ref"), str))
        files: dict[str, dict[str, Any]] = {}
        for index, step in enumerate(steps):
            if step.kind != "python":
                continue
            export = step.params.get("workbook_export")
            if export is not None and index < len(steps) - 1:
                continue  # 与执行器一致：中途导出的工作簿供后续处理使用。
            declarations = step.params.get("output_files")
            if declarations is None:
                # 旧快照只记了文件导入的表；无表的交付文件从新版声明读取。
                refs = [spec["ref"] for spec in step.params.get("file_tables", {}).values()
                        if isinstance(spec, dict) and isinstance(spec.get("ref"), str)]
                declarations = [{"name": ref.split("/", 1)[1], "ref": ref} for ref in dict.fromkeys(refs)
                                if isinstance(ref, str) and ref.startswith(f"{step.output}/")]
                if isinstance(export, dict) and export.get("filename"):
                    name = export["filename"]
                    declarations.append({"name": name, "ref": f"{step.output}/{name}"})
                if not declarations and index == len(steps) - 1 and not self.output_spec and not step_output_tables(step):
                    declarations = [{"name": "输出文件（名称待确认）", "ref": ""}]
            for item in declarations if isinstance(declarations, list) else []:
                if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"]:
                    continue
                ref = item.get("ref", "")
                if not isinstance(ref, str) or ref in consumed:
                    continue
                files[ref or f"unknown:{step.id}"] = {"name": item["name"], "ref": ref,
                                                    "source": step.output, "sources": [step.output]}
        table_sources = list(dict.fromkeys(o.source for o in self.output_spec))
        if not table_sources and steps and not self.metadata.get("explicit_outputs"):
            table_sources = step_output_tables(steps[-1])
        if table_sources:
            # 普通表格流程由 host/replay 统一导出为「流程名_结果.xlsx」。
            filename = re.sub(r"[^\w一-鿿.\-()（） ]+", "_", Path(f"{self.name}_结果.xlsx").name).strip(" .")[-120:]
            files["workbook"] = {"name": filename, "ref": "", "source": table_sources[0], "sources": table_sources}
        return list(files.values())


# ---------------------------------------------------------------------------
# 静态检查（执行前）：公式存在、表引用顺序正确、输出不重名
# ---------------------------------------------------------------------------

_TABLE_PARAM_TYPES = {"table", "tables"}


def step_table_refs(step: RecipeStep, library: FormulaLibrary | None = None) -> list[str]:
    """这一步读了哪些表。"""
    if step.kind == "python":
        values = step.params.get("input_tables", [])
        refs = [str(value) for value in values] if isinstance(values, list) else []
        bindings = step.params.get("table_bindings", {})
        if isinstance(bindings, dict):
            refs.extend(str(value) for value in bindings.values())
        export = step.params.get("workbook_export", {})
        if isinstance(export, dict):
            for spec in export.get("output_spec", []):
                if isinstance(spec, dict) and isinstance(spec.get("source"), str):
                    refs.append(spec["source"])
        return list(dict.fromkeys(refs))
    lib = library or FormulaLibrary()
    refs: list[str] = []
    try:
        spec = lib.resolve(step.formula).spec
    except ValueError:
        spec = None
    if spec is not None:
        for name, p in spec.params.items():
            value = step.params.get(name)
            if value is None or p.type not in _TABLE_PARAM_TYPES:
                continue
            for v in value if isinstance(value, list) else [value]:
                if str(v) not in refs:
                    refs.append(str(v))
    if step.formula == "custom_sql" and not refs:
        refs = sql_table_refs(str(step.params.get("sql", "")))
    return refs


def step_file_refs(step: RecipeStep) -> list[str]:
    """文件输入引用输入别名，或前序脚本的 output/文件名。"""
    values = step.params.get("input_files", {}) if step.kind == "python" else {}
    return list(dict.fromkeys(str(value) for value in values.values())) if isinstance(values, dict) else []


def step_output_tables(step: RecipeStep) -> list[str]:
    if step.kind != "python":
        return [step.output]
    mapping = step.params.get("output_tables")
    outputs = [step.output] if mapping is None else list(mapping.values()) if isinstance(mapping, dict) else []
    file_tables = step.params.get("file_tables", {})
    if isinstance(file_tables, dict):
        outputs.extend(file_tables)
    return outputs


def check_recipe(recipe: Recipe, library: FormulaLibrary) -> list[str]:
    """返回问题列表（空列表表示通过）。只检查结构，列是否存在要到执行时由引擎检查。"""
    problems: list[str] = []
    aliases = recipe.input_aliases()
    known = {inp.alias for inp in recipe.inputs if inp.kind == "table"}
    file_inputs = set(aliases)
    file_outputs: set[str] = set()
    if len(set(aliases)) != len(recipe.inputs):
        problems.append("输入别名有重复")
    if not recipe.steps:
        problems.append("Recipe 至少需要一个步骤")
    seen_ids: set[int] = set()
    for step in recipe.ordered_steps():
        label = f"第 {step.id} 步「{step.name}」"
        if step.id in seen_ids:
            problems.append(f"{label}：步骤 id 重复")
        seen_ids.add(step.id)
        if step.kind == "python":
            if step.formula != "python_script":
                problems.append(f"{label}：Python 步骤的 formula 必须为 python_script")
            try:
                code = resolve_script_code(step.params)
            except (OSError, ValueError) as exc:
                code = None
                problems.append(f"{label}：无法读取 Python 脚本：{exc}")
            if not isinstance(code, str) or not code.strip() or len(code.encode("utf-8")) > 100_000:
                problems.append(f"{label}：Python 脚本不能为空，且不能超过 100 KB")
            else:
                try:
                    compile(code, "<recipe_script>", "exec")
                except SyntaxError as exc:
                    problems.append(f"{label}：Python 语法错误（第 {exc.lineno} 行）：{exc.msg}")
            tables = step.params.get("input_tables", [])
            if not isinstance(tables, list) or any(not isinstance(t, str) or not t.strip() for t in tables):
                problems.append(f"{label}：input_tables 必须是表名列表")
            files = step.params.get("input_files", {})
            if not isinstance(files, dict) or any(not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip()
                                                  for k, v in files.items()):
                problems.append(f"{label}：input_files 必须映射文件别名到输入或前序脚本文件")
            for ref in step_file_refs(step):
                if ref not in file_inputs and not any(ref.startswith(f"{namespace}/") and len(ref) > len(namespace) + 1
                                                     for namespace in file_outputs):
                    problems.append(f"{label}：引用的文件「{ref}」不存在（只能用输入文件或前面脚本的输出）")
            mapping = step.params.get("output_tables", {})
            if not isinstance(mapping, dict) or any(not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip()
                                                    for k, v in mapping.items()):
                problems.append(f"{label}：output_tables 必须映射脚本结果表名到流程表名")
            elif len(set(mapping.values())) != len(mapping):
                problems.append(f"{label}：Python 输出表名有重复")
            declared_files = step.params.get("output_files")
            if declared_files is not None and (
                not isinstance(declared_files, list) or any(
                    not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"]
                    or "/" in item["name"] or "\\" in item["name"] or item["name"] in {".", ".."}
                    or not isinstance(item.get("ref"), str) or not item["ref"].startswith(f"{step.output}/")
                    or not item["ref"][len(step.output) + 1:]
                    or "/" in item["ref"][len(step.output) + 1:] or "\\" in item["ref"][len(step.output) + 1:]
                    for item in declared_files
                )
            ):
                problems.append(f"{label}：output_files 必须声明本步骤输出文件的 name 和 ref")
            bindings = step.params.get("table_bindings", {})
            if not isinstance(bindings, dict) or any(not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip()
                                                    for k, v in bindings.items()):
                problems.append(f"{label}：table_bindings 必须映射脚本表名到流程表名")
            file_tables = step.params.get("file_tables", {})
            if not isinstance(file_tables, dict) or any(not isinstance(k, str) or not k.strip() or not isinstance(v, dict)
                                                       or not isinstance(v.get("ref"), str) or not v["ref"].strip()
                                                       for k, v in file_tables.items()):
                problems.append(f"{label}：file_tables 必须映射流程表名到文件来源")
            elif isinstance(mapping, dict) and set(file_tables) & {v for v in mapping.values() if isinstance(v, str)}:
                problems.append(f"{label}：文件读取表和脚本结果表重名")
            else:
                for spec in file_tables.values() if isinstance(file_tables, dict) else []:
                    ref = spec["ref"]
                    if ref not in file_inputs and not any(ref.startswith(f"{namespace}/") for namespace in file_outputs | {step.output}):
                        problems.append(f"{label}：文件读取表的来源「{ref}」不存在")
            export = step.params.get("workbook_export")
            if export is not None:
                if not isinstance(export, dict) or not isinstance(export.get("output_spec"), list) or not export["output_spec"]:
                    problems.append(f"{label}：workbook_export 必须指定输出工作表")
                else:
                    try:
                        for output in export["output_spec"]:
                            OutputSheet.model_validate(output)
                    except ValueError:
                        problems.append(f"{label}：workbook_export 的工作表定义无效")
                filename = export.get("filename", "") if isinstance(export, dict) else ""
                if not isinstance(filename, str) or not filename or "/" in filename or "\\" in filename or filename in {".", ".."}:
                    problems.append(f"{label}：workbook_export.filename 必须是文件名")
        else:
            if step.kind == "sql" and step.formula != "custom_sql":
                problems.append(f"{label}：SQL 步骤的 formula 必须为 custom_sql")
            if not library.has(step.formula):
                problems.append(f"{label}：公式「{step.formula}」不存在")
                continue
            spec = library.resolve(step.formula).spec
            unknown = set(step.params) - set(spec.params)
            if unknown:
                problems.append(f"{label}：公式「{step.formula}」没有参数 {sorted(unknown)}")
            missing = [n for n, p in spec.params.items() if p.required and step.params.get(n) in (None, "", [], {})]
            if missing:
                problems.append(f"{label}：缺少必填参数 {missing}")
        for ref in step_table_refs(step, library):
            if ref not in known:
                problems.append(f"{label}：引用的表「{ref}」不存在（只能用输入表或前面步骤的输出）")
        for output in step_output_tables(step):
            if not isinstance(output, str):
                continue
            if output in aliases:
                problems.append(f"{label}：输出名「{output}」和输入表重名")
            if output in known and output not in aliases:
                problems.append(f"{label}：输出名「{output}」和前面步骤重名")
            known.add(output)
        if step.kind == "python":
            if step.output in file_outputs or step.output in file_inputs:
                problems.append(f"{label}：文件输出命名空间「{step.output}」重复")
            file_outputs.add(step.output)
    for out in recipe.output_spec:
        if out.source not in known:
            problems.append(f"输出 sheet「{out.name}」的来源「{out.source}」不存在")
    return problems


def file_pattern_from_name(filename: str) -> str:
    """从文件名推出匹配规则：去掉扩展名，把日期/月份/数字部分换成 *。例：运费明细_2026-09.xlsx → 运费明细*"""
    stem = re.sub(r"\.(xlsx|xlsm|xls|csv)$", "", filename, flags=re.IGNORECASE)
    stem = re.sub(r"（[1-9]\d*）$", "", stem)
    stem = re.sub(r"[\s_\-（(]*\d[\d\s_\-.年月日）)]*$", "", stem)
    stem = re.sub(r"\d+", "*", stem)
    stem = stem.strip(" _-")
    return f"{stem}*" if stem else "*"
