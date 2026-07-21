"""Stable provider contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from maintain.models import ProviderCapabilities, ProviderRequest, ProviderResponse


class Provider(ABC):
    name = "provider"
    capabilities = ProviderCapabilities()

    @abstractmethod
    def exchange(self, request: ProviderRequest) -> ProviderResponse:
        """Return one validated response for one isolated workflow role."""

    def preflight(self) -> None:
        """Fail before repository mutation when the provider is unavailable."""

    def exchange_in_workspace(self, request: ProviderRequest, worktree: Path) -> ProviderResponse:
        """Implement directly in an isolated worktree when capability permits it."""
        raise NotImplementedError("This provider does not edit a workspace.")
