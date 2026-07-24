"""Focused, auditable repository context selection."""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path

from .security import secret_file


@dataclass(frozen=True)
class ContextFile:
    path: str
    sha256: str
    bytes: int
    content: str
    score: int

    def to_dict(self) -> dict:
        return asdict(self)


class ContextSelector:
    _cache: dict[tuple, tuple[ContextFile, ...]] = {}

    def __init__(self, repository: Path, roots: tuple[str, ...], excludes: tuple[str, ...],
                 max_file_bytes: int = 120_000) -> None:
        self.repository, self.roots, self.excludes = repository.resolve(), roots or (".",), excludes
        self.max_file_bytes = max_file_bytes

    def select(self, request: str, limit_files: int = 60, limit_bytes: int = 350_000) -> list[ContextFile]:
        terms = {term for term in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{1,}", request.lower())
                 if term not in {"the", "and", "that", "with", "from", "this"}}
        ranked: list[ContextFile] = []
        for item in self._inventory():
            relative, content = item.path, item.content
            haystack = f"{relative.lower()}\n{content[:12000].lower()}"
            score = sum((8 if term in relative.lower() else 1) * haystack.count(term)
                        for term in terms)
            if score or Path(relative).name.lower().startswith(("readme", "pyproject", "package")):
                ranked.append(ContextFile(relative, item.sha256, item.bytes, content, score))
        ranked.sort(key=lambda item: (-item.score, item.bytes, item.path))
        selected, total = [], 0
        for item in ranked:
            if len(selected) >= limit_files or total + item.bytes > limit_bytes:
                continue
            selected.append(item)
            total += item.bytes
        return selected

    def _inventory(self) -> tuple[ContextFile, ...]:
        key = self._cache_key()
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        inventory: list[ContextFile] = []
        seen: set[Path] = set()
        for root_name in self.roots:
            root = (self.repository / root_name).resolve()
            if not root.exists():
                continue
            if root.is_file():
                paths = [root]
            else:
                paths = []
                ignored = {
                    ".git", ".maintain", ".venv", ".mypy_cache", ".pytest_cache",
                    ".ruff_cache", ".tox", ".nox", ".next", "node_modules", "vendor",
                    "dist", "build", "coverage", "target", "__pycache__",
                }
                for directory, dirs, files in os.walk(root):
                    kept = []
                    for name in sorted(dirs):
                        candidate = Path(directory) / name
                        relative_dir = candidate.relative_to(self.repository).as_posix()
                        if (name not in ignored and
                                not any(fnmatch.fnmatch(relative_dir, pattern)
                                        for pattern in self.excludes)):
                            kept.append(name)
                    dirs[:] = kept
                    paths.extend(Path(directory) / name for name in sorted(files))
            for path in paths:
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                try:
                    resolved.relative_to(self.repository)
                except ValueError:
                    continue
                relative = path.relative_to(self.repository).as_posix()
                if (path.is_symlink() or secret_file(path)
                        or any(fnmatch.fnmatch(relative, x) for x in self.excludes)):
                    continue
                try:
                    raw = path.read_bytes()
                    if len(raw) > self.max_file_bytes or b"\x00" in raw:
                        continue
                    content = raw.decode("utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                inventory.append(ContextFile(relative, hashlib.sha256(raw).hexdigest(),
                                             len(raw), content, 0))
        result = tuple(inventory)
        self._cache[key] = result
        while len(self._cache) > 8:
            self._cache.pop(next(iter(self._cache)))
        return result

    def _cache_key(self) -> tuple:
        result = subprocess.run(
            ["git", "-C", str(self.repository), "rev-parse", "HEAD^{tree}"],
            text=True, capture_output=True, check=False,
        )
        tree = result.stdout.strip() if result.returncode == 0 else "no-git-tree"
        return (str(self.repository.resolve()), tree, self.roots, self.excludes, self.max_file_bytes)

    def exact(self, paths: set[str]) -> list[ContextFile]:
        """Return exact safe inventory entries for bounded context expansion."""
        requested = set(paths)
        return [item for item in self._inventory() if item.path in requested]

    def repository_text_bytes(self) -> int:
        return sum(item.bytes for item in self._inventory())

    def repository_map(self) -> list[dict[str, object]]:
        """Return a content-free path index so the assistant can request precise expansion."""
        return [{"path": item.path, "bytes": item.bytes, "sha256": item.sha256}
                for item in self._inventory()]
