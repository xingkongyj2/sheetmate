"""从真实执行记录编译可复用流程；不调用模型，也不重新执行处理。"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import duckdb

from sheetmate.engine.formulas import FormulaLibrary
from sheetmate.engine.sqlutil import sanitize_table_name, sql_base_table_nodes
from sheetmate.engine.workspace import Workspace
from sheetmate.recipe.learner import InputMeta, LearnContext, build_inputs
from sheetmate.recipe.schema import OutputSheet, Recipe, RecipeInput, RecipeStep, file_pattern_from_name, step_table_refs


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
        return Recipe(name=name[:80], description=description, customer_id=customer_id, created_by="ai",
                      inputs=list(self.inputs.values()), steps=self.steps, output_spec=outputs,
                      field_mapping=dict(field_mapping or {}),
                      learned_from={"conversation_id": conversation_id, "from": "execution",
                                    "input_files": list(dict.fromkeys(i.source_file for i in self.inputs.values() if i.source_file)),
                                    "input_bindings": self.bindings},
                      metadata={"requirement": description, "confidence_overall": 0.9, "explicit_outputs": True,
                                "validation_checkpoints": self.checkpoints})


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
                   field_mapping: dict[str, list[str]] | None = None) -> Recipe:
    compiler = _Compiler(ws, library, file_resolver=file_resolver)
    bindings = {table: compiler.table(table) for table in input_tables}
    files = {alias: compiler.file(file_id) for alias, file_id in input_files.items()}
    output = sanitize_table_name("脚本_" + run_id)
    params = {"code": code, "input_tables": list(dict.fromkeys(bindings.values())),
              "table_bindings": bindings, "input_files": files, "output_tables": output_tables,
              "file_tables": file_tables}
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
