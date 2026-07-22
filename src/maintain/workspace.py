"""Isolated Git worktrees and diff policy."""

from __future__ import annotations

import fnmatch
import os
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .errors import PolicyError, RecoveryError


def git(repository: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", "-C", str(repository), *args], text=True,
                            capture_output=True, check=False)
    if check and result.returncode:
        raise RecoveryError((result.stderr or result.stdout).strip() or "Git command failed.")
    return result.stdout.strip()


@dataclass(frozen=True)
class DiffEvidence:
    text: str
    paths: tuple[str, ...]
    bytes: int
    tree_hash: str
    statuses: tuple[tuple[str, str], ...] = ()


class WorkspaceManager:
    def __init__(self, repository: Path, workspace_root: Path,
                 ignored_source_paths: tuple[str, ...] = ()) -> None:
        self.repository = repository.resolve()
        self.workspace_root = workspace_root.resolve()
        self.ignored_source_paths = ignored_source_paths

    @property
    def repository_lock(self) -> Path:
        common = Path(git(self.repository, "rev-parse", "--git-common-dir"))
        if not common.is_absolute():
            common = self.repository / common
        return common.resolve() / "maintain-workspace.lock"

    def preflight(self) -> str:
        if self._source_status():
            raise PolicyError("The source repository has uncommitted changes.")
        return git(self.repository, "rev-parse", "HEAD")

    def _source_status(self) -> str:
        arguments = ["status", "--porcelain", "--", "."]
        arguments.extend(f":(exclude){path}" for path in self.ignored_source_paths)
        return git(self.repository, *arguments)

    def create(self, run_id: str, base_commit: str) -> tuple[str, Path]:
        branch = f"maintain/{run_id}"
        worktree = self.workspace_root / run_id
        worktree.parent.mkdir(parents=True, exist_ok=True)
        if worktree.exists():
            if (git(worktree, "rev-parse", "HEAD") == base_commit and
                    git(worktree, "branch", "--show-current") == branch):
                return branch, worktree
            raise RecoveryError(f"Worktree already exists with unexpected state: {worktree}")
        result = subprocess.run(["git", "-C", str(self.repository), "worktree", "add", "-b",
                                 branch, str(worktree), base_commit], text=True,
                                capture_output=True, check=False)
        if result.returncode:
            raise RecoveryError((result.stderr or result.stdout).strip())
        return branch, worktree

    def diff(self, worktree: Path) -> DiffEvidence:
        text, paths, tree, statuses = self._snapshot(worktree)
        for relative in paths:
            candidate = worktree / relative
            if candidate.is_symlink():
                raise PolicyError(f"The implementation created or changed a symbolic link: {relative}")
        return DiffEvidence(text=text, paths=paths, bytes=len(text.encode()), tree_hash=tree,
                            statuses=statuses)

    @staticmethod
    def _snapshot(worktree: Path) -> tuple[str, tuple[str, ...], str, tuple[tuple[str, str], ...]]:
        env = os.environ.copy()
        with tempfile.TemporaryDirectory(prefix="maintain-index-") as directory:
            env["GIT_INDEX_FILE"] = str(Path(directory) / "index")
            subprocess.run(["git", "-C", str(worktree), "read-tree", "HEAD"], env=env,
                           check=True, capture_output=True)
            subprocess.run(["git", "-C", str(worktree), "add", "-A"], env=env,
                           check=True, capture_output=True)
            tree = subprocess.run(["git", "-C", str(worktree), "write-tree"], env=env,
                                  text=True, check=True, capture_output=True).stdout.strip()
            text = subprocess.run(["git", "-C", str(worktree), "diff", "--cached", "--binary", "HEAD"],
                                  env=env, text=True, check=True, capture_output=True).stdout
            raw_status = subprocess.run(
                ["git", "-C", str(worktree), "diff", "--cached", "--name-status", "HEAD"],
                env=env, text=True, check=True, capture_output=True).stdout
            statuses = []
            names = []
            for line in raw_status.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2:
                    changed_paths = parts[1:] if parts[0][0] in {"R", "C"} else parts[-1:]
                    names.extend(changed_paths)
                    statuses.extend((parts[0], path) for path in changed_paths)
            return text.strip(), tuple(dict.fromkeys(names)), tree, tuple(statuses)

    @staticmethod
    def validate(diff: DiffEvidence, allowed: list[str], protected: tuple[str, ...],
                 max_files: int, max_bytes: int, *, allow_new_files: bool = True,
                 allow_deletes: bool = False, dependency_changes: str = "approval") -> None:
        if not diff.paths:
            raise PolicyError("The implementation did not change a file.")
        if len(diff.paths) > max_files or diff.bytes > max_bytes:
            raise PolicyError("The implementation exceeds the configured diff limit.")
        for path in diff.paths:
            if any(fnmatch.fnmatch(path, pattern) for pattern in protected):
                raise PolicyError(f"The implementation changed a protected path: {path}")
            if allowed and path not in allowed:
                raise PolicyError(f"The implementation changed a path outside the task: {path}")
        if "GIT binary patch" in diff.text or "Subproject commit" in diff.text:
            raise PolicyError("Binary and submodule changes need explicit human handling.")
        dependency_files = {"pyproject.toml", "requirements.txt", "requirements.lock",
                            "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
                            "Cargo.toml", "Cargo.lock", "go.mod", "go.sum", "pom.xml"}
        for status, path in diff.statuses:
            code = status[0]
            if code == "A" and not allow_new_files:
                raise PolicyError(f"New files are not permitted: {path}")
            if code == "D" and not allow_deletes:
                raise PolicyError(f"File deletion is not permitted: {path}")
            if code == "R" and not (allow_new_files and allow_deletes):
                raise PolicyError(
                    f"A rename needs both new-file and deletion permission: {path}")
            if code == "C":
                raise PolicyError(f"Copy changes need human handling: {path}")
            if Path(path).name in dependency_files and dependency_changes == "deny":
                raise PolicyError(f"Dependency changes are not permitted: {path}")

    def apply_patch(self, worktree: Path, patch: str) -> None:
        if not patch.strip():
            raise PolicyError("The provider returned an empty patch.")
        checked = subprocess.run(["git", "-C", str(worktree), "apply", "--check", "-"],
                                 input=patch, text=True, capture_output=True, check=False)
        if checked.returncode:
            raise PolicyError(f"The patch is invalid: {checked.stderr.strip()}")
        applied = subprocess.run(["git", "-C", str(worktree), "apply", "-"], input=patch,
                                 text=True, capture_output=True, check=False)
        if applied.returncode:
            raise PolicyError(f"The patch could not be applied: {applied.stderr.strip()}")

    def apply_patch_idempotent(self, worktree: Path, patch: str) -> bool:
        """Apply a patch once. Return False when the exact patch is already present."""
        checked = subprocess.run(["git", "-C", str(worktree), "apply", "--check", "-"],
                                 input=patch, text=True, capture_output=True, check=False)
        if checked.returncode == 0:
            self.apply_patch(worktree, patch)
            return True
        reverse = subprocess.run(["git", "-C", str(worktree), "apply", "--reverse", "--check", "-"],
                                 input=patch, text=True, capture_output=True, check=False)
        if reverse.returncode == 0:
            return False
        raise PolicyError(f"The patch does not apply to the current workspace: {checked.stderr.strip()}")

    @staticmethod
    def apply_output_zip(worktree: Path, archive: Path, allowed: list[str],
                         max_file_bytes: int, max_total_bytes: int, *,
                         allow_empty: bool = False) -> tuple[str, ...]:
        """Safely apply complete repository files from an assistant ZIP."""
        worktree = worktree.resolve()
        allowed_paths = set(allowed)
        members: list[tuple[zipfile.ZipInfo, str]] = []
        total = 0
        try:
            with zipfile.ZipFile(archive) as bundle:
                for item in bundle.infolist():
                    if item.is_dir():
                        continue
                    if item.flag_bits & 0x1:
                        raise PolicyError("The implementation ZIP contains an encrypted file.")
                    if "\\" in item.filename:
                        raise PolicyError("The implementation ZIP contains a non-portable path.")
                    relative = PurePosixPath(item.filename)
                    if (relative.is_absolute() or not relative.parts or
                            any(part in {"", ".", ".."} for part in relative.parts)):
                        raise PolicyError("The implementation ZIP contains an unsafe path.")
                    path = relative.as_posix()
                    if path not in allowed_paths:
                        raise PolicyError(
                            f"The implementation ZIP contains a file outside the task: {path}")
                    mode = (item.external_attr >> 16) & 0o170000
                    if mode == 0o120000:
                        raise PolicyError(
                            f"The implementation ZIP contains a symbolic link: {path}")
                    if item.file_size > max_file_bytes:
                        raise PolicyError(
                            f"The implementation ZIP file exceeds the size limit: {path}")
                    total += item.file_size
                    if total > max_total_bytes:
                        raise PolicyError("The implementation ZIP exceeds the size limit.")
                    if any(existing == path for _, existing in members):
                        raise PolicyError(
                            f"The implementation ZIP contains a duplicate file: {path}")
                    members.append((item, path))
                if not members and not allow_empty:
                    raise PolicyError("The implementation ZIP contains no repository files.")
                for item, path in members:
                    destination = worktree / Path(path)
                    current = worktree
                    for part in Path(path).parts[:-1]:
                        current /= part
                        if current.is_symlink():
                            raise PolicyError(
                                f"The implementation ZIP targets a symbolic-link directory: {path}")
                    if destination.is_symlink():
                        raise PolicyError(
                            f"The implementation ZIP targets a symbolic link: {path}")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(bundle.read(item))
        except zipfile.BadZipFile as exc:
            raise PolicyError("The implementation output is not a valid ZIP file.") from exc
        return tuple(path for _, path in members)

    @staticmethod
    def apply_deletions(worktree: Path, deleted: list[str], allowed: list[str]) -> tuple[str, ...]:
        """Delete only explicitly declared, task-scoped regular files."""
        root = worktree.resolve()
        scoped = set(allowed)
        applied: list[str] = []
        for path in deleted:
            if path not in scoped:
                raise PolicyError(f"The implementation deletes a file outside the task: {path}")
            destination = root / path
            try:
                destination.resolve().relative_to(root)
            except ValueError as exc:
                raise PolicyError(f"The implementation deletion has an unsafe path: {path}") from exc
            if destination.is_symlink() or not destination.is_file():
                raise PolicyError(f"The implementation cannot delete this path: {path}")
            destination.unlink()
            applied.append(path)
        return tuple(applied)

    def commit(self, worktree: Path, message: str, expected_tree: str) -> str:
        current_head = git(worktree, "rev-parse", "HEAD")
        current_tree = git(worktree, "show", "-s", "--format=%T", "HEAD")
        if current_tree == expected_tree:
            return current_head
        git(worktree, "add", "-A")
        if git(worktree, "write-tree") != expected_tree:
            raise PolicyError("The accepted tree changed before delivery.")
        git(worktree, "commit", "-m", message)
        if git(worktree, "show", "-s", "--format=%T", "HEAD") != expected_tree:
            raise PolicyError("The delivered commit does not match the accepted tree.")
        return git(worktree, "rev-parse", "HEAD")

    def integrate_current_branch(self, branch: str, commit: str, expected_base: str) -> str:
        """Fast-forward the checked-out source branch after explicit confirmation."""
        if self._source_status():
            raise RecoveryError("The target working tree is not clean.")
        current_branch = git(self.repository, "branch", "--show-current")
        if current_branch != branch:
            raise RecoveryError(f"The checked-out target branch is {current_branch}, not {branch}.")
        if git(self.repository, "rev-parse", "HEAD") != expected_base:
            raise RecoveryError("The target branch changed after the maintenance run started.")
        result = subprocess.run(
            ["git", "-C", str(self.repository), "merge", "--ff-only", commit],
            text=True, capture_output=True, check=False,
        )
        if result.returncode:
            raise RecoveryError((result.stderr or result.stdout).strip() or
                                "The target branch cannot be fast-forwarded.")
        return git(self.repository, "rev-parse", "HEAD")
