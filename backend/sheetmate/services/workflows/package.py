"""流程包：把一个流程的样例文件和处理逻辑打成一个 zip，在聊天里发回来就能让 AI 重新建立这个流程。

包里放的是「逻辑」，不是可以原样还原的流程步骤：
- 流程包.json     指令文件：说明这个包是什么、这次要做什么（action）。后端解压后看到它，
                  就知道要按包里的逻辑和样例文件重新建立流程；
- 处理逻辑.md     写给 AI 读的处理说明（做什么、要什么输入、得到什么输出、各步怎么算、有什么规则），
                  角色等同于 build_recipe 的 requirement；
- 输入/           样例输入文件；
- 输出/           样例输出文件（人工做好的结果样例，导入后作为期望结果）。

导入时 AI 用 build_recipe 重新生成流程，所以步骤、表名、脚本都会按当时的数据重新来过；
包里的步骤实现只放在「参考实现」一节，不要求一致。
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy.orm import Session

from sheetmate.core.config.settings import settings
from sheetmate.core.errors import ServiceError
from sheetmate.db.models import FileRecord
from sheetmate.engine.scripts.store import resolve_script_code
from sheetmate.recipe.definition.schema import Recipe
from sheetmate.services.files import service as file_service

MARKER_FILE = "流程包.json"
LOGIC_FILE = "处理逻辑.md"
INPUT_DIR, OUTPUT_DIR = "输入", "输出"
FORMAT = "sheetmate.flow-package"
FORMAT_VERSION = 1
ACTION_REBUILD = "rebuild_flow"  # 目前唯一的指令：按处理逻辑和样例文件重新建立流程
INSTRUCTION = "根据「处理逻辑.md」和「输入」「输出」里的样例文件，重新建立这个流程。"
MAX_PACKAGE_FILES = 60
MAX_UNPACKED_BYTES = 200 * 1024 * 1024
_CODE_CHARS = 6_000  # 每一步放进参考实现的 SQL / 脚本字符上限，过长只留开头


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


def export_package(db: Session, recipe: Recipe) -> tuple[str, bytes]:
    """导出这个流程的流程包，返回 (建议的文件名, zip 字节)。

    只读，不触发任何 AI 调用：业务说明用已有的流程总结（没有就留空），记忆只放用户自己写的。"""
    from sheetmate.db.models import Customer
    from sheetmate.services.memory import long_term as memory_service
    from sheetmate.services.workflows import service as recipe_service

    customer = db.get(Customer, recipe.customer_id) if recipe.customer_id else None
    if customer is None:
        raise ServiceError("只有客户下的流程可以导出")
    inputs, outputs = package_files(db, customer.id, recipe.id, customer.primary_recipe_id)
    profile = recipe_service.ensure_profile(db, recipe_service.get_record(db, recipe.id))
    memories = [item["value"] for item in memory_service.customer_visible_memories(db, customer.id, recipe_id=recipe.id)
                if item.get("source") == "human" and item.get("value")]
    mapping = memory_service.field_mapping_for(db, customer.id, recipe_id=recipe.id)
    data = build_package(recipe, inputs, outputs, summary=profile.summary or "", memories=memories, mapping=mapping)
    return f"{re.sub(r'[\\/:*?\"<>|]+', '_', recipe.name).strip() or '流程'}_流程包.zip", data


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
        lines += ["## 期望结果样例", "", "人工做好的结果，用来核对新建的流程算得对不对，不是数据来源。", ""]
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
            lines.append(f"- 文件「{item['name']}」")
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


def build_package(recipe: Recipe, inputs: list[FileRecord], outputs: list[FileRecord], *, summary: str,
                  memories: list[str], mapping: dict[str, list[str]]) -> bytes:
    """打成 zip。文件读不到时跳过，不让一个坏文件挡住整个导出。"""
    input_items, output_items = _arc_names(inputs, INPUT_DIR), _arc_names(outputs, OUTPUT_DIR)
    if len(input_items) + len(output_items) > MAX_PACKAGE_FILES:
        raise ServiceError(f"这个流程的样例文件太多（超过 {MAX_PACKAGE_FILES} 个），请先清理不再需要的文件再导出")
    readable = [(r, arc) for r, arc in (*input_items, *output_items) if Path(r.stored_path).is_file()]
    if sum(Path(r.stored_path).stat().st_size for r, _ in readable) > MAX_UNPACKED_BYTES:
        raise ServiceError("这个流程的样例文件总共太大，无法导出")
    readable_inputs = [arc for r, arc in input_items if (r, arc) in readable]
    readable_outputs = [arc for r, arc in output_items if (r, arc) in readable]
    manifest = {
        "format": FORMAT, "version": FORMAT_VERSION, "action": ACTION_REBUILD, "instruction": INSTRUCTION, "name": recipe.name,
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


# ---------------------------------------------------------------------------
# 导入：看到标志文件，就按包里的逻辑和样例文件重新建立流程
# ---------------------------------------------------------------------------


def is_package(data: bytes) -> bool:
    """压缩包里有隐藏的指令文件，且格式合法。只看指令文件本身，不解压其他内容。"""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return read_manifest(archive) is not None
    except (zipfile.BadZipFile, OSError):
        return False


def read_manifest(archive: zipfile.ZipFile) -> dict[str, Any] | None:
    if MARKER_FILE not in archive.namelist():
        return None
    try:
        manifest = json.loads(archive.read(MARKER_FILE).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        return None
    return manifest


def unpack(data: bytes) -> dict[str, Any]:
    """安全地解开流程包：返回 {name, logic, inputs: [(文件名, 字节, kind)], outputs: [(文件名, 字节)]}。

    只取标志文件登记过的文件；拒绝路径穿越、超大、过多的压缩包（压缩炸弹）。"""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ServiceError("这个压缩包无法打开") from exc
    with archive:
        manifest = read_manifest(archive)
        if manifest is None:
            raise ServiceError("这不是 SheetMate 的流程包")
        if manifest.get("action", ACTION_REBUILD) != ACTION_REBUILD:
            raise ServiceError(f"这个流程包要求的操作「{manifest.get('action')}」当前版本不支持")
        infos = {info.filename: info for info in archive.infolist()}
        if len(infos) > MAX_PACKAGE_FILES + 4 or sum(info.file_size for info in infos.values()) > MAX_UNPACKED_BYTES:
            raise ServiceError("流程包太大，无法导入")

        def read(name: str) -> bytes | None:
            pure = PurePosixPath(name)
            if pure.is_absolute() or ".." in pure.parts or name not in infos or infos[name].is_dir():
                return None
            if infos[name].file_size > settings.max_upload_mb * 1024 * 1024:
                raise ServiceError(f"包里的文件「{pure.name}」超过 {settings.max_upload_mb} MB")
            return archive.read(name)

        logic_bytes = read(str(manifest.get("logic") or LOGIC_FILE))
        inputs = [(PurePosixPath(item["file"]).name, content, item.get("kind", "input")) for item in manifest.get("inputs", [])
                  if isinstance(item, dict) and isinstance(item.get("file"), str) and (content := read(item["file"])) is not None]
        outputs = [(PurePosixPath(item["file"]).name, content) for item in manifest.get("outputs", [])
                   if isinstance(item, dict) and isinstance(item.get("file"), str) and (content := read(item["file"])) is not None]
        return {"name": str(manifest.get("name") or "导入的流程"),
                "logic": (logic_bytes or b"").decode("utf-8", errors="replace"),
                "inputs": inputs, "outputs": outputs}


def import_into_conversation(db: Session, *, data: bytes, user: Any, customer_id: str | None, conversation_id: str,
                             recipe_id: str | None = None) -> tuple[FileRecord, list[FileRecord]]:
    """把流程包解开放进会话：样例输入 → 输入文件，样例输出 → 期望结果，处理逻辑 → 会话里的 md 文档。

    返回 (处理逻辑文档, 登记的表格文件)。AI 之后从文件列表读到处理逻辑，用 build_recipe 重新建立流程；
    输入和期望结果正是 build_recipe 学习流程要用的材料。"""
    package = unpack(data)
    if not package["inputs"] and not package["outputs"]:
        raise ServiceError("流程包里没有可用的样例文件")
    records: list[FileRecord] = []

    def register(name: str, content: bytes, kind: str) -> None:
        try:
            record = file_service.save_upload(db, data=content, filename=name, user=user, customer_id=customer_id,
                                              conversation_id=conversation_id, kind=kind)
        except ServiceError as exc:
            raise ServiceError(f"流程包里的文件「{name}」无法导入：{exc}") from exc
        if customer_id and recipe_id:
            file_service.retain_for_recipe(db, record, customer_id, recipe_id)
        records.append(record)

    try:
        for name, content, kind in package["inputs"]:
            register(name, content, "expected" if kind == "expected" else "input")
        for name, content in package["outputs"]:
            register(name, content, "expected")  # 输出样例就是「人工做好的结果」，学习流程时用来对照
        logic = file_service.save_doc(db, text=package["logic"] or f"# {package['name']}\n", filename=LOGIC_FILE, user=user,
                                      conversation_id=conversation_id)
    except Exception:
        # 一次导入多个文件，中途失败时已经写到磁盘的不能留下：数据库那边随请求回滚，磁盘上的文件要自己清掉
        for record in records:
            Path(record.stored_path).unlink(missing_ok=True)
        raise
    return logic, records
