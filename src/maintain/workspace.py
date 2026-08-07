"""Isolated Git worktrees and diff policy."""

from __future__ import annotations

import fnmatch
import os
import subprocess
import tempfile
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .errors import PolicyError, RecoveryError
from .proc import hidden


def git(repository: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", "-C", str(repository), *args], text=True,
                            encoding="utf-8", errors="replace",
                            capture_output=True, check=False, **hidden())
    if check and result.returncode:
        raise RecoveryError((result.stderr or result.stdout).strip() or "Git command failed.")
    return result.stdout.strip()


def _err(completed) -> str:
    return completed.stderr.decode("utf-8", "replace").strip()


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
            raise PolicyError(
                "The project has uncommitted changes. Commit them or "
                "remove them, then start again.")
        return git(self.repository, "rev-parse", "HEAD")

    def _source_status(self) -> str:
        arguments = ["status", "--porcelain", "--", "."]
        arguments.extend(f":(exclude){path}" for path in self.ignored_source_paths)
        return git(self.repository, *arguments)

    def create(self, run_id: str, base_commit: str) -> tuple[str, Path]:
        """The run works in the person's own checkout, on their branch.

        An isolated worktree kept the project files untouched until an
        extra merge step. In use, that step is where the work looked
        lost, and the merge itself became the difficulty. The run now
        edits the files the person can see, and its commit lands on
        the branch they chose.
        """
        branch = self.current_branch()
        if not branch:
            raise RecoveryError(
                "The project is not on a branch. Move to a branch, then start again.")
        if self._source_status():
            raise RecoveryError("The project has changes that are not committed.")
        head = git(self.repository, "rev-parse", "HEAD")
        if head != base_commit:
            raise RecoveryError("The branch moved after this run started.")
        return branch, self.repository

    def current_branch(self) -> str:
        return git(self.repository, "branch", "--show-current", check=False)

    def in_place(self, worktree: Path) -> bool:
        """True when the run works in the person's own checkout."""
        try:
            return Path(worktree).resolve() == self.repository
        except OSError:
            return False

    def restore_base(self, worktree: Path, base_commit: str) -> None:
        """Return the files to the commit the run started from.

        A discarded run must leave nothing behind. Everything after
        the start belongs to the run, because the start refused to
        begin with changes that were not committed.
        """
        if not base_commit:
            return
        git(worktree, "reset", "--hard", base_commit)
        self.clean(worktree)

    def restore_tree(self, worktree: Path, tree_hash: str) -> None:
        """Return the worktree files to a recorded tree without moving HEAD.

        The tree objects come from the diff snapshots (git write-tree), so they
        stay in the shared object database for the life of the run.
        """
        git(worktree, "reset", "--hard", "HEAD")
        self.clean(worktree)
        if not tree_hash or tree_hash == git(worktree, "rev-parse", "HEAD^{tree}"):
            return
        removed = git(worktree, "diff-tree", "-r", "--name-only", "--diff-filter=D",
                      "HEAD", tree_hash)
        env = os.environ.copy()
        with tempfile.TemporaryDirectory(prefix="maintain-restore-") as directory:
            env["GIT_INDEX_FILE"] = str(Path(directory) / "index")
            subprocess.run(["git", "-C", str(worktree), "read-tree", tree_hash],
                           env=env, check=True, capture_output=True, **hidden())
            subprocess.run(["git", "-C", str(worktree), "checkout-index", "-f", "-a"],
                           env=env, check=True, capture_output=True, **hidden())
        for line in removed.splitlines():
            name = line.strip()
            target = worktree / name
            if name and target.is_file():
                target.unlink()

    def clean(self, worktree: Path) -> None:
        """Remove untracked files, but never Maintain's own settings.

        The run works in the person's checkout, where .maintain.json
        and the prompt files usually are not committed. A plain clean
        would delete the project's configuration.
        """
        arguments = ["clean", "-fd"]
        for path in self.ignored_source_paths:
            arguments.extend(["-e", path])
        git(worktree, *arguments)

    def _stage_paths(self) -> list[str]:
        """The pathspec that stages the code, and nothing else.

        The run works in the person's own checkout now, so Maintain's
        own files sit beside the code. Without these excludes they
        would read as an implementation that edited the settings.
        """
        return [".", *(f":(exclude){path}"
                       for path in self.ignored_source_paths)]

    def diff(self, worktree: Path) -> DiffEvidence:
        text, paths, tree, statuses = self._snapshot(worktree, self._stage_paths())
        for relative in paths:
            candidate = worktree / relative
            if candidate.is_symlink():
                raise PolicyError(f"The implementation created or changed a symbolic link: {relative}")
        return DiffEvidence(text=text, paths=paths, bytes=len(text.encode()), tree_hash=tree,
                            statuses=statuses)

    @staticmethod
    def _snapshot(worktree: Path, stage: list[str] | None = None
                  ) -> tuple[str, tuple[str, ...], str, tuple[tuple[str, str], ...]]:
        env = os.environ.copy()

        def run(*arguments: str) -> str:
            """FR-V13: git's own words, not a traceback.

            These calls used check=True, so a failure raised
            CalledProcessError, whose message is only the command and
            the exit status. The person saw a wall of Python and no
            cause. git writes the cause to stderr; it belongs in the
            error.
            """
            result = subprocess.run(
                ["git", "-C", str(worktree), *arguments], env=env,
                text=True, encoding="utf-8", errors="replace",
                check=False, capture_output=True, **hidden())
            if result.returncode:
                cause = (result.stderr or result.stdout).strip()
                raise RecoveryError(
                    f"git {arguments[0]} could not read your project files. "
                    f"{cause}" if cause else
                    f"git {arguments[0]} could not read your project files.")
            return result.stdout

        with tempfile.TemporaryDirectory(prefix="maintain-index-") as directory:
            env["GIT_INDEX_FILE"] = str(Path(directory) / "index")
            run("read-tree", "HEAD")
            run("add", "-A", "--", *(stage or ["."]))
            tree = run("write-tree").strip()
            text = run("diff", "--cached", "--binary", "HEAD")
            raw_status = run("diff", "--cached", "--name-status", "HEAD")
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
        checked = self._git_apply(worktree, patch, "--check")
        if checked.returncode:
            raise PolicyError(
                f"The patch is invalid: {_err(checked)}")
        applied = self._git_apply(worktree, patch)
        if applied.returncode:
            raise PolicyError(
                f"The patch could not be applied: {_err(applied)}")

    def apply_patch_idempotent(self, worktree: Path, patch: str) -> bool:
        """Apply a patch once. Return False when the exact patch is already present."""
        checked = self._git_apply(worktree, patch, "--check")
        if checked.returncode == 0:
            self.apply_patch(worktree, patch)
            return True
        reverse = self._git_apply(worktree, patch, "--reverse", "--check")
        if reverse.returncode == 0:
            return False
        raise PolicyError(
            "The patch does not apply to the current workspace: "
            f"{_err(checked)}")

    @staticmethod
    def _git_apply(worktree: Path, patch: str, *flags: str):
        # The patch feeds the pipe as bytes: Windows text mode would
        # turn \n into \r\n and git apply would refuse every hunk.
        # --ignore-whitespace lets an LF patch land in the CRLF
        # worktrees that Windows checkouts produce.
        return subprocess.run(
            ["git", "-C", str(worktree), "apply", "--ignore-whitespace",
             *flags, "-"],
            input=patch.encode("utf-8"), capture_output=True,
            check=False, **hidden())

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
                archive_items = [
                    item for item in bundle.infolist() if not item.is_dir()]
                archive_names = [item.filename for item in archive_items]
                if len(archive_names) != len(set(archive_names)):
                    raise PolicyError(
                        "The implementation ZIP contains a duplicate member.")
                structured = "IMPLEMENTATION.toml" in archive_names
                declared_files: set[str] | None = None
                if structured:
                    manifest_info = bundle.getinfo("IMPLEMENTATION.toml")
                    if manifest_info.flag_bits & 0x1:
                        raise PolicyError(
                            "The implementation ZIP contains an encrypted manifest.")
                    if manifest_info.file_size > 65_536:
                        raise PolicyError(
                            "The implementation ZIP manifest exceeds 64 KiB.")
                    try:
                        manifest = tomllib.loads(
                            bundle.read(manifest_info).decode("utf-8"))
                    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
                        raise PolicyError(
                            "The implementation ZIP manifest is invalid TOML.") from exc
                    manifest_files = manifest.get("files")
                    if (not isinstance(manifest_files, list)
                            or any(not isinstance(path, str)
                                   for path in manifest_files)
                            or len(manifest_files) != len(set(manifest_files))):
                        raise PolicyError(
                            "The implementation ZIP manifest files are invalid.")
                    declared_files = set(manifest_files)
                    expected_names = {
                        "IMPLEMENTATION.toml",
                        *(f"files/{path}" for path in declared_files),
                    }
                    if set(archive_names) != expected_names:
                        raise PolicyError(
                            "The implementation ZIP members do not match its manifest.")
                for item in archive_items:
                    if structured and item.filename == "IMPLEMENTATION.toml":
                        continue
                    if item.is_dir():
                        continue
                    if item.flag_bits & 0x1:
                        raise PolicyError("The implementation ZIP contains an encrypted file.")
                    if "\\" in item.filename:
                        raise PolicyError("The implementation ZIP contains a non-portable path.")
                    member_name = (
                        item.filename.removeprefix("files/")
                        if structured else item.filename
                    )
                    relative = PurePosixPath(member_name)
                    if (relative.is_absolute() or not relative.parts or
                            any(part in {"", ".", ".."} for part in relative.parts)):
                        raise PolicyError("The implementation ZIP contains an unsafe path.")
                    path = relative.as_posix()
                    if declared_files is not None and path not in declared_files:
                        raise PolicyError(
                            "The implementation ZIP contains an undeclared file.")
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
        git(worktree, "add", "-A", "--", *self._stage_paths())
        if git(worktree, "write-tree") != expected_tree:
            raise PolicyError("The accepted tree changed before delivery.")
        git(worktree, "commit", "-m", message)
        if git(worktree, "show", "-s", "--format=%T", "HEAD") != expected_tree:
            raise PolicyError("The delivered commit does not match the accepted tree.")
        return git(worktree, "rev-parse", "HEAD")

    def push(self, branch: str) -> dict:
        """Send the branch to its remote, and never stop the run.

        A project with no remote, or a machine with no network, is a
        normal state. The result is words for the person, not a fault.
        """
        remotes = git(self.repository, "remote", check=False).split()
        if not remotes:
            return {"pushed": False, "reason": "no_remote"}
        tracked = git(self.repository, "rev-parse", "--abbrev-ref",
                      f"{branch}@{{upstream}}", check=False)
        remote = tracked.split("/", 1)[0] if tracked else (
            "origin" if "origin" in remotes else remotes[0])
        arguments = ["push"] + ([] if tracked else ["-u"]) + [remote, branch]
        result = subprocess.run(["git", "-C", str(self.repository), *arguments],
                                text=True, encoding="utf-8", errors="replace",
                                capture_output=True, check=False, **hidden())
        if result.returncode:
            return {"pushed": False, "reason": "refused", "remote": remote,
                    "error": (result.stderr or result.stdout).strip()}
        return {"pushed": True, "remote": remote}

    def state(self) -> dict:
        """A small picture of the repository, for the person to read."""
        branch = self.current_branch()
        # Maintain's own files are not the person's work, and a run
        # does not refuse because of them. They do not count here.
        changed = [line for line in self._source_status().splitlines()
                   if line.strip()]
        ahead = behind = 0
        tracked = git(self.repository, "rev-parse", "--abbrev-ref",
                      f"{branch}@{{upstream}}", check=False) if branch else ""
        if tracked:
            counts = git(self.repository, "rev-list", "--left-right", "--count",
                         f"{tracked}...{branch}", check=False).split()
            if len(counts) == 2:
                behind, ahead = int(counts[0]), int(counts[1])
        return {"branch": branch, "changed": len(changed), "tracked": tracked,
                "ahead": ahead, "behind": behind,
                "has_remote": bool(git(self.repository, "remote",
                                       check=False).split())}

    def branches(self) -> list[str]:
        """The local branch names, current one first."""
        names = [line.strip() for line in
                 git(self.repository, "branch", "--format=%(refname:short)",
                     check=False).splitlines() if line.strip()]
        current = self.current_branch()
        return ([current] if current in names else []) + [
            name for name in names if name != current]

    def switch_branch(self, branch: str, create: bool = False) -> None:
        """Move the checkout to another branch, with the tree clean."""
        if self._source_status():
            raise RecoveryError("The project has changes that are not committed.")
        arguments = ["checkout"] + (["-b"] if create else []) + [branch]
        result = subprocess.run(["git", "-C", str(self.repository), *arguments],
                                text=True, encoding="utf-8", errors="replace",
                                capture_output=True, check=False, **hidden())
        if result.returncode:
            raise RecoveryError((result.stderr or result.stdout).strip()
                                or "The branch could not be opened.")

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
            text=True, capture_output=True, check=False, **hidden(),
        )
        if result.returncode:
            raise RecoveryError((result.stderr or result.stdout).strip() or
                                "The target branch cannot be fast-forwarded.")
        return git(self.repository, "rev-parse", "HEAD")
