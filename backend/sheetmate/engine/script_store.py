"""Immutable Python source files referenced by execution records and recipe nodes."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from sheetmate.config import settings

_SCRIPT_ID = re.compile(r"scr_[0-9a-f]{32}\Z")
MAX_SCRIPT_BYTES = 100_000
MAX_TITLE_LENGTH = 20
_BIND_LOCK = threading.RLock()


def _folder() -> Path:
    folder = settings.data_dir / "scripts"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def normalize_script_title(value: str) -> str:
    """Use the same concise title for explicit names and generated fallbacks."""
    if not isinstance(value, str):
        raise ValueError("脚本标题必须是文字")
    value = " ".join(value.split()) or "Python 脚本"
    return value[:MAX_TITLE_LENGTH]


def _source(code: str) -> str:
    if not isinstance(code, str) or not code.strip() or len(code.encode("utf-8")) > MAX_SCRIPT_BYTES:
        raise ValueError("Python 脚本不能为空，且不能超过 100 KB")
    return code


def save_script(code: str, title: str = "") -> dict[str, str]:
    """Create a new source version. Existing files are never overwritten."""
    code, title = _source(code), normalize_script_title(title)
    folder = _folder()
    script_id = f"scr_{uuid4().hex}"
    filename = f"{script_id}.py"
    metadata = {"script_id": script_id, "script_title": title, "script_filename": filename}
    source_path, metadata_path = folder / filename, folder / f"{script_id}.json"
    try:
        with source_path.open("x", encoding="utf-8") as source:
            source.write(code)
        with metadata_path.open("x", encoding="utf-8") as target:
            json.dump(metadata, target, ensure_ascii=False)
    except BaseException:
        source_path.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
        raise
    return metadata


def _read_metadata(script_id: str) -> dict[str, Any]:
    if not isinstance(script_id, str) or not _SCRIPT_ID.fullmatch(script_id):
        raise ValueError("脚本 ID 不正确")
    metadata_path = _folder() / f"{script_id}.json"
    if metadata_path.is_symlink():
        raise ValueError("脚本文件不能是符号链接")
    if metadata_path.stat().st_size > 16_000:
        raise ValueError("脚本元数据超过大小限制")
    with metadata_path.open(encoding="utf-8") as source:
        metadata = json.load(source)
    if not isinstance(metadata, dict) or not isinstance(metadata.get("script_title"), str):
        raise ValueError("脚本元数据已损坏")
    return metadata


def read_script(script_id: str) -> dict[str, str]:
    try:
        metadata = _read_metadata(script_id)
        source_path = _folder() / f"{script_id}.py"
        if source_path.is_symlink():
            raise ValueError("脚本文件不能是符号链接")
        if source_path.stat().st_size > MAX_SCRIPT_BYTES:
            raise ValueError("脚本文件超过大小限制")
        return {"script_id": script_id, "script_title": normalize_script_title(metadata["script_title"]),
                "script_filename": f"{script_id}.py", "code": _source(source_path.read_text(encoding="utf-8"))}
    except OSError as exc:
        raise ValueError("脚本文件不存在或无法读取，请重新生成脚本") from exc


def script_params(params: dict[str, Any], title: str = "") -> dict[str, Any]:
    """Replace inline source with a stable file reference; retain unchanged versions."""
    result = dict(params)
    code = result.pop("code", None)
    script_id = result.get("script_id")
    saved = read_script(script_id) if script_id else None
    explicit_title = result.get("script_title") or result.get("title")
    desired_title = normalize_script_title(explicit_title) if explicit_title else (
        saved["script_title"] if saved else normalize_script_title(str(title))
    )
    if code is not None:
        _source(code)
        if saved is None or saved["code"] != code or saved["script_title"] != desired_title:
            saved = save_script(code, desired_title)
    if saved is None:
        raise ValueError("Python 步骤缺少脚本文件或完整代码")
    result.update({key: saved[key] for key in ("script_id", "script_title", "script_filename")})
    result.pop("title", None)
    return result


def resolve_script_code(params: dict[str, Any]) -> str:
    """Read referenced source; old records containing inline code remain readable."""
    if params.get("script_id"):
        return read_script(params["script_id"])["code"]
    return _source(params.get("code"))


def script_origin(script_id: str) -> dict[str, Any] | None:
    """Internal provenance; never included in the public script payload."""
    origin = _read_metadata(script_id).get("origin")
    return origin if isinstance(origin, dict) else None


def bind_script(script_id: str, *, user_id: int | None = None, customer_id: str | None = None,
                conversation_id: str | None = None, global_access: bool = False) -> None:
    """Remember the initial owner; copying an ID elsewhere cannot change ownership."""
    if not (user_id is not None or customer_id or conversation_id or global_access):
        return
    with _BIND_LOCK:
        read_script(script_id)
        metadata_path = _folder() / f"{script_id}.json"
        with metadata_path.open(encoding="utf-8") as source:
            metadata = json.load(source)
        if isinstance(metadata.get("origin"), dict):
            return
        metadata["origin"] = {"user_id": user_id, "customer_id": customer_id,
                              "conversation_id": conversation_id, "global_access": bool(global_access)}
        temporary = metadata_path.with_suffix(f".{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as target:
                json.dump(metadata, target, ensure_ascii=False)
            temporary.replace(metadata_path)
        finally:
            temporary.unlink(missing_ok=True)


def queue_unreferenced_scope_scripts(db: Any, *, customer_ids: set[str] | None = None,
                                     conversation_ids: set[str] | None = None) -> None:
    """Queue scripts whose original scope was deleted and which no remaining row uses."""
    customer_ids = customer_ids or set()
    conversation_ids = conversation_ids or set()
    if not customer_ids and not conversation_ids:
        return

    from sheetmate.db.models import Message, RecipeRecord, Run

    used: set[str] = set()
    for message in db.scalars(select(Message)):
        for script_id in _SCRIPT_ID.findall(json.dumps(message.events or {}, ensure_ascii=False)):
            used.add(script_id)
    for run in db.scalars(select(Run)):
        for value in (run.steps, run.validation):
            for script_id in _SCRIPT_ID.findall(json.dumps(value or {}, ensure_ascii=False)):
                used.add(script_id)
    for recipe in db.scalars(select(RecipeRecord)):
        for script_id in _SCRIPT_ID.findall(json.dumps(recipe.body or {}, ensure_ascii=False)):
            used.add(script_id)

    folder = settings.data_dir / "scripts"
    if not folder.is_dir():
        return
    for metadata_path in folder.glob("scr_*.json"):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            script_id = metadata.get("script_id")
            origin = metadata.get("origin") or {}
            scoped = (origin.get("customer_id") in customer_ids or
                      origin.get("conversation_id") in conversation_ids)
            if (not isinstance(script_id, str) or not _SCRIPT_ID.fullmatch(script_id) or
                    not scoped or origin.get("global_access") or script_id in used):
                continue
            db.info.setdefault("deleted_aux_paths", set()).update({
                str(metadata_path), str(folder / f"{script_id}.py")
            })
        except (OSError, ValueError, TypeError):
            continue
