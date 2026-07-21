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

TEXT_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".cs", ".cpp", ".c",
                 ".h", ".m", ".md", ".json", ".toml", ".yaml", ".yml", ".html", ".css"}


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
        terms = {term for term in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", request.lower())
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
            paths = [root] if root.is_file() else (Path(d) / n for d, dirs, files in os.walk(root)
                    for _ in [dirs.__setitem__(slice(None), [x for x in dirs if x not in {".git", ".venv", "node_modules", "dist", "build", "__pycache__"}])]
                    for n in files)
            for path in paths:
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                relative = path.relative_to(self.repository).as_posix()
                if (secret_file(path) or path.suffix.lower() not in TEXT_SUFFIXES
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

    def repository_text_bytes(self) -> int:
        return sum(item.bytes for item in self._inventory())
