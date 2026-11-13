"""从真实执行记录编译可复用流程；不调用模型，也不重新执行处理。"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import duckdb

from sheetmate.engine.formulas import FormulaLibrary
from sheetmate.engine.scripts.store import resolve_script_code, script_params
from sheetmate.engine.tables.sql import sanitize_table_name, sql_base_table_nodes
from sheetmate.engine.tables.workspace import Workspace
from sheetmate.recipe.learning.learner import InputMeta, LearnContext, build_inputs
from sheetmate.recipe.definition.schema import (
    REMOVED_OUTPUT_REFS,
    OutputSheet,
    Recipe,
    RecipeInput,
    RecipeStep,
    check_recipe,
    file_pattern_from_name,
    step_table_refs,
)


@dataclass
class FileDependency:
    file_id: str
    filename: str
    recipe: Recipe | None = None
    ref: str | None = None
    replaceable_source: bool = False


FileResolver = Callable[[str], FileDependency]


def _remap_sql(sql: str, names: dict[str, str]) -> str:
    """只改 AST 的表引用，保留列名、字符串和别名。"""
    with duckdb.connect(":memory:") as conn:
        conn.execute("SET enable_external_access = false")
        ast = json.loads(conn.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()[0])
        if ast.get("error"):
            raise ValueError("无法解析已执行 SQL 的依赖")
        for node in sql_base_table_nodes(ast):
            old = node["table_name"]
            if old not in names:
                continue
            node["table_name"] = names[old]
            qualified = node.get("qualified_name") or {}
            if qualified.get("path"):
                qualified["path"][-1] = names[old]
            if not node.get("alias"):
                node["alias"] = old
        return conn.execute("SELECT json_deserialize_sql(?)", [json.dumps(ast)]).fetchone()[0]


def _ref(value: str, names: dict[str, str]) -> str:
    prefix, slash, tail = value.partition("/")
    return names.get(prefix, prefix) + slash + tail


def _params(step: RecipeStep, names: dict[str, str], library: FormulaLibrary) -> dict[str, Any]:
    params = json.loads(json.dumps(step.params, ensure_ascii=False))
    if step.formula == "python_script":
        bindings = params.get("table_bindings") or {name: name for name in params.get("input_tables") or []}
        params["table_bindings"] = {key: names.get(value, value) for key, value in bindings.items()}
        params["input_tables"] = list(dict.fromkeys(params["table_bindings"].values()))
        params["input_files"] = {key: _ref(value, names) for key, value in (params.get("input_files") or {}).items()}
        if "output_tables" in params:
            params["output_tables"] = {key: names.get(value, value) for key, value in (params.get("output_tables") or {}).items()}
        params["file_tables"] = {names.get(key, key): {**value, "ref": _ref(value["ref"], names)}
                                 for key, value in (params.get("file_tables") or {}).items()}
        if "output_files" in params:
            params["output_files"] = [{**value, "ref": _ref(value["ref"], names)} for value in params["output_files"]]
        if params.get("workbook_export"):
            for spec in params["workbook_export"].get("output_spec", []):
                spec["source"] = names.get(spec["source"], spec["source"])
    elif step.formula == "custom_sql":
        if any(old != new for old, new in names.items()):
            params["sql"] = _remap_sql(str(params.get("sql") or ""), names)
    elif library.has(step.formula):
        for key, spec in library.resolve(step.formula).spec.params.items():
            if key in params and spec.type == "table":
                params[key] = names.get(params[key], params[key])
            elif key in params and spec.type == "tables":
                params[key] = [names.get(value, value) for value in params[key]]
    return params


# ---------------------------------------------------------------------------
# 表名：编译时用带哈希的内部唯一名防止上游流程的表互相重名，最后再换成干净的名字
# ---------------------------------------------------------------------------

_DEPENDENCY_NAME = re.compile(r"^依赖_[0-9a-f]{10}_(?P<base>.+?)(?:_\d+)?$")


def _table_aliases(recipe: Recipe) -> set[str]:
    aliases = {item.alias for item in recipe.inputs}
    for step in recipe.steps:
        aliases.add(step.output)
        aliases.update((step.params.get("output_tables") or {}).values())
        aliases.update((step.params.get("file_tables") or {}).keys())
    return {alias for alias in aliases if isinstance(alias, str)}


def dependency_name_map(recipe: Recipe) -> dict[str, str]:
    """{带「依赖_哈希_」前缀的内部名: 干净的名字}。只有和别的表重名时才加数字区分，同名按原名排序保证稳定。"""
    aliases = _table_aliases(recipe)
    prefixed = sorted(alias for alias in aliases if _DEPENDENCY_NAME.match(alias))
    taken = {alias for alias in aliases if alias not in prefixed}
    names: dict[str, str] = {}
    for old in prefixed:
        base = _DEPENDENCY_NAME.match(old).group("base")  # type: ignore[union-attr]
        name, count = base, 1
        while name in taken:
            count += 1
            name = f"{base}_{count}"
        names[old] = name
        taken.add(name)
    return names


def _remap_value(value: Any, names: dict[str, str]) -> Any:
    """校验规则、断言、检查点里引用的表名（可能是「表名」或「表名/文件名」），整体替换。"""
    if isinstance(value, str):
        if value in names:
            return names[value]
        head, slash, tail = value.partition("/")
        return names[head] + slash + tail if slash and head in names else value
    if isinstance(value, list):
        return [_remap_value(item, names) for item in value]
    if isinstance(value, dict):
        return {key: _remap_value(item, names) for key, item in value.items()}
    return value


def _rename_sql(sql: str, names: dict[str, str]) -> str:
    """按完整标识符替换 SQL 里的表名（带引号或不带引号都行，也覆盖 t.\"列\" 这类限定写法）。"""
    for old in sorted(names, key=len, reverse=True):
        sql = re.sub(r"(?<!\w)" + re.escape(old) + r"(?!\w)", lambda _match, new=names[old]: new, sql)
    return sql


def clean_dependency_names(recipe: Recipe, library: FormulaLibrary) -> Recipe:
    """把「依赖_哈希_名字_序号」换回干净的名字，并同步所有引用。

    改不干净（脚本里直接写了内部名、改名后引用悬空等）时原样返回：名字难看但流程照常能跑。"""
    names = dependency_name_map(recipe)
    if not names:
        return recipe
    try:
        pattern = re.compile("|".join(re.escape(old) for old in sorted(names, key=len, reverse=True)))
        for step in recipe.steps:
            if step.kind == "python" and step.formula == "python_script":
                if pattern.search(resolve_script_code(step.params)):
                    return recipe  # 脚本直接引用了内部名，改名会让脚本读不到表
        cleaned = recipe.model_copy(deep=True)
        for item in cleaned.inputs:
            item.alias = names.get(item.alias, item.alias)
        bindings = cleaned.learned_from.get("input_bindings")
        if isinstance(bindings, dict):
            cleaned.learned_from["input_bindings"] = {names.get(key, key): value for key, value in bindings.items()}
        for step in cleaned.steps:
            step.output = names.get(step.output, step.output)
            if step.formula == "custom_sql":
                step.params = {**step.params, "sql": _rename_sql(str(step.params.get("sql") or ""), names)}
            else:
                step.params = _params(step, names, library)
            if step.sql:
                step.sql = _rename_sql(step.sql, names)
            for assertion in step.assertions:
                assertion.params = _remap_value(assertion.params, names)
        for spec in cleaned.output_spec:
            spec.source = names.get(spec.source, spec.source)
        for rule in cleaned.validation_rules:
            rule.params = _remap_value(rule.params, names)
        for key in ("validation_checkpoints", REMOVED_OUTPUT_REFS):
            if key in cleaned.metadata:
                cleaned.metadata[key] = _remap_value(cleaned.metadata[key], names)
        if len(check_recipe(cleaned, library)) > len(check_recipe(recipe, library)):
            return recipe
        return cleaned
    except Exception:
        return recipe


class _Compiler:
    def __init__(self, ws: Workspace, library: FormulaLibrary, *, file_resolver: FileResolver | None = None):
        self.ws, self.library, self.file_resolver = ws, library, file_resolver
        self.steps: list[RecipeStep] = []
        self.inputs: dict[str, RecipeInput] = {}
        self.bindings: dict[str, dict[str, Any]] = {}
        self.table_refs: dict[str, str] = {}
        self.file_refs: dict[str, str] = {}
        self.snapshots: dict[str, dict[str, str]] = {}
        self.snapshot_steps: dict[str, set[tuple[int, str]]] = {}
        self.checkpoints: list[dict[str, Any]] = []
        self.checkpoint_keys: set[tuple[str, str]] = set()
        self.step_ids: dict[tuple[str, int], int] = {}
        self.visiting: set[str] = set()

    def append(self, step: RecipeStep) -> None:
        step.id = len(self.steps) + 1
        self.steps.append(step)

    def snapshot(self, recipe: Recipe, key: str) -> dict[str, str]:
        prefix = "依赖_" + hashlib.sha256(key.encode()).hexdigest()[:10] + "_"
        aliases = {i.alias for i in recipe.inputs}
        for step in recipe.ordered_steps():
            aliases.add(step.output)
            aliases.update((step.params.get("output_tables") or {}).values())
            aliases.update((step.params.get("file_tables") or {}).keys())
        names = self.snapshots.setdefault(key, {})
        for old in sorted(aliases):
            if old not in names:
                names[old] = sanitize_table_name(prefix + old)[:52] + f"_{len(names)}"
        self.snapshots[key] = names
        old_bindings = recipe.learned_from.get("input_bindings") or {}
        for inp in recipe.inputs:
            binding = old_bindings.get(inp.alias) or {}
            existing = next((alias for alias, value in self.bindings.items()
                             if binding.get("file_id") and value.get("file_id") == binding["file_id"]
                             and value.get("sheet") == binding.get("sheet") and self.inputs[alias].kind == inp.kind), None)
            if existing:
                names[inp.alias] = existing
                continue
            cloned = inp.model_copy(deep=True)
            cloned.alias = names[inp.alias]
            self.inputs[cloned.alias] = cloned
            if inp.alias in old_bindings:
                self.bindings[cloned.alias] = dict(old_bindings[inp.alias])
        for step in recipe.ordered_steps():
            signature = (step.id, step.output)
            seen = self.snapshot_steps.setdefault(key, set())
            if signature in seen:
                existing = self.steps[self.step_ids[(key, step.id)] - 1]
                if step.formula == "python_script" and step.params.get("file_tables"):
                    # 同一文件后来重新加载时可能得到新的表名，保留两种读表绑定。
                    mapped = _params(step, names, self.library)
                    existing.params["file_tables"] = {**existing.params.get("file_tables", {}), **mapped["file_tables"]}
                continue
            seen.add(signature)
            cloned = step.model_copy(deep=True)
            cloned.output = names[step.output]
            cloned.params = _params(step, names, self.library)
            self.append(cloned)
            self.step_ids[(key, step.id)] = cloned.id
        for checkpoint in recipe.metadata.get("validation_checkpoints", []):
            signature = (key, json.dumps(checkpoint, sort_keys=True, ensure_ascii=False))
            if signature in self.checkpoint_keys:
                continue
            self.checkpoint_keys.add(signature)
            copied = json.loads(json.dumps(checkpoint, ensure_ascii=False))
            copied["after_step"] = self.step_ids[(key, int(copied["after_step"]))]
            self.checkpoints.append(self._checkpoint(copied, names))
        if recipe.validation_rules:
            signature = (key, "final_validation")
            if signature not in self.checkpoint_keys:
                self.checkpoint_keys.add(signature)
                self.checkpoints.append(self._checkpoint({
                    "after_step": self.step_ids[(key, max(s.id for s in recipe.ordered_steps()))],
                    "input_aliases": [i.alias for i in recipe.inputs if i.kind == "table"],
                    "output_spec": [o.model_dump() for o in recipe.output_spec],
                    "validation_rules": [r.model_dump() for r in recipe.validation_rules],
                }, names))
        return names

    @staticmethod
    def _checkpoint(checkpoint: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
        inputs = set(checkpoint.get("input_aliases", []))
        checkpoint["input_aliases"] = [names.get(alias, alias) for alias in checkpoint.get("input_aliases", [])]
        for spec in checkpoint.get("output_spec", []):
            spec["source"] = names.get(spec["source"], spec["source"])
        for rule in checkpoint.get("validation_rules", []):
            params = rule.get("params") or {}
            for param in ("input", "input_table", "source_table", "vs", "table"):
                if isinstance(params.get(param), str) and params[param] in inputs:
                    params[param] = names.get(params[param], params[param])
        return checkpoint

    def table(self, table: str) -> str:
        if table in self.table_refs:
            return self.table_refs[table]
        marker = "table:" + table
        if marker in self.visiting:
            raise ValueError(f"表「{table}」存在循环依赖")
        self.visiting.add(marker)
        info = self.ws.require(table)
        provenance = info.step or {}
        recipe = Recipe.model_validate(provenance["recipe_snapshot"]) if provenance.get("recipe_snapshot") else None
        replaceable = bool(recipe is not None and not recipe.inputs and info.file_id and self.file_resolver
                           and self.file_resolver(info.file_id).replaceable_source)
        if recipe is not None and not replaceable:
            names = self.snapshot(recipe, str(provenance.get("run_id") or recipe.id))
            result = names[str(provenance["table_ref"])]
        elif replaceable or (info.source == "uploaded" and info.role == "input"):
            meta = InputMeta(filename=info.filename or info.label, sheet=info.sheet, header=info.header, file_id=info.file_id)
            ctx = LearnContext(requirement="", library=self.library, input_frames={table: self.ws.df(table)}, input_meta={table: meta})
            self.inputs[table] = build_inputs(ctx, [], {}, None)[0]
            self.bindings[table] = {"file_id": info.file_id, "sheet": info.sheet}
            result = table
        elif provenance.get("formula"):
            probe = RecipeStep(id=1, name=table[:40], formula=provenance["formula"], params=provenance.get("params", {}), output=table)
            names = {ref: self.table(ref) for ref in step_table_refs(probe, self.library)}
            resolved = self.library.resolve(probe.formula)
            probe.params = _params(probe, names, self.library)
            probe.action, probe.formula_kind = resolved.spec.category, resolved.kind
            probe.name, probe.description = (info.label or table)[:40], info.description or resolved.describe(probe.params)
            probe.explanation, probe.confidence = "由实际执行记录保存", 0.9
            self.append(probe)
            result = table
        else:
            raise ValueError(f"表「{table}」缺少可复用的处理记录，请先执行有明确输入和输出的工具步骤")
        self.visiting.remove(marker)
        self.table_refs[table] = result
        return result

    def file(self, file_id: str) -> str:
        if file_id in self.file_refs:
            return self.file_refs[file_id]
        if self.file_resolver is None:
            raise ValueError("缺少文件依赖解析器")
        dependency = self.file_resolver(file_id)
        if dependency.recipe is not None and dependency.ref:
            key = str((dependency.recipe.metadata.get("last_run") or {}).get("run_id") or dependency.recipe.id)
            result = _ref(dependency.ref, self.snapshot(dependency.recipe, key))
        else:
            result = "文件_" + file_id
            self.inputs[result] = RecipeInput(alias=result, kind="file", description=f"来自「{dependency.filename}」",
                                              file_pattern=file_pattern_from_name(dependency.filename), source_file=dependency.filename)
            self.bindings[result] = {"file_id": file_id}
        self.file_refs[file_id] = result
        return result

    def recipe(self, *, name: str, description: str, customer_id: str | None, conversation_id: str | None,
               field_mapping: dict[str, list[str]] | None, outputs: list[OutputSheet]) -> Recipe:
        direct = {alias: self.ws.df(alias) for alias in self.inputs
                  if self.ws.exists(alias) and self.ws.require(alias).source == "uploaded" and self.inputs[alias].kind == "table"}
        if direct:
            meta = {alias: InputMeta(filename=self.inputs[alias].source_file, sheet=self.inputs[alias].sheet,
                                     header=self.inputs[alias].header) for alias in direct}
            ctx = LearnContext(requirement=description, library=self.library, input_frames=direct, input_meta=meta)
            self.inputs.update({inp.alias: inp for inp in build_inputs(ctx, self.steps, {}, None)})
        recipe = Recipe(name=name[:80], description=description, customer_id=customer_id, created_by="ai",
                        inputs=list(self.inputs.values()), steps=self.steps, output_spec=outputs,
                        field_mapping=dict(field_mapping or {}),
                        learned_from={"conversation_id": conversation_id, "from": "execution",
                                      "input_files": list(dict.fromkeys(i.source_file for i in self.inputs.values() if i.source_file)),
                                      "input_bindings": self.bindings},
                        metadata={"requirement": description, "confidence_overall": 0.9, "explicit_outputs": True,
                                  "validation_checkpoints": self.checkpoints})
        return clean_dependency_names(recipe, self.library)


def compile_from_workspace(ws: Workspace, targets: list[str], library: FormulaLibrary, *, name: str,
                           description: str = "", customer_id: str | None = None, conversation_id: str | None = None,
                           field_mapping: dict[str, list[str]] | None = None,
                           file_resolver: FileResolver | None = None) -> Recipe:
    if not targets:
        raise ValueError("请指定要输出的结果表")
    compiler = _Compiler(ws, library, file_resolver=file_resolver)
    outputs = [OutputSheet(name=(ws.require(table).label or table)[:31], source=compiler.table(table)) for table in targets]
    return compiler.recipe(name=name, description=description, customer_id=customer_id,
                           conversation_id=conversation_id, field_mapping=field_mapping, outputs=outputs)


def compile_script(ws: Workspace, library: FormulaLibrary, *, run_id: str, code: str, description: str,
                   input_tables: list[str], input_files: dict[str, str], output_tables: dict[str, str],
                   file_tables: dict[str, dict[str, Any]], file_resolver: FileResolver,
                   display_tables: list[str] | None = None,
                   output_files: list[dict[str, str]] | None = None,
                   customer_id: str | None = None, conversation_id: str | None = None,
                   field_mapping: dict[str, list[str]] | None = None,
                   script_id: str | None = None, script_title: str = "") -> Recipe:
    compiler = _Compiler(ws, library, file_resolver=file_resolver)
    bindings = {table: compiler.table(table) for table in input_tables}
    files = {alias: compiler.file(file_id) for alias, file_id in input_files.items()}
    output = sanitize_table_name("脚本_" + run_id)
    params = {"code": code, "input_tables": list(dict.fromkeys(bindings.values())),
              "table_bindings": bindings, "input_files": files, "output_tables": output_tables,
              "file_tables": file_tables}
    if script_id:
        params["script_id"] = script_id
    if script_title:
        params["script_title"] = script_title
    params = script_params(params, title=(script_title or description or "Python 处理")[:20])
    if output_files is not None:
        params["output_files"] = [{"name": item["name"], "ref": item["ref"]} for item in output_files]
    compiler.append(RecipeStep(id=1, name=description[:40] or "Python 处理", kind="python", formula="python_script",
                              output=output, description=description, explanation="保存实际执行的代码、输入依赖与校验",
                              params=params))
    outputs = [OutputSheet(name=str(label)[:31], source=table) for label, table in output_tables.items()
               if display_tables is None or label in display_tables]
    recipe = compiler.recipe(name=description or "脚本流程", description=description, customer_id=customer_id,
                             conversation_id=conversation_id, field_mapping=field_mapping, outputs=outputs)
    recipe.metadata["last_run"] = {"run_id": run_id, "status": "success"}
    return recipe


def with_workbook_export(recipe: Recipe, filename: str, *, plain: bool = False) -> tuple[Recipe, str]:
    """把表格交付物化为可供后续脚本引用的流程文件。"""
    recipe = recipe.model_copy(deep=True)
    output = sanitize_table_name("工作簿_" + recipe.id)
    if plain:
        lines = [f"with pd.ExcelWriter(OUTPUT_DIR / {filename!r}, engine='openpyxl') as writer:"]
        for spec in recipe.output_spec:
            lines.append(f"    tables[{spec.source!r}].to_excel(writer, sheet_name={spec.name!r}, index=False)")
        lines.extend([f"deliver_files = [{filename!r}]", "check(True, '工作簿已导出')"])
        params = {"code": "\n".join(lines), "input_tables": [o.source for o in recipe.output_spec]}
    else:
        params = {"code": "pass", "input_tables": [o.source for o in recipe.output_spec],
                  "workbook_export": {"filename": filename, "output_spec": [o.model_dump() for o in recipe.output_spec]}}
    params["output_files"] = [{"name": filename, "ref": f"{output}/{filename}"}]
    recipe.steps.append(RecipeStep(id=max((s.id for s in recipe.steps), default=0) + 1, name="生成处理后的工作簿",
                                   kind="python", formula="python_script", output=output, params=params))
    return recipe, f"{output}/{filename}"
