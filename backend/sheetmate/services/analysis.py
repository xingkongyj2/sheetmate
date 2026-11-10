from __future__ import annotations

import logging
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory

from sheetmate.engine.file_copies import FileCopies
from sheetmate.engine.table_memory import copy_table_memory, merge_table_memory
from sheetmate.engine.workspace import Workspace
from sheetmate.services.table_memory import persist_workspace_memories

logger = logging.getLogger(__name__)


def _copy_table(source: Workspace, target: Workspace, name: str) -> None:
    info = source.require(name)
    meta = info.to_dict()
    for key in ("name", "rows", "columns"):
        meta.pop(key, None)
    target.put_dataframe(name, source.df(name), **meta)
    copy_table_memory(info, target, name)


@dataclass
class AnalysisWorkspace:
    source: Workspace
    ws: Workspace
    root: Path
    files: FileCopies = field(init=False)

    def __post_init__(self) -> None:
        self.files = FileCopies(self.root / "files")

    def file_copy(self, original: Path) -> Path:
        return self.files.file_copy(original)

    def publish(self, name: str) -> None:
        existing = self.source.info(name)
        if existing is not None and existing.source == "uploaded":
            return
        _copy_table(self.ws, self.source, name)


@contextmanager
def analysis_workspace(source: Workspace, *, root: Path | None = None, resume: bool = False) -> Iterator[AnalysisWorkspace]:
    """Keep execution scratch data on disk when a durable root is supplied."""
    if root is None:
        with TemporaryDirectory(prefix="sheetmate_analysis_") as directory:
            with analysis_workspace(source, root=Path(directory)) as analysis:
                yield analysis
        return
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "inputs.json"
    if resume and (not manifest.exists() or not (root / "working.duckdb").exists()):
        raise ValueError("执行工作区已丢失，无法继续旧任务。")
    scratch = Workspace(root / "working.duckdb")
    expected_revisions: dict[str, tuple[str, str]] = {}
    try:
        if resume:
            expected_revisions = {name: tuple(value) for name, value in json.loads(manifest.read_text()).items()}
        else:
            for table in source.list_tables():
                _copy_table(source, scratch, table.name)
                expected_revisions[table.name] = (table.revision, scratch.require(table.name).revision)
            manifest.write_text(json.dumps(expected_revisions), encoding="utf-8")
        yield AnalysisWorkspace(source, scratch, root)
    finally:
        try:
            merge_table_memory(source, scratch, expected_revisions)
            persist_workspace_memories(source)
        except Exception:
            # 记忆维护失败不能遮蔽本次处理或用户取消的实际结果。
            logger.warning("无法保存本轮表格探测记忆", exc_info=True)
        finally:
            scratch.close()
