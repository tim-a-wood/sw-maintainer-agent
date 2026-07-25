"""Objective local command execution with bounded evidence."""
from __future__ import annotations
import os, subprocess, time
from datetime import datetime, timezone
from pathlib import Path
from .models import CommandResult, VerificationCommand

def run_command(spec:VerificationCommand, repository:Path)->CommandResult:
    root=Path(repository).resolve(); cwd=(root/spec.working_directory).resolve(); cwd.relative_to(root)
    env=os.environ.copy(); env.update(dict(spec.environment)); start=datetime.now(timezone.utc); tick=time.monotonic()
    timed_out=False
    try:
        p=subprocess.run(list(spec.command),cwd=cwd,env=env,text=False,capture_output=True,timeout=spec.timeout_seconds,check=False)
        code=p.returncode; out=p.stdout; err=p.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out=True; code=None; out=exc.stdout or b""; err=exc.stderr or b""
    end=datetime.now(timezone.utc)
    ot=len(out)>spec.max_stdout_bytes; et=len(err)>spec.max_stderr_bytes
    stdout=out[:spec.max_stdout_bytes].decode("utf-8",errors="replace"); stderr=err[:spec.max_stderr_bytes].decode("utf-8",errors="replace")
    return CommandResult(spec.id,spec.command,str(cwd),start.isoformat(),end.isoformat(),time.monotonic()-tick,code,timed_out,stdout,stderr,ot,et,not timed_out and code in spec.expected_exit_codes)
