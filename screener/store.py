"""CompanyStore — atomic file I/O for per-company JSON profiles.

Single class so every caller (enrich, markets, index, freshness) shares the
same load/save/delete semantics instead of each having its own _load_company
with slightly different error handling or non-atomic writes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator


class CompanyStore:
    """Load, save, delete, and iterate company JSON files in a directory.

    All writes are atomic (write to .tmp then rename) so a crash or interrupt
    never leaves a half-written file on disk.
    """

    def __init__(self, dir_path: Path) -> None:
        self.dir_path = dir_path

    # ── Single company ───────────────────────────────────────────────

    def load(self, symbol: str) -> dict:
        """Load a company JSON. Raises FileNotFoundError if missing."""
        path = self.dir_path / f"{symbol}.json"
        with open(path) as f:
            return json.load(f)

    def save(self, symbol: str, data: dict) -> None:
        """Atomically write a company JSON (tmp + rename)."""
        self.dir_path.mkdir(parents=True, exist_ok=True)
        path = self.dir_path / f"{symbol}.json"
        tmp = self.dir_path / f".{symbol}.json.tmp"
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(path)

    def update(self, symbol: str, fn) -> None:
        """Load, transform via fn(data) -> data, save atomically."""
        data = fn(self.load(symbol))
        self.save(symbol, data)

    def delete(self, symbol: str) -> None:
        """Remove a company file. Silently no-ops if missing."""
        path = self.dir_path / f"{symbol}.json"
        path.unlink(missing_ok=True)

    # ── Batch / iteration ────────────────────────────────────────────

    def symbols(self) -> list[str]:
        """Sorted list of company symbols (file stems) on disk."""
        return sorted(p.stem for p in self.dir_path.glob("*.json"))

    def iter_all(self) -> Iterator[tuple[Path, dict]]:
        """Yield (path, company_dict) for every company file.

        Skips unreadable files (bad JSON, permission errors) rather than
        crashing the whole iteration."""
        for path in sorted(self.dir_path.glob("*.json")):
            try:
                yield path, json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                pass
