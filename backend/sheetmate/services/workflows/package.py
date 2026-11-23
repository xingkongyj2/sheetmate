"""流程包导出与导入：支持两种明确区分格式的 zip 包。

1. 完整流程包（mode="exact"，文件名 `*_完整流程包.zip`）：
   - 流程包.json     清单（mode="exact", action="import_exact", definition="流程定义.json"）
   - 流程定义.json   完整可执行的流程定义（含内联 Python 脚本源码、客户公式、字段映射、规则记忆与业务总结）
   - 输入/           样例输入文件
   - 输出/           样例输出文件
   仅用于客户页「导入流程」直接无损导入（含新建空客户），不触发 AI 重建。

2. 对话重建包（mode="rebuild"，文件名 `*_对话重建包.zip`）：
   - 流程包.json     清单（mode="rebuild", action="rebuild_flow", logic="处理逻辑.md"）
   - 处理逻辑.md     给 AI 读的处理说明与参考实现
   - 输入/           样例输入文件
   - 输出/           样例输出文件
   逻辑不变，用于拖入聊天页面由 AI 按处理逻辑重新建立并执行；客户页直接导入会明确拦截。
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy.orm import Session

from sheetmate.core.config.settings import settings
from sheetmate.core.errors import ServiceError
from sheetmate.db.models import FileRecord, User, now
from sheetmate.engine.scripts.store import resolve_script_code
from sheetmate.recipe.definition.schema import Recipe, check_recipe, new_recipe_id
from sheetmate.services.files import service as file_service

MARKER_FILE = "流程包.json"
DEFINITION_FILE = "流程定义.json"
LOGIC_FILE = "处理逻辑.md"
INPUT_DIR, OUTPUT_DIR = "输入", "输出"
FORMAT = "sheetmate.flow-package"
FORMAT_VERSION = 2
REBUILD_FORMAT_VERSION = 1
DEFINITION_FORMAT = "sheetmate.flow-definition"
DEFINITION_VERSION = 1
MODE_EXACT = "exact"
MODE_REBUILD = "rebuild"
ACTION_IMPORT_EXACT = "import_exact"
ACTION_REBUILD = "rebuild_flow"
INSTRUCTION = "根据「处理逻辑.md」和「输入」里的文件，重新建立这个流程。"
MAX_PACKAGE_FILES = 60
MAX_UNPACKED_BYTES = 200 * 1024 * 1024
_CODE_CHARS = 6_000  # 处理逻辑.md 里参考实现的代码字符上限（流程定义.json 保留完整源码）
_IMPORT_TABLE_SUFFIXES = {".xlsx", ".xlsm", ".xls", ".csv"}
PackageMode = Literal["exact", "rebuild"]


def _is_final_output(record: FileRecord) -> bool:
    profile = record.profile or {}
    return (record.kind == "output" and not profile.get("intermediate") and profile.get("step_id") is None
            and profile.get("delivery_status") not in {"intermediate", "superseded", "candidate"})


def package_files(db: Session, customer_id: str, recipe_id: str, primary_recipe_id: str | None) -> tuple[list[FileRecord], list[FileRecord]]:
    """这个流程的样例输入文件、样例输出文件（只要最终结果，不要各环节的中间文件）。"""
    resources = file_service.recipe_files(db, customer_id, recipe_id, primary_recipe_id=primary_recipe_id)
    inputs = [item for item in resources if item.kind in {"input", "expected"}]
    outputs = [item for item in resources if _is_final_output(item)]
    return inputs, outputs


def _fence(code: str, language: str) -> str:
    clipped = code if len(code) <= _CODE_CHARS else code[:_CODE_CHARS] + "\n…（过长，已截断）"
    return f"```{language}\n{clipped.rstrip()}\n```"


def _step_reference(step: Any) -> str:
    """一步的实现，仅供参考：SQL / 脚本原文，公式写名称和参数。"""
    if step.kind == "python":
        try:
            return _fence(resolve_script_code(step.params), "python")
        except (OSError, ValueError):
            return ""
    if step.formula == "custom_sql":
        sql = str(step.params.get("sql") or "").strip()
        return _fence(sql, "sql") if sql else ""
    params = json.dumps(step.params, ensure_ascii=False, indent=2)
    return f"公式 `{step.formula}`，参数：\n{_fence(params, 'json')}"


def _export_recipe_dict(db: Session, recipe: Recipe) -> dict[str, Any]:
    """生成可跨客户/跨环境迁移的完整流程定义：内联 Python 脚本源码，并把文件 ID 绑定转为文件名绑定。"""
    body = deepcopy(recipe.to_dict())
    body.pop("customer_id", None)
    for step in body.get("steps", []):
        if step.get("kind") == "python" and isinstance(step.get("params"), dict):
            params = dict(step["params"])
            try:
                code = resolve_script_code(params)
                params["code"] = code
            except (OSError, ValueError):
                pass
            params.pop("script_id", None)
            params.pop("script_filename", None)
            step["params"] = params

    learned = dict(body.get("learned_from") or {})
    bindings = learned.pop("input_bindings", None)
    learned.pop("conversation_id", None)
    binding_names: dict[str, dict[str, Any]] = {}
    if isinstance(bindings, dict):
        for alias, value in bindings.items():
            if not isinstance(value, dict):
                continue
            file_id = value.get("file_id")
            rec = db.get(FileRecord, file_id) if isinstance(file_id, str) and file_id else None
            if rec is not None:
                binding_names[str(alias)] = {"filename": rec.filename, "sheet": value.get("sheet")}
    if binding_names:
        learned["input_binding_names"] = binding_names
    body["learned_from"] = learned

    metadata = dict(body.get("metadata") or {})
    for transient in ("last_run", "verification", "revision_base_version"):
        metadata.pop(transient, None)
    body["metadata"] = metadata
    return body


def _export_customer_formulas(db: Session, customer_id: str, recipe: Recipe) -> list[dict[str, Any]]:
    """打包流程步骤引用的客户独有公式，导入到空客户时可自动补齐。"""
    from sheetmate.services.workflows import service as recipe_service

    needed = {step.formula for step in recipe.steps if step.kind == "formula" and step.formula}
    if not needed:
        return []
    result: list[dict[str, Any]] = []
    for record in recipe_service.latest_formula_records(db, customer_id):
        if record.formula_id not in needed:
            continue
        spec = recipe_service.record_to_formula(record)
        if spec is None:
            continue
        result.append(spec.model_dump(mode="json", by_alias=True))
    return result


def export_package(db: Session, recipe: Recipe, *, mode: PackageMode = MODE_EXACT) -> tuple[str, bytes]:
    """导出流程包，返回 (建议的文件名, zip 字节)。

    - mode="exact"：完整流程包（*_完整流程包.zip），供客户页直接按原格式无损导入；
    - mode="rebuild"：对话重建包（*_对话重建包.zip），供拖入聊天页由 AI 重建执行。
    只读，不触发任何 AI 调用。"""
    from sheetmate.db.models import Customer
    from sheetmate.services.memory import long_term as memory_service
    from sheetmate.services.workflows import service as recipe_service

    if mode not in {MODE_EXACT, MODE_REBUILD}:
        raise ServiceError("不支持的导出模式，请选择完整流程包或对话重建包")
    customer = db.get(Customer, recipe.customer_id) if recipe.customer_id else None
    if customer is None:
        raise ServiceError("只有客户下的流程可以导出")
    inputs, outputs = package_files(db, customer.id, recipe.id, customer.primary_recipe_id)
    profile = recipe_service.ensure_profile(db, recipe_service.get_record(db, recipe.id))
    human_memories = [
        {"category": item.get("category") or "domain_knowledge", "key": item.get("key") or "", "value": item.get("value") or ""}
        for item in memory_service.customer_visible_memories(db, customer.id, recipe_id=recipe.id)
        if item.get("source") == "human" and item.get("value")
    ]
    memories = [item["value"] for item in human_memories]
    mapping = memory_service.field_mapping_for(db, customer.id, recipe_id=recipe.id)
    safe_base = re.sub(r'[\\/:*?"<>|]+', "_", recipe.name).strip() or "流程"
    if mode == MODE_EXACT:
        definition = {
            "format": DEFINITION_FORMAT,
            "version": DEFINITION_VERSION,
            "recipe": _export_recipe_dict(db, recipe),
            "formulas": _export_customer_formulas(db, customer.id, recipe),
            "field_mapping": mapping,
            "memories": human_memories,
            "summary": profile.summary or "",
        }
        data = build_exact_package(recipe, inputs, outputs, definition=definition)
        return f"{safe_base}_完整流程包.zip", data
    data = build_package(recipe, inputs, outputs, summary=profile.summary or "", memories=memories, mapping=mapping)
    return f"{safe_base}_对话重建包.zip", data


def build_logic(recipe: Recipe, *, summary: str, memories: list[str], mapping: dict[str, list[str]],
                input_names: list[str], output_names: list[str], expected_names: list[str] | None = None) -> str:
    """处理逻辑.md：写给 AI 读的处理说明。"""
    lines = [f"# {recipe.name}", ""]
    if recipe.description.strip():
        lines += [recipe.description.strip(), ""]
    # 流程总结（页面「处理逻辑」那份）讲的就是下面的输入、步骤、输出和用户记下的规则，放进来 AI 要读重复的内容，所以不放。

    lines += ["## 输入文件", ""]
    lines += [f"- 输入/{name}" for name in input_names] or ["- （没有）"]
    lines.append("")
    for item in recipe.inputs:
        label = item.source_file or item.alias
        sheet = f"，工作表「{item.sheet}」" if item.sheet else ""
        if item.kind == "file":
            lines.append(f"- **{label}**：完整文件，由脚本读取并核对工作簿内容{sheet}")
        else:
            columns = item.required_columns or item.expected_columns
            lines.append(f"- **{label}**{sheet}：需要包含 {'、'.join(columns) if columns else '（未指定列）'}")
            if item.key_columns:
                lines.append(f"  业务主键：{'、'.join(item.key_columns)}")
        if item.description.strip() and item.description.strip() != f"来自「{label}」":
            lines.append(f"  {item.description.strip()}")
    lines.append("")
    if expected_names:
        lines += ["## 期望结果样例", "", "人工做好的结果，用来核对新建的流程算得对不对，不是数据来源。"
                  "它是历史样例，可能和下面的处理步骤有出入（比如行数、小计或总计行），出入时以处理逻辑为准。", ""]
        lines += [f"- 输入/{name}" for name in expected_names] + [""]

    lines += ["## 处理步骤", ""]
    for index, step in enumerate(recipe.ordered_steps(), start=1):
        lines.append(f"{index}. **{step.name}**" + (f"：{step.description.strip()}" if step.description.strip() else ""))
    lines.append("")

    lines += ["## 输出结果", ""]
    for name in output_names:
        lines.append(f"- 输出/{name}")
    lines.append("")
    for sheet in recipe.output_spec:
        columns = f"，列：{'、'.join(sheet.columns)}" if sheet.columns else ""
        lines.append(f"- 工作表「{sheet.name}」{columns}" + (f"：{sheet.description.strip()}" if sheet.description.strip() else ""))
    for item in recipe.flow().get("output_files", []):
        if item.get("name") and item["name"] not in output_names:
            lines.append(f"- 流程当前声明的交付文件名：「{item['name']}」")
    lines.append("")

    if recipe.validation_rules:
        lines += ["## 校验规则", ""] + [f"- {rule.description or rule.type}" for rule in recipe.validation_rules] + [""]
    if mapping:
        lines += ["## 字段对应（不同表格里同一个字段的不同叫法）", ""]
        lines += [f"- {standard}：{'、'.join(aliases)}" for standard, aliases in mapping.items()] + [""]
    if memories:
        lines += ["## 需要遵循的规则（用户记下的，优先于其他说明）", ""] + [f"- {text}" for text in memories] + [""]

    lines += ["## 参考实现", "",
              "下面是原流程各步骤的实际做法，用来帮助理解口径和细节。这是参考，不要求照搬："
              "重新建立流程时，请按「处理步骤」和上面的说明，结合输入输出样例，用当前的数据自己设计步骤并验证结果。", ""]
    for index, step in enumerate(recipe.ordered_steps(), start=1):
        reference = _step_reference(step)
        if reference:
            lines += [f"### {index}. {step.name}", "", reference, ""]
    return "\n".join(lines).rstrip() + "\n"


def _content_digest(path: str) -> str | None:
    """文件内容的指纹。Excel 只看各工作表里的值：重新保存会改写时间戳，字节不同但表格内容一样的两份算同一份。"""
    try:
        if Path(path).suffix.lower() in {".xlsx", ".xlsm"}:
            import openpyxl

            workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
            try:
                digest = hashlib.sha256()
                for sheet in workbook.worksheets:
                    digest.update(f"\x00{sheet.title}\x00".encode())
                    for row in sheet.iter_rows(values_only=True):
                        digest.update(repr(row).encode())
                return digest.hexdigest()
            finally:
                workbook.close()
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except Exception:  # noqa: BLE001 - 打不开的文件退回按字节比较，再不行就不去重
        try:
            return hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except OSError:
            return None


def _distinct(records: list[FileRecord], prefer: set[str] = frozenset()) -> list[FileRecord]:
    """内容完全一样的文件只留一份：流程每跑一次、改一次名都会多出一份同样的结果，打进包里只会让 AI 多读一遍。

    留哪份：文件名是流程当前声明的交付文件名的优先，其次是最新的。顺序保持原来的排列。"""
    best: dict[str, FileRecord] = {}
    keys: dict[str, str] = {}
    for record in records:
        digest = _content_digest(record.stored_path) or f"unreadable:{record.id}"  # 读不到的文件不参与去重，交给后面的跳过逻辑
        keys[record.id] = digest
        kept = best.get(digest)
        rank = (record.filename in prefer, str(record.created_at or ""))
        if kept is None or rank > (kept.filename in prefer, str(kept.created_at or "")):
            best[digest] = record
    chosen = {record.id for record in best.values()}
    return [record for record in records if record.id in chosen]


def _arc_names(records: list[FileRecord], folder: str) -> list[tuple[FileRecord, str]]:
    """压缩包里的文件名：同名时加序号，保证不互相覆盖。"""
    used: set[str] = set()
    result = []
    for record in records:
        name = re.sub(r"[\\/:*?\"<>|]+", "_", record.filename).strip() or "file"
        candidate, count = name, 1
        while candidate.lower() in used:
            count += 1
            stem, dot, ext = name.rpartition(".")
            candidate = f"{stem}（{count}）{dot}{ext}" if dot else f"{name}（{count}）"
        used.add(candidate.lower())
        result.append((record, f"{folder}/{candidate}"))
    return result


def build_exact_package(recipe: Recipe, inputs: list[FileRecord], outputs: list[FileRecord], *,
                        definition: dict[str, Any]) -> bytes:
    """打包完整流程包（mode=exact）：流程包.json + 流程定义.json + 样例输入/输出文件，专用于客户页直接无损导入。"""
    declared = {str(item.get("name")) for item in recipe.flow().get("output_files", []) if item.get("name")}
    inputs, outputs = _distinct(inputs), _distinct(outputs, prefer=declared)
    input_items, output_items = _arc_names(inputs, INPUT_DIR), _arc_names(outputs, OUTPUT_DIR)
    if len(output_items) == 1 and len(declared) == 1:
        (record, arc), name = output_items[0], next(iter(declared))
        safe = re.sub(r'[\\/:*?"<>|]+', "_", name).strip()
        output_items = [(record, f"{OUTPUT_DIR}/{safe or PurePosixPath(arc).name}")]
    if len(input_items) + len(output_items) > MAX_PACKAGE_FILES:
        raise ServiceError(f"这个流程的样例文件太多（超过 {MAX_PACKAGE_FILES} 个），请先清理不再需要的文件再导出")
    readable = [(r, arc) for r, arc in (*input_items, *output_items) if Path(r.stored_path).is_file()]
    if sum(Path(r.stored_path).stat().st_size for r, _ in readable) > MAX_UNPACKED_BYTES:
        raise ServiceError("这个流程的样例文件总共太大，无法导出")
    readable_set = set(readable)
    manifest = {
        "format": FORMAT,
        "version": FORMAT_VERSION,
        "mode": MODE_EXACT,
        "action": ACTION_IMPORT_EXACT,
        "name": recipe.name,
        "definition": DEFINITION_FILE,
        "inputs": [{"file": arc, "kind": r.kind, "original_name": r.filename} for r, arc in input_items if (r, arc) in readable_set],
        "outputs": [{"file": arc, "original_name": PurePosixPath(arc).name} for r, arc in output_items if (r, arc) in readable_set],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MARKER_FILE, json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr(DEFINITION_FILE, json.dumps(definition, ensure_ascii=False, indent=2))
        for record, arc in readable:
            archive.write(record.stored_path, arc)
    return buffer.getvalue()


def build_package(recipe: Recipe, inputs: list[FileRecord], outputs: list[FileRecord], *, summary: str,
                  memories: list[str], mapping: dict[str, list[str]]) -> bytes:
    """打包对话重建包（mode=rebuild）：处理逻辑.md + 样例输入输出，拖入聊天页由 AI 重建执行。"""
    declared = {str(item.get("name")) for item in recipe.flow().get("output_files", []) if item.get("name")}
    inputs, outputs = _distinct(inputs), _distinct(outputs, prefer=declared)
    input_items, output_items = _arc_names(inputs, INPUT_DIR), _arc_names(outputs, OUTPUT_DIR)
    if len(output_items) == 1 and len(declared) == 1:
        # 流程只交付一个文件：样例结果就用流程现在声明的文件名，和流程图上看到的一致（旧结果可能是改名之前生成的）。
        (record, arc), name = output_items[0], next(iter(declared))
        safe = re.sub(r"[\\/:*?\"<>|]+", "_", name).strip()
        output_items = [(record, f"{OUTPUT_DIR}/{safe or PurePosixPath(arc).name}")]
    if len(input_items) + len(output_items) > MAX_PACKAGE_FILES:
        raise ServiceError(f"这个流程的样例文件太多（超过 {MAX_PACKAGE_FILES} 个），请先清理不再需要的文件再导出")
    readable = [(r, arc) for r, arc in (*input_items, *output_items) if Path(r.stored_path).is_file()]
    if sum(Path(r.stored_path).stat().st_size for r, _ in readable) > MAX_UNPACKED_BYTES:
        raise ServiceError("这个流程的样例文件总共太大，无法导出")
    readable_inputs = [arc for r, arc in input_items if (r, arc) in readable]
    readable_outputs = [arc for r, arc in output_items if (r, arc) in readable]
    manifest = {
        "format": FORMAT,
        "version": REBUILD_FORMAT_VERSION,
        "mode": MODE_REBUILD,
        "action": ACTION_REBUILD,
        "instruction": INSTRUCTION,
        "name": recipe.name,
        "inputs": [{"file": arc, "kind": r.kind, "original_name": r.filename} for r, arc in input_items if (r, arc) in readable],
        "outputs": [{"file": arc, "original_name": r.filename} for r, arc in output_items if (r, arc) in readable],
        "logic": LOGIC_FILE,
    }
    kinds = {arc: r.kind for r, arc in input_items}
    logic = build_logic(recipe, summary=summary, memories=memories, mapping=mapping,
                        input_names=[PurePosixPath(arc).name for arc in readable_inputs if kinds[arc] != "expected"],
                        expected_names=[PurePosixPath(arc).name for arc in readable_inputs if kinds[arc] == "expected"],
                        output_names=[PurePosixPath(arc).name for arc in readable_outputs])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MARKER_FILE, json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr(LOGIC_FILE, logic)
        for record, arc in readable:
            archive.write(record.stored_path, arc)
    return buffer.getvalue()


def _safe_arc_path(path: Any) -> str | None:
    if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path:
        return None
    parts = PurePosixPath(path).parts
    if not parts or ".." in parts:
        return None
    return path


def _parse_exact_package(data: bytes, filename: str) -> tuple[dict[str, Any], list[tuple[str, str, bytes]], list[tuple[str, bytes]]]:
    """严格解析客户页导入的「完整流程包（.zip）」，拒绝非完整格式包（如对话重建包或普通文件）。"""
    if not data:
        raise ServiceError("上传的文件是空的")
    if len(data) > MAX_UNPACKED_BYTES:
        raise ServiceError("导入的文件太大，无法处理")
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise ServiceError("客户页仅支持导入「完整流程包（.zip）」，请先在流程详情页选择导出完整流程包")

    try:
        with zipfile.ZipFile(io.BytesIO(data), "r") as archive:
            infos = archive.infolist()
            if len(infos) > MAX_PACKAGE_FILES + 8:
                raise ServiceError("流程包里的文件太多，无法导入")
            total_size = sum(info.file_size for info in infos)
            if total_size > MAX_UNPACKED_BYTES:
                raise ServiceError("流程包解压后体积太大，无法导入")

            names = set(archive.namelist())
            if MARKER_FILE not in names:
                raise ServiceError("这不是 SheetMate 导出的流程包（缺少 流程包.json）")
            try:
                manifest = json.loads(archive.read(MARKER_FILE).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ServiceError("流程包清单已损坏") from exc
            if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
                raise ServiceError("不支持的流程包格式")

            mode = manifest.get("mode")
            action = manifest.get("action")
            if mode == MODE_REBUILD or action == ACTION_REBUILD:
                raise ServiceError("这是「对话重建包」，仅支持拖入聊天页面由 AI 重建；客户页直接导入请导出「完整流程包」")
            if mode != MODE_EXACT or action != ACTION_IMPORT_EXACT:
                raise ServiceError("客户页仅支持导入「完整流程包」，请重新选择导出「完整流程包」后再导入")

            def_path = _safe_arc_path(manifest.get("definition")) or DEFINITION_FILE
            if def_path not in names:
                raise ServiceError("完整流程包缺少流程定义文件（流程定义.json），无法导入")
            try:
                definition = json.loads(archive.read(def_path).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ServiceError("流程定义文件已损坏") from exc
            if (not isinstance(definition, dict) or definition.get("format") != DEFINITION_FORMAT
                    or not isinstance(definition.get("recipe"), dict)):
                raise ServiceError("流程定义格式不正确或不完整")

            input_files: list[tuple[str, str, bytes]] = []
            for item in manifest.get("inputs") if isinstance(manifest.get("inputs"), list) else []:
                if not isinstance(item, dict):
                    continue
                arc = _safe_arc_path(item.get("file"))
                if not arc or arc not in names:
                    continue
                orig_name = str(item.get("original_name") or PurePosixPath(arc).name).strip()
                suffix = Path(orig_name).suffix.lower()
                if suffix not in _IMPORT_TABLE_SUFFIXES:
                    continue
                kind = "expected" if item.get("kind") == "expected" else "input"
                raw_bytes = archive.read(arc)
                if raw_bytes:
                    input_files.append((orig_name, kind, raw_bytes))

            output_files: list[tuple[str, bytes]] = []
            for item in manifest.get("outputs") if isinstance(manifest.get("outputs"), list) else []:
                if not isinstance(item, dict):
                    continue
                arc = _safe_arc_path(item.get("file"))
                if not arc or arc not in names:
                    continue
                orig_name = str(item.get("original_name") or PurePosixPath(arc).name).strip()
                suffix = Path(orig_name).suffix.lower()
                if suffix not in _IMPORT_TABLE_SUFFIXES:
                    continue
                raw_bytes = archive.read(arc)
                if raw_bytes:
                    output_files.append((orig_name, raw_bytes))
            return definition, input_files, output_files
    except zipfile.BadZipFile as exc:
        raise ServiceError("压缩包已损坏，无法读取") from exc


def import_package(db: Session, *, data: bytes, filename: str, customer_id: str, user: User) -> dict[str, Any]:
    """把导出的「完整流程包（.zip）」直接无损导入到指定客户（支持空客户）。"""
    from sheetmate.services.memory.field_mappings import upsert_field_mapping
    from sheetmate.services.memory.repository import set_memory
    from sheetmate.services.workflows import service as recipe_service

    definition, bundled_inputs, bundled_outputs = _parse_exact_package(data, filename)

    # 1. 先补齐客户独有公式（如果有）
    existing_formulas = {
        rec.formula_id: recipe_service.record_to_formula(rec)
        for rec in recipe_service.latest_formula_records(db, customer_id)
    }
    for formula_body in definition.get("formulas") if isinstance(definition.get("formulas"), list) else []:
        if not isinstance(formula_body, dict) or not formula_body.get("id"):
            continue
        fid = str(formula_body["id"])
        existing = existing_formulas.get(fid)
        if existing is not None and existing.sql == formula_body.get("sql") and existing.status == "active":
            continue
        try:
            recipe_service.save_formula(db, customer_id, formula_body, created_by="human")
        except Exception as exc:  # noqa: BLE001 - 公式校验失败直接阻断导入并给出明确原因
            raise ServiceError(f"导入流程依赖的自定义公式「{fid}」失败：{exc}") from exc

    # 2. 清洗并构造新流程（重新生成 recipe_id、归属当前客户、物化 Python 脚本文件）
    raw_recipe = deepcopy(definition["recipe"])
    raw_recipe["id"] = new_recipe_id()
    raw_recipe["version"] = 1
    raw_recipe["scope"] = "customer"
    raw_recipe["customer_id"] = customer_id
    raw_recipe["created_by"] = "human"
    metadata = dict(raw_recipe.get("metadata") or {})
    for transient in ("last_run", "verification", "revision_base_version"):
        metadata.pop(transient, None)
    metadata["version_note"] = "从流程包导入"
    raw_recipe["metadata"] = metadata

    learned = dict(raw_recipe.get("learned_from") or {})
    binding_names = learned.pop("input_binding_names", None)
    learned.pop("input_bindings", None)
    learned.pop("conversation_id", None)
    raw_recipe["learned_from"] = learned

    # 3. 导入样例输入文件到当前客户（同客户已有同名同内容文件时直接复用，避免产生多余副本）
    existing_inputs = file_service.list_files(db, customer_id=customer_id, kinds=("input", "expected"))
    existing_by_name: dict[str, list[FileRecord]] = {}
    for rec in existing_inputs:
        existing_by_name.setdefault(rec.filename.casefold(), []).append(rec)

    def _reuse_existing(orig_name: str, kind: str, raw_bytes: bytes) -> FileRecord | None:
        candidates = existing_by_name.get(file_service.safe_filename(orig_name).casefold(), [])
        if not candidates:
            return None
        incoming_hash = hashlib.sha256(raw_bytes).hexdigest()
        for rec in candidates:
            if rec.kind != kind:
                continue
            path = Path(rec.stored_path)
            if path.is_file() and path.stat().st_size == len(raw_bytes):
                try:
                    if hashlib.sha256(path.read_bytes()).hexdigest() == incoming_hash:
                        return rec
                except OSError:
                    continue
        return None

    uploaded_records: list[FileRecord] = []
    linked_records: list[FileRecord] = []
    by_original_name: dict[str, FileRecord] = {}
    for orig_name, kind, raw_bytes in bundled_inputs:
        reused = _reuse_existing(orig_name, kind, raw_bytes)
        if reused is not None:
            record = reused
        else:
            record = file_service.save_upload(
                db, data=raw_bytes, filename=orig_name, user=user,
                customer_id=customer_id, kind=kind,
            )
            uploaded_records.append(record)
        linked_records.append(record)
        by_original_name.setdefault(orig_name.casefold(), record)
        by_original_name.setdefault(record.filename.casefold(), record)

    # 4. 恢复 input_bindings，使新客户的「样例文件」按钮开箱即用
    new_bindings: dict[str, dict[str, Any]] = {}
    if isinstance(binding_names, dict):
        for alias, spec in binding_names.items():
            if not isinstance(spec, dict):
                continue
            fname = str(spec.get("filename") or "")
            matched = by_original_name.get(fname.casefold())
            if matched is not None:
                new_bindings[str(alias)] = {"file_id": matched.id, "sheet": spec.get("sheet")}
    for inp in raw_recipe.get("inputs") if isinstance(raw_recipe.get("inputs"), list) else []:
        if not isinstance(inp, dict):
            continue
        alias = str(inp.get("alias") or "")
        if not alias or alias in new_bindings:
            continue
        source_file = str(inp.get("source_file") or "")
        matched = by_original_name.get(source_file.casefold())
        if matched is not None:
            inp["source_file"] = matched.filename
            new_bindings[alias] = {"file_id": matched.id, "sheet": inp.get("sheet")}
    if new_bindings:
        raw_recipe["learned_from"]["input_bindings"] = new_bindings

    try:
        recipe = Recipe.model_validate(raw_recipe)
    except ValidationError as exc:
        raise ServiceError(f"流程定义校验未通过：{exc.errors()[0].get('msg', '结构不合法')}") from exc
    except ValueError as exc:
        raise ServiceError(f"流程脚本解析失败：{exc}") from exc

    problems = check_recipe(recipe, recipe_service.library_for(db, customer_id))
    if problems:
        raise ServiceError("流程结构检查未通过：" + "；".join(problems[:3]))

    saved = recipe_service.save_recipe(db, recipe)
    for file_record in linked_records:
        file_service.retain_for_recipe(db, file_record, customer_id, saved.recipe_id)

    # 4.5 导入样例输出文件到当前客户（同客户已有同名同内容最终结果时直接复用）
    existing_outputs = [
        rec for rec in file_service.list_files(db, customer_id=customer_id, kinds=("output",))
        if _is_final_output(rec)
    ]
    existing_outputs_by_name: dict[str, list[FileRecord]] = {}
    for rec in existing_outputs:
        existing_outputs_by_name.setdefault(rec.filename.casefold(), []).append(rec)

    def _reuse_existing_output(orig_name: str, raw_bytes: bytes) -> FileRecord | None:
        candidates = existing_outputs_by_name.get(file_service.safe_filename(orig_name).casefold(), [])
        if not candidates:
            return None
        incoming_hash = hashlib.sha256(raw_bytes).hexdigest()
        for rec in candidates:
            path = Path(rec.stored_path)
            if path.is_file() and path.stat().st_size == len(raw_bytes):
                try:
                    if hashlib.sha256(path.read_bytes()).hexdigest() == incoming_hash:
                        return rec
                except OSError:
                    continue
        return None

    uploaded_outputs: list[FileRecord] = []
    written_output_paths: list[Path] = []
    try:
        for orig_name, raw_bytes in bundled_outputs:
            reused_out = _reuse_existing_output(orig_name, raw_bytes)
            if reused_out is not None:
                out_record = reused_out
            else:
                safe_name = file_service.safe_filename(orig_name)
                folder = settings.outputs_dir / file_service.owner_dir(customer_id, None)
                folder.mkdir(parents=True, exist_ok=True)
                out_path = folder / f"imp_{uuid4().hex[:8]}_{safe_name}"
                out_path.write_bytes(raw_bytes)
                written_output_paths.append(out_path)
                out_record = file_service.register_output(
                    db, path=out_path, filename=orig_name, customer_id=customer_id,
                    conversation_id=None, run_id=None, user_id=user.id, delivery_status="final",
                )
                uploaded_outputs.append(out_record)
            file_service.retain_for_recipe(db, out_record, customer_id, saved.recipe_id)
    except BaseException:
        for path in written_output_paths:
            path.unlink(missing_ok=True)
        raise

    # 5. 恢复字段映射、人工规则记忆与业务总结
    mapping = definition.get("field_mapping")
    if not isinstance(mapping, dict):
        mapping = recipe.field_mapping
    for standard, aliases in mapping.items() if isinstance(mapping, dict) else []:
        if isinstance(standard, str) and isinstance(aliases, list) and aliases:
            clean_aliases = [str(a) for a in aliases if isinstance(a, str) and a.strip()]
            if clean_aliases:
                upsert_field_mapping(db, customer_id, standard, clean_aliases, source="human", recipe_id=saved.recipe_id)

    for mem in definition.get("memories") if isinstance(definition.get("memories"), list) else []:
        if not isinstance(mem, dict):
            continue
        value = str(mem.get("value") or "").strip()
        if not value:
            continue
        key = str(mem.get("key") or value[:24]).strip()
        category = str(mem.get("category") or "domain_knowledge")
        try:
            set_memory(db, category=category, key=key, value=value, customer_id=customer_id,
                       recipe_id=saved.recipe_id, source="human")
        except ServiceError:
            continue

    summary_text = str(definition.get("summary") or "").strip()
    if summary_text:
        from sheetmate.services.customers import service as customer_service

        profile = recipe_service.ensure_profile(db, saved)
        profile.summary = summary_text
        profile.summary_by = "ai"
        profile.summary_updated_at = now()
        profile.summary_policy_version = customer_service.SUMMARY_POLICY_VERSION

    db.flush()
    return {
        "record": saved,
        "uploaded_file_ids": [rec.id for rec in uploaded_records],
        "imported_files": len(uploaded_records) + len(uploaded_outputs),
        "has_summary": bool(summary_text),
    }
