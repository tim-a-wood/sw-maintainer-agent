"""Isolated Git worktrees and diff policy."""

from __future__ import annotations

import fnmatch
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

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
    def __init__(self, repository: Path, workspace_root: Path) -> None:
        self.repository = repository.resolve()
        self.workspace_root = workspace_root.resolve()

    @property
    def repository_lock(self) -> Path:
        common = Path(git(self.repository, "rev-parse", "--git-common-dir"))
        if not common.is_absolute():
            common = self.repository / common
        return common.resolve() / "maintain-workspace.lock"

    def preflight(self) -> str:
        if git(self.repository, "status", "--porcelain"):
            raise PolicyError("The source repository has uncommitted changes.")
        return git(self.repository, "rev-parse", "HEAD")

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
            if Path(path).name in dependency_files and dependency_changes != "allow":
                action = "approval" if dependency_changes == "approval" else "policy"
                raise PolicyError(f"Dependency change needs {action}: {path}")

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
        if git(self.repository, "status", "--porcelain"):
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
