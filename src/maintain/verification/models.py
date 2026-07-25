from __future__ import annotations
from dataclasses import dataclass
@dataclass(frozen=True)
class VerificationCommand:
    id:str; required:bool; command:tuple[str,...]; working_directory:str="."; timeout_seconds:int=120
    expected_exit_codes:tuple[int,...]=(0,); environment:tuple[tuple[str,str],...]=(); max_stdout_bytes:int=1_000_000; max_stderr_bytes:int=1_000_000
@dataclass(frozen=True)
class CommandResult:
    id:str; argv:tuple[str,...]; working_directory:str; started_at:str; ended_at:str; duration_seconds:float
    exit_code:int|None; timed_out:bool; stdout:str; stderr:str; stdout_truncated:bool; stderr_truncated:bool; passed:bool
