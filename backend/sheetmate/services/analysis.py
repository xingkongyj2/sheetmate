from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from sheetmate.engine.workspace import Workspace


def _copy_table(source: Workspace, target: Workspace, name: str) -> None:
    meta = source.require(name).to_dict()
    for key in ("name", "rows", "columns"):
        meta.pop(key, None)
    target.put_dataframe(name, source.df(name), **meta)


@dataclass
class AnalysisWorkspace:
    source: Workspace
    ws: Workspace
    root: Path

    def file_copy(self, original: Path) -> Path:
        destination = self.root / "files" / original.name
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, destination)
        return destination

    def publish(self, name: str) -> None:
        existing = self.source.info(name)
        if existing is not None and existing.source == "uploaded":
            return
        _copy_table(self.ws, self.source, name)


@contextmanager
def analysis_workspace(source: Workspace) -> Iterator[AnalysisWorkspace]:
    with TemporaryDirectory(prefix="sheetmate_analysis_") as directory:
        root = Path(directory)
        scratch = Workspace(root / "working.duckdb")
        try:
            for table in source.list_tables():
                _copy_table(source, scratch, table.name)
            yield AnalysisWorkspace(source, scratch, root)
        finally:
            scratch.close()
