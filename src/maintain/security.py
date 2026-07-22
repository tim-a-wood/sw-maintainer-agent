"""Fail-closed checks for outbound secrets and unsafe repository paths."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .errors import PolicyError

SECRET_FILE_NAMES = {".env", ".env.local", ".maintain.json", ".npmrc", ".pypirc",
                     "id_rsa", "id_ed25519",
                     "credentials", "credentials.json"}
SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}
SECRET_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.I),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password)\b\s*[:=]\s*[\"']?[A-Za-z0-9._~+/=-]{12,}"),
]


def secret_file(path: Path) -> bool:
    return path.name.lower() in SECRET_FILE_NAMES or path.suffix.lower() in SECRET_SUFFIXES


def assert_no_secrets(value: Any, location: str = "payload") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            assert_no_secrets(item, f"{location}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            assert_no_secrets(item, f"{location}[{index}]")
    elif isinstance(value, str):
        for pattern in SECRET_PATTERNS:
            if pattern.search(value):
                raise PolicyError(f"Possible secret detected in {location}. Remove or redact it.")
