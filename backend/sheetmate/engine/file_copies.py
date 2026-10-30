from __future__ import annotations

import hashlib
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory


@dataclass
class FileCopies:
    root: Path
    originals: dict[Path, Path] = field(default_factory=dict)
    copies: dict[Path, Path] = field(default_factory=dict)

    def file_copy(self, original: Path) -> Path:
        source = original.resolve()
        if source in self.originals:
            return original
        if source in self.copies:
            return self.copies[source]
        key = hashlib.sha256(str(source).encode()).hexdigest()
        destination = self.root / key / original.name
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        self.originals[destination.resolve()] = source
        self.copies[source] = destination
        return destination

    def original_path(self, path: Path) -> Path:
        return self.originals.get(path.resolve(), path)


@contextmanager
def temporary_file_copies() -> Iterator[FileCopies]:
    with TemporaryDirectory(prefix="sheetmate_files_") as directory:
        yield FileCopies(Path(directory))
