"""Configuration-driven providers."""

from pathlib import Path

from .errors import ConfigurationError
from .providers import (ChatGPTBrowserProvider, CodexProvider, CommandProvider,
                        FileExchangeProvider, M365CopilotBrowserProvider, Provider)
from .providers import OpenAIResponsesProvider


def build_provider(name: str, config: dict, evidence_dir: Path) -> Provider:
    kind = config.get("type")
    if kind == "codex_cli":
        return CodexProvider(str(config.get("executable", "")), str(config.get("model", "")),
                             int(config.get("timeout_seconds", 900)), evidence_dir)
    if kind == "command":
        return CommandProvider(name, list(config.get("argv", [])), int(config.get("timeout_seconds", 900)))
    if kind == "file_exchange":
        return FileExchangeProvider(name, Path(str(config["exchange_dir"])).expanduser(),
                                    int(config.get("timeout_seconds", 3600)))
    if kind == "m365_copilot_browser":
        return M365CopilotBrowserProvider(config, evidence_dir)
    if kind == "chatgpt_browser":
        return ChatGPTBrowserProvider(config, evidence_dir)
    if kind == "openai_responses":
        return OpenAIResponsesProvider(config)
    raise ConfigurationError(f"Unknown provider type for {name!r}: {kind!r}")
