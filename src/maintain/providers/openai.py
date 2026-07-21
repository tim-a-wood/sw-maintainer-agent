"""Optional OpenAI Responses API provider.

This provider is intentionally separate from ``chatgpt_browser``. It uses only
the configured API key and the documented Responses endpoint.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import asdict
from typing import Any

from maintain.errors import ProviderError
from maintain.models import ProviderCapabilities, ProviderRequest, ProviderResponse

from .base import Provider


class OpenAIResponsesProvider(Provider):
    name = "openai_responses"
    capabilities = ProviderCapabilities()

    def __init__(self, config: dict[str, Any], opener: Any = None) -> None:
        self.model = str(config.get("model") or "")
        self.endpoint = str(config.get("endpoint") or "https://api.openai.com/v1/responses")
        self.api_key = os.environ.get(str(config.get("api_key_env") or "OPENAI_API_KEY"), "")
        self.timeout_seconds = int(config.get("timeout_seconds", 300))
        self.store = bool(config.get("store", False))
        self._opener = opener or urllib.request.urlopen

    def preflight(self) -> None:
        if not self.api_key:
            raise ProviderError("The configured OpenAI API key environment variable is empty.")
        if not self.model:
            raise ProviderError("The OpenAI Responses provider needs an explicit model.")
        if self.endpoint != "https://api.openai.com/v1/responses":
            raise ProviderError("The OpenAI Responses endpoint is not approved.")

    def exchange(self, request: ProviderRequest) -> ProviderResponse:
        schema = {
            "type": "object", "additionalProperties": False,
            "properties": {
                "schema_version": {"type": "integer"}, "run_id": {"type": "string"},
                "task_id": {"type": "string"}, "role": {"type": "string"},
                "content_json": {"type": "string"}, "conversation_id": {"type": "string"},
            },
            "required": ["schema_version", "run_id", "task_id", "role", "content_json",
                         "conversation_id"],
        }
        body = {
            "model": self.model,
            "instructions": request.instructions +
                " Return the response content as JSON encoded in content_json.",
            "input": json.dumps(asdict(request), ensure_ascii=False),
            "store": self.store,
            "tools": [],
            "text": {"format": {"type": "json_schema", "name": "maintain_envelope",
                                  "strict": True, "schema": schema}},
        }
        outbound = urllib.request.Request(
            self.endpoint, data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        try:
            with self._opener(outbound, timeout=self.timeout_seconds) as response:
                raw = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[-600:]
            raise ProviderError(f"OpenAI returned HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise ProviderError(f"OpenAI Responses failed: {exc}") from exc
        text = _output_text(raw)
        try:
            envelope = json.loads(text)
            content = json.loads(envelope.pop("content_json"))
            result = ProviderResponse(provider=self.name, content=content, **envelope)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ProviderError("OpenAI returned an invalid response envelope.") from exc
        if (result.schema_version, result.run_id, result.task_id, result.role) != (
                request.schema_version, request.run_id, request.task_id, request.role):
            raise ProviderError("OpenAI returned an envelope for a different task.")
        return result


def _output_text(response: dict[str, Any]) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str) and direct:
        return direct
    chunks = [str(part.get("text", "")) for item in response.get("output", [])
              if isinstance(item, dict) and item.get("type") == "message"
              for part in item.get("content", [])
              if isinstance(part, dict) and part.get("type") == "output_text"]
    if not chunks:
        raise ProviderError("OpenAI returned no output text.")
    return "".join(chunks)
