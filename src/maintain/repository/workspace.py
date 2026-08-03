"""Atomic implementation attempt worktrees."""
from __future__ import annotations
import shutil, subprocess, tempfile
from pathlib import Path
from ..artifacts.contracts import ImplementationArtifact
from ..proc import hidden
from ..artifacts.validation import validate_repository_path
from ..workspace import DiffEvidence, WorkspaceManager, git

class AttemptWorkspace:
    def __init__(self, repository:Path, run_worktree:Path, root:Path):
        self.repository=Path(repository).resolve(); self.run_worktree=Path(run_worktree).resolve(); self.root=Path(root).resolve()
    def apply(self, artifact:ImplementationArtifact, *, allowed:set[tuple[str,str]], protected:tuple[str,...], excluded:tuple[str,...],
              allow_new_files:bool, allow_deletes:bool, max_files:int, max_diff_bytes:int)->DiffEvidence:
        base=git(self.run_worktree,"rev-parse","HEAD"); self.root.mkdir(parents=True,exist_ok=True)
        attempt=Path(tempfile.mkdtemp(prefix="attempt-",dir=self.root)); branch=f"maintain-attempt-{attempt.name}"
        try:
            r=subprocess.run(["git","-C",str(self.repository),"worktree","add","--detach",str(attempt),base],text=True,encoding="utf-8",errors="replace",capture_output=True,**hidden())
            if r.returncode: raise RuntimeError((r.stderr or r.stdout).strip())
            declared=[]
            for op in artifact.operations:
                rel=validate_repository_path(op.path,protected=protected,excluded=excluded)
                if (op.operation,rel) not in allowed: raise ValueError(f"Unauthorized operation: {op.operation} {rel}")
                target=attempt/rel; declared.append(rel)
                if op.operation=="add":
                    if target.exists() or not allow_new_files: raise ValueError(f"Add prohibited or path exists: {rel}")
                    target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(op.staged_content.read_bytes())
                elif op.operation=="modify":
                    if not target.is_file(): raise ValueError(f"Modify path is missing: {rel}")
                    target.write_bytes(op.staged_content.read_bytes())
                else:
                    if not target.is_file() or not allow_deletes: raise ValueError(f"Delete prohibited or path missing: {rel}")
                    target.unlink()
            manager=WorkspaceManager(self.repository,self.root)
            diff=manager.diff(attempt)
            manager.validate(diff,declared,protected+excluded,max_files,max_diff_bytes,allow_new_files=allow_new_files,allow_deletes=allow_deletes)
            if set(diff.paths)!=set(declared): raise ValueError("Actual changed paths differ from declared operations.")
            return diff
        finally:
            subprocess.run(["git","-C",str(self.repository),"worktree","remove","--force",str(attempt)],capture_output=True,**hidden())
            shutil.rmtree(attempt,ignore_errors=True)
