"""Visible Playwright providers for M365 Copilot and ChatGPT."""

from __future__ import annotations

import json
import hashlib
import os
import re
import time
import zipfile
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

from maintain.errors import ProviderError
from maintain.exchange_package import build_exchange_package
from maintain.models import ProviderCapabilities, ProviderRequest
from maintain.locking import FileLock
from maintain.security import assert_no_secrets

from .base import Provider
from .command import parse_response


M365_ENTRY_URL = "https://copilot.cloud.microsoft/?internalredirect=M365Cloud&auth=2"
M365_APPROVED_HOSTS = {"copilot.cloud.microsoft", "m365.cloud.microsoft"}


@dataclass(frozen=True)
class BrowserLayout:
    """A recognised, supported browser presentation."""

    name: str
    provider: str
    composer: str


class BrowserProvider(Provider):
    capabilities = ProviderCapabilities(browser_automation=True, sandbox_code_execution=True)

    def __init__(self, name: str, config: dict[str, Any], evidence_dir: Path) -> None:
        self.name, self.config, self.evidence_dir = name, config, evidence_dir
        profile = str(config.get("profile_dir") or "")
        if not profile:
            raise ProviderError("The browser provider needs a dedicated profile directory.")
        self.profile_dir = Path(os.path.expandvars(profile)).expanduser().resolve()
        self._journey: list[dict[str, str]] = []
        self._status_callback: Callable[[str, str], None] | None = None
        self._expected_attachments: list[str] = []
        self._layout_name = ""

    def set_status_callback(self, callback: Callable[[str, str], None]) -> None:
        self._status_callback = callback

    def _start_journey(self) -> None:
        self._journey = []
        self._expected_attachments = []
        self._layout_name = ""
        self._mark_state("opening", "Open the configured assistant")

    def _mark_state(self, state: str, detail: str = "") -> None:
        if self._journey and self._journey[-1]["state"] == state:
            return
        self._journey.append({
            "state": state,
            "detail": detail,
            "at": datetime.now(timezone.utc).isoformat(),
        })
        shown = {
            "page_ready": ("BROWSER", "Assistant page is ready"),
            "model_confirmed": ("MODEL", "Preferred model confirmed"),
            "files_ready": ("ATTACH", "Package files are ready"),
            "request_submitted": ("SEND", "Request submitted"),
            "response_complete": ("RESPONSE", "Assistant response received"),
            "response_saved": ("SAVE", "Exchange evidence saved"),
        }.get(state)
        if shown and self._status_callback:
            self._status_callback(*shown)

    def _navigation_url(self) -> str:
        """Return the supported entry URL for the configured assistant."""
        configured = str(self.config["url"])
        parsed = urlparse(configured)
        if (self.name == "m365_copilot_browser"
                and parsed.hostname == "m365.cloud.microsoft"
                and parsed.path.rstrip("/") in {"", "/chat"}):
            return M365_ENTRY_URL
        return configured

    def _approved_hosts(self, navigation_url: str) -> set[str]:
        """Return configured hosts plus the provider's fixed service hosts."""
        defaults = ({"chatgpt.com"} if self.name == "chatgpt_browser"
                    else M365_APPROVED_HOSTS)
        allowed = {str(host).casefold() for host in defaults}
        allowed.update(
            str(host).casefold() for host in self.config.get("allowed_hosts", []))
        configured = urlparse(navigation_url).hostname or ""
        if configured:
            allowed.add(configured.casefold())
        return allowed

    def _wait_for_approved_host(self, page, allowed: set[str], timeout: int) -> str:
        """Wait for an authentication redirect chain to reach an approved service host."""
        deadline = time.monotonic() + timeout / 1_000
        actual = ""
        while time.monotonic() < deadline:
            actual = urlparse(page.url).hostname or ""
            if actual.casefold() in allowed:
                return actual
            page.wait_for_timeout(250)
        raise ProviderError(
            f"The assistant did not complete authentication on an approved host. "
            f"Last host: {actual or 'unknown'}.")

    def _open_page(self, page) -> None:
        """Open the assistant and allow a bounded Microsoft authentication redirect chain."""
        timeout = min(int(self.config.get("timeout_ms", 300_000)), 60_000)
        redirect_timeout = min(timeout, 30_000)
        navigation_url = self._navigation_url()
        allowed = self._approved_hosts(navigation_url)
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                page.goto(navigation_url, wait_until="domcontentloaded", timeout=timeout)
                self._wait_for_approved_host(page, allowed, redirect_timeout)
                return
            except ProviderError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    page.wait_for_timeout(500)
        raise ProviderError(f"The assistant page did not open: {last_error}") from last_error

    def _verify_session(self, page, selectors: dict[str, Any]) -> None:
        sign_in_selector = selectors.get("sign_in_selector")
        if sign_in_selector and any(
                node.is_visible() for node in page.locator(sign_in_selector).all()):
            raise ProviderError(
                "Interactive sign-in or MFA is required. Run maintain provider login first.")
        identity_selector = selectors.get("identity_selector")
        expected_context = _configured_value(
            self.config.get("expected_tenant") or self.config.get("expected_workspace"))
        expected_identity = _configured_value(self.config.get("expected_identity"))
        context_selector_name = (
            "tenant_selector" if self.name == "m365_copilot_browser"
            else "workspace_selector")
        context_selector = selectors.get(context_selector_name)
        if expected_context:
            if not context_selector:
                raise ProviderError("Configure a context selector for browser verification.")
            context_label = page.locator(context_selector).inner_text(timeout=30_000).strip()
            if expected_context.casefold() not in context_label.casefold():
                raise ProviderError(
                    f"The browser context does not match {expected_context!r}.")
        if expected_identity:
            if not identity_selector:
                raise ProviderError("Configure an identity selector for browser verification.")
            identity_label = page.locator(identity_selector).inner_text(timeout=30_000).strip()
            if expected_identity.casefold() not in identity_label.casefold():
                raise ProviderError(
                    f"The signed-in identity does not match {expected_identity!r}.")
        self._mark_state("workspace_confirmed", "Signed-in browser context confirmed")

    def _resolve_prompt(self, page, selectors: dict[str, Any]):
        """Resolve the visible composer after allowing the client UI to finish rendering."""
        configured = selectors.get("prompt_selector")
        timeout = min(int(self.config.get("timeout_ms", 300_000)), 30_000)
        deadline = time.monotonic() + timeout / 1_000
        visible = []
        while time.monotonic() < deadline:
            candidates = (page.locator(configured).all() if configured
                          else page.get_by_role(
                              selectors.get("prompt_role", "textbox")).all())
            visible = [node for node in candidates if node.is_visible()]
            if len(visible) == 1:
                return visible[0]
            preferred = []
            for node in visible:
                label = " ".join(filter(None, [
                    node.get_attribute("aria-label"),
                    node.get_attribute("placeholder"),
                    node.get_attribute("data-testid"),
                ])).casefold()
                if any(word in label for word in (
                        "message", "copilot", "prompt", "ask", "chat")):
                    preferred.append(node)
            if len(preferred) == 1:
                return preferred[0]
            page.wait_for_timeout(250)
        if not visible:
            raise ProviderError("The message field was not found.")
        raise ProviderError(
            "More than one possible message field was found. No browser action was taken.")

    @staticmethod
    def _control_distance(control, prompt_handle) -> int:
        return int(control.evaluate(
            """(node, prompt) => {
              const ancestors = element => {
                const found = []; let current = element;
                while (current) { found.push(current); current = current.parentElement; }
                return found;
              };
              const left = ancestors(node); const right = ancestors(prompt);
              let best = 100000;
              left.forEach((item, leftIndex) => {
                const rightIndex = right.indexOf(item);
                if (rightIndex >= 0) best = Math.min(best, leftIndex + rightIndex);
              });
              return best;
            }""",
            prompt_handle,
        ))

    def _resolve_control(self, page, selector: str | None, prompt, purpose: str,
                         *, allow_hidden: bool = False):
        if not selector:
            raise ProviderError(f"The {purpose} control is not configured.")
        candidates = [
            node for node in page.locator(selector).all()
            if allow_hidden or node.is_visible()
        ]
        if not candidates:
            raise ProviderError(f"The {purpose} control was not found.")
        prompt_handle = prompt.element_handle(
            timeout=int(self.config.get("timeout_ms", 300_000)))
        ranked = sorted(
            ((self._control_purpose_penalty(node, purpose),
              self._control_distance(node, prompt_handle), node)
             for node in candidates),
            key=lambda item: item[:2],
        )
        if len(ranked) > 1 and ranked[0][:2] == ranked[1][:2]:
            raise ProviderError(
                f"More than one possible {purpose} control was found. "
                "No browser action was taken.")
        return ranked[0][2]

    @staticmethod
    def _control_purpose_penalty(control, purpose: str) -> int:
        """Prefer a general file input over purpose-specific media inputs."""
        if purpose != "attachment":
            return 0
        accept = str(control.get_attribute("accept") or "").casefold()
        label = " ".join(filter(None, [
            control.get_attribute("aria-label"),
            control.get_attribute("data-testid"),
            control.get_attribute("name"),
        ])).casefold()
        media_only = bool(accept) and all(
            item.strip().startswith(("image/", "video/", "audio/"))
            for item in accept.split(",") if item.strip())
        return 10 if media_only or any(
            word in label for word in ("photo", "image", "camera", "video", "audio")
        ) else 0

    def _attachment_trigger(self, page, prompt, selectors: dict[str, Any]):
        """Resolve the current Copilot Add control without stale-node distance evaluation."""
        selector = selectors.get("attachment_trigger_selector")
        if not selector:
            raise ProviderError("The attachment trigger is not configured.")
        timeout = min(int(self.config.get("timeout_ms", 300_000)), 30_000)
        deadline = time.monotonic() + timeout / 1_000
        while time.monotonic() < deadline:
            visible = [node for node in page.locator(selector).all() if node.is_visible()]
            if len(visible) == 1:
                return visible[0]
            if len(visible) > 1:
                exact = [node for node in visible
                         if node.get_attribute("data-testid") == "chat-input-attach-button"]
                if len(exact) == 1:
                    return exact[0]
                raise ProviderError(
                    "More than one possible attachment trigger was found. "
                    "No browser action was taken.")
            page.wait_for_timeout(250)
        raise ProviderError("The attachment trigger was not found.")

    def _attachment_input(self, page, prompt, selectors: dict[str, Any]):
        """Return an existing file input, or None when Copilot exposes only Add."""
        selector = selectors.get("attachment_selector")
        if not selector:
            return None
        inputs = page.locator(selector).all()
        if not inputs:
            return None
        return self._resolve_control(
            page, selector, prompt, "attachment", allow_hidden=True)

    def _attach_files(self, page, prompt, paths: tuple[Path, ...] | list[Path],
                      selectors: dict[str, Any]) -> None:
        """Attach files using the POC flow, including Add-menu and file-chooser variants."""
        timeout = min(int(self.config.get("timeout_ms", 300_000)), 30_000)
        values = [str(path) for path in paths]
        file_input = self._attachment_input(page, prompt, selectors)
        if file_input is not None:
            file_input.set_input_files(values, timeout=timeout)
            self._wait_for_attachments(page, file_input, paths, selectors)
            return

        trigger = self._attachment_trigger(page, prompt, selectors)
        chooser = None
        try:
            with page.expect_file_chooser(timeout=min(timeout, 5_000)) as pending:
                trigger.click(timeout=timeout)
            chooser = pending.value
        except Exception:
            # Some M365 layouts open a menu or create a hidden input instead.
            pass
        if chooser is not None:
            chooser.set_files(values)
            self._wait_for_attachments(page, None, paths, selectors)
            return

        deadline = time.monotonic() + min(timeout, 10_000) / 1_000
        file_input = None
        actions = []
        action_selector = selectors.get("attachment_action_selector")
        while time.monotonic() < deadline:
            file_input = self._attachment_input(page, prompt, selectors)
            if file_input is not None:
                break
            actions = ([node for node in page.locator(action_selector).all()
                        if node.is_visible()] if action_selector else [])
            if actions:
                break
            page.wait_for_timeout(250)

        if file_input is not None:
            file_input.set_input_files(values, timeout=timeout)
            self._wait_for_attachments(page, file_input, paths, selectors)
            return

        if not actions:
            raise ProviderError(
                "The Add control did not expose a file chooser, file input, or upload action.")
        ranked = sorted(
            ((self._control_purpose_penalty(node, "attachment"), node) for node in actions),
            key=lambda item: item[0],
        )
        if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
            raise ProviderError(
                "More than one possible file upload action was found. No browser action was taken.")
        action = ranked[0][1]
        try:
            with page.expect_file_chooser(timeout=timeout) as pending:
                action.click(timeout=timeout)
            pending.value.set_files(values)
        except Exception:
            action.click(timeout=timeout)
            page.wait_for_function(
                "selector => document.querySelectorAll(selector).length > 0",
                arg=selectors.get("attachment_selector"), timeout=timeout)
            file_input = self._attachment_input(page, prompt, selectors)
            if file_input is None:
                raise ProviderError("The file upload action did not expose a file chooser.")
            file_input.set_input_files(values, timeout=timeout)
        self._wait_for_attachments(page, file_input, paths, selectors)

    def _recognize_page(self, page, selectors: dict[str, Any]) -> tuple[BrowserLayout, Any]:
        """Recognise a supported layout before performing any consequential action."""
        prompt = self._resolve_prompt(page, selectors)
        if selectors.get("attachment_selector"):
            if self._attachment_input(page, prompt, selectors) is None:
                self._attachment_trigger(page, prompt, selectors)
        if self.name == "chatgpt_browser":
            name = "chatgpt-current"
        else:
            toggle_selector = selectors.get("new_design_toggle_selector")
            has_toggle = bool(toggle_selector and any(
                node.is_visible() for node in page.locator(toggle_selector).all()))
            name = "m365-new" if has_toggle else "m365-classic"
        layout = BrowserLayout(name=name, provider=self.name, composer="message composer")
        self._layout_name = layout.name
        self._mark_state("page_ready", f"Recognised {layout.name}")
        return layout, prompt

    def _set_prompt_text(self, page, prompt, value: str) -> None:
        """Set text on the live M365 editable node, not its role=textbox wrapper."""
        timeout = min(int(self.config.get("timeout_ms", 300_000)), 30_000)
        expected = self._normalize_prompt_text(value)
        target = prompt.locator(
            'xpath=self::*[self::textarea or self::input or @contenteditable="true"] | '
            './/*[self::textarea or self::input or @contenteditable="true"]'
        ).first
        if not target.count():
            target = prompt
        target.click(timeout=timeout)
        try:
            target.fill(value, timeout=timeout)
        except Exception:
            try:
                target.press("Control+A", timeout=5_000)
                target.press("Backspace", timeout=5_000)
            except Exception:
                pass
            if value:
                page.keyboard.insert_text(value)
        deadline = time.monotonic() + min(timeout, 5_000) / 1_000
        while time.monotonic() < deadline:
            live_prompt = self._resolve_prompt(page, {
                **PAGE_OBJECTS.get(self.name, {}), **self.config.get("selectors", {})})
            if self._prompt_value(live_prompt) == expected:
                return
            page.wait_for_timeout(100)
        raise ProviderError("The complete request did not appear in the message field.")

    def _check_send_control(self, page, prompt, selectors: dict[str, Any]) -> bool:
        """Expose a dynamic Send button with an unsent draft, then clear it."""
        draft = "Maintain compatibility check. This text will not be sent."
        if self._prompt_value(prompt).strip():
            raise ProviderError(
                "The message field contains a draft. Clear it before compatibility checking.")
        try:
            self._set_prompt_text(page, prompt, draft)
            # M365 can replace the composer node after input. Reacquire the live
            # textbox before resolving nearby controls or reading its value.
            prompt = self._resolve_prompt(page, selectors)
            if self._prompt_value(prompt) != self._normalize_prompt_text(draft):
                raise ProviderError(
                    "The compatibility draft did not remain in the live message field.")
            page.wait_for_timeout(250)
            send = self._resolve_control(
                page, selectors.get("send_button_selector"), prompt, "send")
            if not self._control_enabled(send):
                raise ProviderError(
                    "The Send control remained disabled with a complete draft.")
            return True
        finally:
            live_prompt = self._resolve_prompt(page, selectors)
            self._set_prompt_text(page, live_prompt, "")

    def _new_chat(self, page, name: str) -> None:
        names = re.compile(
            rf"^({re.escape(name)}|New conversation|Start new chat)$",
            re.IGNORECASE)
        candidates = [
            node for node in page.get_by_role("link", name=names).or_(
                page.get_by_role("button", name=names)).all()
            if node.is_visible()
        ]
        if len(candidates) != 1:
            if not candidates:
                raise ProviderError("The New chat control was not found.")
            ranked = sorted(
                ((self._new_chat_penalty(node), node) for node in candidates),
                key=lambda item: item[0],
            )
            if ranked[0][0] == ranked[1][0]:
                best = [node for score, node in ranked if score == ranked[0][0]]
                destinations = {
                    urlparse(str(node.get_attribute("href") or "")).path
                    for node in best
                }
                if destinations != {"/"}:
                    raise ProviderError(
                        "More than one New chat control was found. "
                        "No browser action was taken.")
                target = best[0]
            else:
                target = ranked[0][1]
        else:
            target = candidates[0]
        try:
            target.click(timeout=min(
                int(self.config.get("timeout_ms", 300_000)), 5_000))
        except Exception as exc:
            href = str(target.get_attribute("href") or "")
            destination = urlparse(urljoin(page.url, href))
            current = urlparse(page.url)
            if (not href or destination.hostname != current.hostname
                    or destination.path != "/"):
                raise ProviderError(
                    "The New chat control could not be activated safely.") from exc
            page.goto(destination.geturl(), wait_until="domcontentloaded",
                      timeout=min(int(self.config.get("timeout_ms", 300_000)), 60_000))

    @staticmethod
    def _new_chat_penalty(control) -> int:
        href = str(control.get_attribute("href") or "")
        label = " ".join(filter(None, [
            control.get_attribute("aria-label"),
            control.get_attribute("data-testid"),
            control.get_attribute("id"),
        ])).casefold()
        path = urlparse(href).path if href else ""
        if any(token in label for token in (
                "create-new-chat", "new-chat-button", "new chat button")):
            return 0
        if path in {"", "/"} and href:
            return 2
        if "/c/" in path or any(token in label for token in (
                "conversation", "history", "pin", "options")):
            return 20
        return 5

    def compatibility_check(self, *, require_selected_model: bool = True) -> dict[str, Any]:
        """Inspect the signed-in UI without attaching files or sending a message."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ProviderError(
                "Install Maintain with the browser extra and install Chromium.") from exc
        selectors = {**PAGE_OBJECTS.get(self.name, {}), **self.config.get("selectors", {})}
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.profile_dir.parent / f".{self.profile_dir.name}.maintain.lock"
        self._start_journey()
        with FileLock(lock_path, f"browser profile {self.name}"), sync_playwright() as playwright:
            context = self._launch_context(playwright)
            page = context.pages[0] if context.pages else context.new_page()
            evidence = self.evidence_dir / f"{self.name}-compatibility.json"
            try:
                self._open_page(page)
                self._verify_session(page, selectors)
                self._enable_preferred_design(page, selectors)
                layout, prompt = self._recognize_page(page, selectors)
                selected_model = str(self.config.get("model") or "").strip()
                models = self._model_options(page, selectors)
                model_available = not selected_model or selected_model in models
                if require_selected_model and not model_available:
                    raise ProviderError(
                        f"The preferred model {selected_model!r} is no longer available. "
                        "Refresh the model list.")
                attachment_ready = bool(
                    self._attachment_input(page, prompt, selectors)
                    or self._attachment_trigger(page, prompt, selectors))
                controls = {
                    "message": True,
                    "attachment": attachment_ready,
                    "send": self._check_send_control(page, prompt, selectors),
                }
                self._mark_state(
                    "compatibility_confirmed", "Required controls are available")
                result = {
                    "ready": True,
                    "provider": self.name,
                    "layout": layout.name,
                    "model": (selected_model or None) if model_available else None,
                    "configured_model": selected_model or None,
                    "model_available": model_available,
                    "models": models,
                    "controls": controls,
                    "states": self._journey,
                }
                evidence.write_text(json.dumps(result, indent=2), encoding="utf-8")
                page.screenshot(
                    path=str(self.evidence_dir / f"{self.name}-compatibility.png"),
                    full_page=True)
                return result
            except Exception as exc:
                self._save_failure_evidence(
                    page, evidence, "compatibility check", exc, self._journey)
                raise ProviderError(
                    "Browser compatibility check stopped safely. "
                    f"Evidence: {evidence.resolve()}. Error: {exc}") from exc
            finally:
                context.close()

    def preflight(self) -> None:
        url = str(self.config.get("url", ""))
        parsed_url = urlparse(url)
        if parsed_url.scheme != "https" or not parsed_url.hostname:
            raise ProviderError("The browser provider needs an HTTPS URL.")
        default_hosts = {"chatgpt.com"} if self.name == "chatgpt_browser" else {
            "m365.cloud.microsoft"}
        allowed_hosts = {str(host).casefold() for host in
                         self.config.get("allowed_hosts", default_hosts)}
        if parsed_url.hostname.casefold() not in allowed_hosts:
            raise ProviderError(
                f"The browser provider URL host is not approved: {parsed_url.hostname}.")
        if self.name == "chatgpt_browser":
            capabilities = self.config.get("account_capabilities", {})
            available = set(capabilities.get("available", []))
            required = set(capabilities.get("required", []))
            missing = sorted(required - available)
            if missing:
                raise ProviderError(
                    f"The ChatGPT account lacks a required capability: {missing[0]}.")
        expected_context = _configured_value(
            self.config.get("expected_tenant") or self.config.get("expected_workspace"))
        expected_identity = _configured_value(self.config.get("expected_identity"))
        selectors = {**PAGE_OBJECTS.get(self.name, {}), **self.config.get("selectors", {})}
        identity_selector = selectors.get("identity_selector")
        if expected_identity and not identity_selector:
            raise ProviderError("Configure an identity selector for the signed-in user.")
        context_selector_name = ("tenant_selector" if self.name == "m365_copilot_browser"
                                 else "workspace_selector")
        context_selector = selectors.get(context_selector_name)
        if expected_context and not context_selector:
            raise ProviderError("Configure a selector for the expected tenant or workspace.")
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError as exc:
            raise ProviderError("Install Maintain with the browser extra and install Chromium.") from exc
        from playwright.sync_api import sync_playwright
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.profile_dir.parent / f".{self.profile_dir.name}.maintain.lock"
        with FileLock(lock_path, f"browser profile {self.name}"), sync_playwright() as playwright:
            context = self._launch_context(playwright)
            page = context.pages[0] if context.pages else context.new_page()
            try:
                self._start_journey()
                self._open_page(page)
                self._verify_session(page, selectors)
                page.screenshot(path=str(self.evidence_dir /
                                f"{self.name}-preflight-{time.time_ns()}.png"),
                                full_page=True)
            except ProviderError:
                raise
            except Exception as exc:
                raise ProviderError(f"Browser preflight failed: {exc}") from exc
            finally:
                context.close()

    def login(self) -> None:
        """Open the dedicated visible profile for interactive sign-in."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ProviderError("Install Maintain with the browser extra and install Chromium.") from exc

        if not bool(self.config.get("visible", True)):
            raise ProviderError("Interactive login requires visible browser mode.")
        lock_path = self.profile_dir.parent / f".{self.profile_dir.name}.maintain.lock"
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            with FileLock(lock_path, f"browser profile {self.name}"), sync_playwright() as playwright:
                context = self._launch_context(playwright, visible=True)
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(
                    self._navigation_url(), wait_until="domcontentloaded",
                    timeout=min(int(self.config.get("timeout_ms", 300_000)), 60_000))
                try:
                    input("Complete sign-in in the browser. Press Enter here when sign-in is complete: ")
                finally:
                    context.close()
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"Browser login failed: {exc}") from exc

    def available_models(self) -> list[str]:
        """Run the non-sending compatibility inspection and return its models."""
        return [str(model) for model in self.compatibility_check(
            require_selected_model=False)["models"]]

    def exchange(self, request: ProviderRequest):
        from playwright.sync_api import sync_playwright

        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        selectors = {**PAGE_OBJECTS.get(self.name, {}), **self.config.get("selectors", {})}
        response_selector = selectors.get("response_selector")
        new_chat_name = selectors.get("new_chat_name", "New chat")
        if not response_selector:
            raise ProviderError("Configure selectors.response_selector for the approved web UI.")
        lock_path = self.profile_dir.parent / f".{self.profile_dir.name}.maintain.lock"
        with FileLock(lock_path, f"browser profile {self.name}"), sync_playwright() as playwright:
            context = self._launch_context(playwright)
            page = context.pages[0] if context.pages else context.new_page()
            stage = "open assistant"
            self._start_journey()
            try:
                self._open_page(page)
                self._verify_session(page, selectors)
                self._enable_preferred_design(page, selectors)
                layout, prompt = self._recognize_page(page, selectors)
                stage = "start new chat"
                self._new_chat(page, new_chat_name)
                layout, prompt = self._recognize_page(page, selectors)
                stage = "select model"
                selected_model = str(self.config.get("model") or "").strip()
                if selected_model:
                    self._select_model(page, selectors, selected_model)
                self._mark_state(
                    "model_confirmed",
                    selected_model or "Use the account default model")
                serialized = json.dumps(asdict(request), ensure_ascii=False, separators=(",", ":"))
                digest = hashlib.sha256(serialized.encode()).hexdigest()
                exchange_dir = self._new_exchange_dir(digest)
                attachment_selector = selectors.get("attachment_selector")
                transport = "text"
                attachment_names: list[str] = []
                package_bytes = len(serialized.encode())
                if attachment_selector:
                    stage = "attach package files"
                    package = build_exchange_package(request, exchange_dir / "packages")
                    if len(package.paths) > 3:
                        raise ProviderError("A browser exchange can attach no more than three files.")
                    self._expected_attachments = [path.name for path in package.paths]
                    self._mark_state("attaching", f"Attach {len(package.paths)} package files")
                    self._attach_files(page, prompt, package.paths, selectors)
                    digest = package.sha256
                    package_bytes = package.bytes
                    attachment_names = list(self._expected_attachments)
                    message = (
                        f"This request is run_id={request.run_id}, task_id={request.task_id}, "
                        f"role={request.role}. Read all {len(package.paths)} attached package "
                        "files. Start with TASK.md, then use the indexed CODEBASE.md and "
                        f"MANIFEST.json. Package SHA-256: {digest}. Return an envelope with these "
                        "exact run_id, task_id, and role values. Follow the output instructions "
                        "exactly."
                    )
                    transport = "attachment"
                else:
                    chunks = make_chunks(serialized, int(self.config.get("max_chunk_chars", 12000)))
                    if len(chunks) == 1:
                        message = request.instructions + "\n\n" + chunks[0]
                    else:
                        acknowledgement = selectors.get("chunk_ack_selector")
                        if not acknowledgement:
                            raise ProviderError(
                                "Configure chunk_ack_selector when file upload is unavailable.")
                        for chunk in chunks:
                            chunk_hash = chunk.splitlines()[0].rsplit(" ", 1)[-1]
                            self._submit(page, prompt, chunk, selectors)
                            page.wait_for_function(
                                "([selector, hash]) => { const nodes = document.querySelectorAll(selector); "
                                "return nodes.length && nodes[nodes.length - 1].textContent.includes(hash); }",
                                arg=[acknowledgement, chunk_hash],
                                timeout=int(self.config.get("timeout_ms", 300_000)))
                        message = (f"{request.instructions}\nAll {len(chunks)} chunks are complete. "
                                   f"Package SHA-256: {digest}. Return the required envelope.")
                        transport = "chunks"
                previous_responses = self._visible_texts(page, response_selector)
                stage = "submit request"
                self._submit(
                    page, prompt, message, selectors,
                    expected_attachments=attachment_names)
                stage = "read assistant response"
                raw = self._wait_for_response_text(
                    page, selectors, request, previous_responses)
                assert_no_secrets(raw, "browser response")
                (exchange_dir / f"{request.role}-initial.txt").write_text(raw, encoding="utf-8")
                repaired = False
                try:
                    stage = "validate response"
                    parsed = parse_response(_extract_json(raw), request, self.name)
                except ProviderError:
                    repaired = True
                    stage = "repair response"
                    repair_message = (
                        "Your last response did not match this request. Return only one complete "
                        f"JSON envelope with schema_version={request.schema_version}, "
                        f"run_id={json.dumps(request.run_id)}, "
                        f"task_id={json.dumps(request.task_id)}, "
                        f"role={json.dumps(request.role)}, provider=\"assistant\", and the "
                        "correct content for the attached package. Do not reuse identifiers from "
                        "an earlier chat or task."
                    )
                    previous_responses = self._visible_texts(page, response_selector)
                    self._submit(page, prompt, repair_message, selectors)
                    raw = self._wait_for_response_text(
                        page, selectors, request, previous_responses)
                    assert_no_secrets(raw, "browser repair response")
                    (exchange_dir / f"{request.role}-repair.txt").write_text(
                        raw, encoding="utf-8")
                    parsed = parse_response(_extract_json(raw), request, self.name)
                parsed, raw, contract_repairs = self._repair_role_contract(
                    page, prompt, selectors, request, parsed, raw,
                    exchange_dir, response_selector)
                parsed = replace(
                    parsed,
                    conversation_id=f"{self.name}-{exchange_dir.name}",
                )
                if request.role == "implement":
                    output_zip = self._inline_output_zip(
                        parsed.content, request, exchange_dir.name)
                    queries = parsed.content.get("context_queries")
                    if (output_zip is None and isinstance(queries, list) and queries):
                        stage = "repair implementation content"
                        allowed = [
                            str(path) for path in
                            request.payload.get("task", {}).get("allowed_files", [])
                        ]
                        implementation_repair = (
                            "The supplied package is sufficient for this implementation. Do not "
                            "return context_queries. Return only one complete JSON envelope for "
                            f"run_id={json.dumps(request.run_id)}, "
                            f"task_id={json.dumps(request.task_id)}, "
                            f"role={json.dumps(request.role)}. In content.files, return the complete "
                            "final text for every changed file. Use only these exact approved paths: "
                            f"{json.dumps(allowed)}. Also return changed_files and deleted_files."
                        )
                        previous_responses = self._visible_texts(page, response_selector)
                        self._submit(page, prompt, implementation_repair, selectors)
                        raw = self._wait_for_response_text(
                            page, selectors, request, previous_responses)
                        assert_no_secrets(raw, "browser implementation repair response")
                        (exchange_dir / f"{request.role}-content-repair.txt").write_text(
                            raw, encoding="utf-8")
                        parsed = parse_response(_extract_json(raw), request, self.name)
                        parsed = replace(
                            parsed,
                            conversation_id=f"{self.name}-{exchange_dir.name}",
                        )
                        output_zip = self._inline_output_zip(
                            parsed.content, request, exchange_dir.name)
                    if output_zip is None:
                        download_selector = selectors.get("output_download_selector")
                        downloadable = bool(download_selector and any(
                            node.is_visible() for node in page.locator(download_selector).all()))
                        if not downloadable:
                            raise ProviderError(
                                "The implementation response contained neither complete inline "
                                "files nor a downloadable output artifact.")
                        stage = "download implementation"
                        output_zip = self._download_output_zip(
                            page, selectors, request, exchange_dir.name)
                    parsed.content["_maintain_output_zip"] = output_zip.name
                page.screenshot(path=str(exchange_dir / f"{request.role}.png"), full_page=True)
                (exchange_dir / f"{request.role}.txt").write_text(raw, encoding="utf-8")
                self._mark_state("response_saved", "Response and audit evidence saved")
                (exchange_dir / f"{request.role}-transport.json").write_text(
                    json.dumps({"transport": transport, "sha256": digest,
                                "bytes": package_bytes, "attachments": attachment_names,
                                "layout": layout.name,
                                "model": selected_model or None,
                                "conversation_id": parsed.conversation_id,
                                "states": self._journey,
                                "schema_repair": repaired,
                                "contract_repairs": contract_repairs,
                                "output_zip": (output_zip.name if request.role == "implement"
                                               else None)}),
                    encoding="utf-8")
                return parsed
            except Exception as exc:
                exchange_dir = locals().get("exchange_dir") or self._new_exchange_dir(
                    hashlib.sha256(f"{request.task_id}-{request.role}-{time.time_ns()}".encode()).hexdigest())
                self._save_failure_evidence(
                    page, exchange_dir / f"{request.role}-failure.json",
                    stage, exc, self._journey,
                    screenshot=exchange_dir / f"{request.role}-failure.png")
                raise ProviderError(
                    f"Browser provider stopped safely at {stage}. "
                    f"Evidence: {exchange_dir.resolve()}. Error: {exc}") from exc
            finally:
                context.close()

    def _wait_for_response_text(self, page, selectors: dict[str, Any],
                                request: ProviderRequest, previous_texts: list[str]) -> str:
        """Return a complete response without depending on one Copilot DOM locator."""
        response_selector = str(selectors.get("response_selector") or "")
        envelope_selector = str(selectors.get("response_envelope_selector") or "pre, code")
        generation_selector = selectors.get("generation_active_selector")
        continuation_selector = selectors.get("response_continue_selector")
        start_timeout = int(self.config.get(
            "response_start_timeout_ms",
            min(int(self.config.get("timeout_ms", 300_000)), 90_000),
        ))
        complete_timeout = int(self.config.get("timeout_ms", 300_000))
        settle_seconds = int(self.config.get("response_settle_ms", 1_500)) / 1_000
        start_deadline = time.monotonic() + start_timeout / 1_000
        complete_deadline: float | None = None
        latest = ""
        latest_at = time.monotonic()
        started = False
        continued = False
        generating = False
        continuation_visible = False
        while time.monotonic() < (complete_deadline or start_deadline):
            candidates = self._response_candidates(
                page, response_selector, envelope_selector, request, previous_texts,
                str(selectors.get("user_message_selector") or ""))
            generating = bool(generation_selector and any(
                node.is_visible() for node in page.locator(generation_selector).all()))
            continuation_controls = (
                [node for node in page.locator(continuation_selector).all()
                 if node.is_visible()]
                if continuation_selector else [])
            continuation_visible = bool(continuation_controls)
            if len(continuation_controls) > 1:
                raise ProviderError(
                    "More than one response continuation control was found.")
            if continuation_visible:
                if not started:
                    started = True
                    complete_deadline = time.monotonic() + complete_timeout / 1_000
                    self._mark_state("response_started", "Assistant response detected")
                if continued:
                    raise ProviderError(
                        "The assistant response remained incomplete after one continuation.")
                continuation_controls[0].click(
                    timeout=int(self.config.get("timeout_ms", 300_000)))
                continued = True
                self._mark_state(
                    "response_generating", "Continue one interrupted response")
                try:
                    continuation_controls[0].wait_for(state="hidden", timeout=5_000)
                except Exception as exc:
                    raise ProviderError(
                        "The response continuation control did not activate.") from exc
                continue
            if (candidates or generating) and not started:
                started = True
                complete_deadline = time.monotonic() + complete_timeout / 1_000
                self._mark_state("response_started", "Assistant response detected")
            if generating:
                self._mark_state("response_generating", "Assistant is generating")
            if candidates:
                candidate = max(
                    candidates,
                    key=lambda value: _response_score(value, request))
                if candidate != latest:
                    latest = candidate
                    latest_at = time.monotonic()
                try:
                    envelope = json.loads(_extract_json(candidate))
                except json.JSONDecodeError:
                    envelope = None
                if (isinstance(envelope, dict)
                        and str(envelope.get("run_id")) == request.run_id
                        and str(envelope.get("task_id")) == request.task_id
                        and str(envelope.get("role")) == request.role):
                    self._mark_state("response_complete", "Complete response envelope received")
                    return candidate
                if (not generating and not continuation_visible
                        and time.monotonic() - latest_at >= settle_seconds):
                    self._mark_state("response_complete", "Response stopped changing")
                    return latest
            page.wait_for_timeout(250)
        if (latest and not generating and not continuation_visible
                and time.monotonic() - latest_at >= settle_seconds):
            self._mark_state("response_complete", "Last complete visible response retained")
            return latest
        if started:
            raise ProviderError(
                "The assistant response started but did not finish before the timeout.")
        raise ProviderError(
            "The assistant did not expose a response to browser automation. "
            "The visible page was saved in the failure evidence.")

    @staticmethod
    def _response_candidates(page, response_selector: str, envelope_selector: str,
                             request: ProviderRequest, previous_texts: list[str],
                             user_selector: str = "") -> list[str]:
        found: list[str] = []
        if response_selector:
            for node in page.locator(response_selector).all():
                if not node.is_visible():
                    continue
                text = node.text_content() or ""
                if text and text not in previous_texts and text not in found:
                    found.append(text)
        if envelope_selector:
            for node in page.locator(envelope_selector).all():
                if not node.is_visible():
                    continue
                text = node.text_content() or ""
                if (text and all(token in text for token in (
                        request.run_id, request.task_id, request.role)) and text not in found):
                    found.append(text)
        token_matches = page.locator("body").evaluate(
            """(body, args) => {
              const [tokens, userSelector] = args;
              const visible = node => {
                const style = getComputedStyle(node);
                const rect = node.getBoundingClientRect();
                return style.visibility !== 'hidden' && style.display !== 'none' &&
                       rect.width > 0 && rect.height > 0;
              };
              const matches = node => {
                const text = node.textContent || '';
                const outgoing = userSelector && node.closest(userSelector);
                const editable = node.closest('textarea, input, [contenteditable="true"]');
                return !outgoing && !editable && visible(node) &&
                       tokens.every(token => text.includes(token));
              };
              return [...body.querySelectorAll('*')]
                .filter(node => matches(node) && ![...node.children].some(matches))
                .map(node => node.textContent || '');
            }""",
            [[request.run_id, request.task_id, request.role], user_selector])
        for text in token_matches:
            if text and text not in found:
                found.append(text)
        return found

    @staticmethod
    def _visible_texts(page, selector: str) -> list[str]:
        return [node.text_content() or "" for node in page.locator(selector).all()
                if node.is_visible()]

    @staticmethod
    def _normalize_prompt_text(value: object) -> str:
        """Normalize editor text and remove Lexical formatting sentinels."""
        normalized = str(value or "").replace("\r\n", "\n").replace("\u00a0", " ")
        return normalized.replace("\u200b", "").replace("\u200c", "").strip()

    @classmethod
    def _prompt_value(cls, prompt) -> str:
        value = prompt.evaluate(
            """field => {
              const target = field.matches('textarea, input, [contenteditable="true"]')
                ? field
                : field.querySelector('textarea, input, [contenteditable="true"]');
              if (!target) return field.innerText || field.textContent || '';
              return ('value' in target ? target.value : target.innerText || target.textContent) || '';
            }""")
        return cls._normalize_prompt_text(value)

    @staticmethod
    def _control_enabled(control) -> bool:
        return bool(control.is_enabled() and
                    str(control.get_attribute("aria-disabled") or "").casefold() != "true")

    def _submission_observed(self, page, prompt, selectors: dict[str, Any],
                             previous_user_messages: int) -> bool:
        if not self._prompt_value(prompt).strip():
            return True
        user_selector = selectors.get("user_message_selector")
        if user_selector and page.locator(user_selector).count() > previous_user_messages:
            return True
        generation_selector = selectors.get("generation_active_selector")
        return bool(generation_selector and any(
            node.is_visible() for node in page.locator(generation_selector).all()))

    def _submit(self, page, prompt, message: str, selectors: dict[str, Any],
                *, expected_attachments: list[str] | None = None) -> None:
        """Submit after confirming the prompt, attachments, and nearby Send control."""
        timeout = int(self.config.get("timeout_ms", 300_000))
        confirm_timeout = int(self.config.get("submission_confirm_timeout_ms", 30_000))
        selected_model = str(self.config.get("model") or "").strip()
        if selected_model and not self._preferred_model_is_active(
                page, selectors, selected_model):
            self._select_model(page, selectors, selected_model)
        user_message_selector = selectors.get("user_message_selector")
        previous_user_messages = (page.locator(user_message_selector).count()
                                  if user_message_selector else 0)
        self._set_prompt_text(page, prompt, message)
        # Input can replace the M365 composer node. Continue with the current node.
        prompt = self._resolve_prompt(page, selectors)
        if self._prompt_value(prompt) != self._normalize_prompt_text(message):
            raise ProviderError("The complete request did not remain in the live message field.")
        self._mark_state("prompt_entered", "Complete request entered")
        page.wait_for_timeout(int(self.config.get(
            "send_settle_ms", 750 if self.name == "m365_copilot_browser" else 250)))
        if self._prompt_value(prompt) != self._normalize_prompt_text(message):
            raise ProviderError("The request changed before it could be submitted.")
        if expected_attachments and not self._attachments_ready(
                page, expected_attachments, selectors):
            raise ProviderError("The attached files were not ready when Send was checked.")
<<<<<<< HEAD
=======
        if selected_model and not self._preferred_model_is_active(
                page, selectors, selected_model):
            raise ProviderError(
                "The preferred model changed before submission. No request was sent.")
        if not self._control_enabled(send):
            raise ProviderError("The Send control changed before submission.")
>>>>>>> bd12c5566b0d3f7e2d6425e17813c87915963f52

        submit_attempts = max(2, min(int(self.config.get("submit_retries", 3)), 5))
        last_problem = ""
        for attempt in range(1, submit_attempts + 1):
            prompt = self._resolve_prompt(page, selectors)
            current = self._prompt_value(prompt)
            if current != self._normalize_prompt_text(message):
                self._set_prompt_text(page, prompt, message)
                prompt = self._resolve_prompt(page, selectors)
            submitted = False
            try:
                send = self._resolve_control(
                    page, selectors.get("send_button_selector"), prompt, "send")
                enable_deadline = time.monotonic() + min(timeout, 10_000) / 1_000
                while not self._control_enabled(send) and time.monotonic() < enable_deadline:
                    page.wait_for_timeout(250)
                    prompt = self._resolve_prompt(page, selectors)
                    try:
                        send = self._resolve_control(
                            page, selectors.get("send_button_selector"), prompt, "send")
                    except ProviderError:
                        continue
                if self._control_enabled(send):
                    send.click(timeout=min(timeout, 30_000))
                    submitted = True
            except Exception as exc:
                last_problem = str(exc)

            if not submitted:
                # M365 can temporarily replace or omit the Send button after several replies.
                # Enter on the live composer is the supported semantic fallback.
                prompt = self._resolve_prompt(page, selectors)
                try:
                    prompt.press("Enter", timeout=min(timeout, 30_000))
                    submitted = True
                except Exception as exc:
                    last_problem = str(exc)

            if submitted:
                confirm_deadline = time.monotonic() + confirm_timeout / 1_000
                while time.monotonic() < confirm_deadline:
                    prompt = self._resolve_prompt(page, selectors)
                    if self._submission_observed(
                            page, prompt, selectors, previous_user_messages):
                        self._mark_state("request_submitted", "Outgoing request confirmed")
                        return
                    page.wait_for_timeout(250)

            prompt = self._resolve_prompt(page, selectors)
            current = self._prompt_value(prompt)
            if not current.strip():
                raise ProviderError(
                    "The request may have been submitted, but the outgoing message "
                    "could not be confirmed.")
            if attempt < submit_attempts:
                page.wait_for_timeout(min(500 * attempt, 2_000))
                continue
        detail = f" Last browser error: {last_problem}" if last_problem else ""
        raise ProviderError(
            f"The request was not submitted after {submit_attempts} bounded attempts.{detail}")

    @staticmethod
    def _attachment_readiness_script() -> str:
        return """
            ([pendingSelector, readySelector, names]) => {
              const visible = node => {
                const style = getComputedStyle(node);
                const rect = node.getBoundingClientRect();
                return style.visibility !== 'hidden' && style.display !== 'none' &&
                       rect.width > 0 && rect.height > 0;
              };
              const visibleNodes = selector => selector
                ? [...document.querySelectorAll(selector)].filter(visible) : [];
              const normalized = value => String(value || '').toLocaleLowerCase();
              const bodyText = normalized(document.body.innerText);
              const readyNodes = visibleNodes(readySelector);
              const readyText = readyNodes.map(node => normalized(
                node.innerText || node.textContent || node.getAttribute('aria-label')));
              const filenameVisible = name => {
                const expected = normalized(name);
                return readyText.some(value => value.includes(expected)) ||
                       bodyText.includes(expected);
              };
              const completed = value =>
                value.includes('upload finished') || value.includes('upload complete') ||
                value.includes('uploaded successfully');
              const pending = visibleNodes(pendingSelector).some(node => {
                const value = normalized(
                  node.innerText || node.textContent || node.getAttribute('aria-label'));
                return !completed(value);
              });
              return names.length > 0 && names.every(filenameVisible) && !pending;
            }
        """

    def _attachments_ready(self, page, names: list[str], selectors: dict[str, Any]) -> bool:
        return bool(page.evaluate(
            self._attachment_readiness_script(),
            [selectors.get("upload_pending_selector"),
             selectors.get("attachment_ready_selector"), names],
        ))

    def _wait_for_attachments(self, page, file_input, paths: tuple[Path, ...] | list[Path],
                              selectors: dict[str, Any]) -> None:
        """Wait until every selected file is visibly attached and upload activity stops."""
        timeout = min(int(self.config.get("timeout_ms", 300_000)), 60_000)
        names = [path.name for path in paths]
        args = [selectors.get("upload_pending_selector"),
                selectors.get("attachment_ready_selector"), names]

        # Copilot consumes the selected files, then can clear or replace the hidden
        # input. The visible attachment state is authoritative after set_input_files.
        page.wait_for_function(
            self._attachment_readiness_script(), arg=args, timeout=timeout)
        page.wait_for_timeout(int(self.config.get(
            "upload_settle_ms", 2_000 if self.name == "m365_copilot_browser" else 500)))
        if not self._attachments_ready(page, names, selectors):
            raise ProviderError(
                "The attached files did not remain ready after the upload settle period.")
        self._mark_state("files_ready", f"{len(names)} attached files confirmed")

    def _enable_preferred_design(self, page, selectors: dict[str, Any]) -> None:
        """Enable the current Copilot design when its opt-in toggle is present."""
        toggle_selector = selectors.get("new_design_toggle_selector")
        if not toggle_selector:
            return
        toggles = page.locator(toggle_selector)
        for toggle in toggles.all():
            if not toggle.is_visible():
                continue
            state = str(toggle.get_attribute("aria-checked") or
                        toggle.get_attribute("aria-pressed") or "").casefold()
            label = " ".join(filter(None, [toggle.get_attribute("aria-label"),
                                             toggle.inner_text()])).casefold()
            if state == "true" or any(text in label for text in (
                    "turn off", "switch to old", "use old", "classic design")):
                return
            toggle.click(timeout=int(self.config.get("timeout_ms", 300_000)))
            page.wait_for_timeout(1_000)
            return

    def _model_options(self, page, selectors: dict[str, Any]) -> list[str]:
        picker_selector = selectors.get("model_picker_selector")
        option_selector = selectors.get("model_option_selector")
        if not picker_selector or not option_selector:
            raise ProviderError("Model discovery selectors are not configured for this browser provider.")
        timeout = int(self.config.get("timeout_ms", 300_000))
        submenu_selector = selectors.get("model_submenu_selector")
        found: list[str] = []
        states: list[dict[str, Any]] = []
        queued: list[tuple[str, ...]] = [()]
        visited: set[tuple[str, ...]] = set()
        while queued:
            path = queued.pop(0)
            if path in visited or len(path) > 3:
                continue
            visited.add(path)
            if not self._open_model_path(
                    page, picker_selector, submenu_selector, option_selector, path, timeout):
                continue
            submenu_labels = self._visible_labels(page, submenu_selector)
            submenu_keys = {label.casefold() for label in submenu_labels}
            option_labels = self._visible_labels(page, option_selector)
            states.append({"path": list(path), "options": option_labels,
                           "submenus": submenu_labels})
            for label in option_labels:
                if label.casefold() not in submenu_keys and label.casefold() not in {
                        item.casefold() for item in found}:
                    found.append(label)
            queued.extend(path + (label,) for label in submenu_labels)
        self._close_model_menu(page, 4)
        (self.evidence_dir / f"{self.name}-model-discovery.json").write_text(
            json.dumps({"models": found, "menu_states": states}, indent=2), encoding="utf-8")
        if not found:
            raise ProviderError("The model picker opened, but it did not contain any models.")
        self._mark_state("models_discovered", f"{len(found)} selectable models found")
        return found

    def _select_model(self, page, selectors: dict[str, Any], model: str) -> None:
        """Select and confirm the preferred model with bounded UI recovery."""
        attempts = max(2, min(int(self.config.get("model_selection_retries", 3)), 5))
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                self._select_model_once(page, selectors, model)
                return
            except ProviderError as exc:
                last_error = exc
                message = str(exc)
                unavailable = "is no longer available" in message
                ambiguous = "More than one model control" in message
                if unavailable or ambiguous or attempt >= attempts:
                    break
                self._close_model_menu(page, 4)
                page.wait_for_timeout(min(1_000 * attempt, 3_000))
                try:
                    page.reload(wait_until="domcontentloaded", timeout=min(
                        int(self.config.get("timeout_ms", 300_000)), 60_000))
                    page.wait_for_timeout(1_000)
                except Exception:
                    # A reload is recovery assistance only. The next attempt re-resolves controls.
                    pass
        raise ProviderError(
            f"The preferred model {model!r} could not be confirmed after {attempts} bounded "
            f"attempts. No request was sent. Last error: {last_error}") from last_error

    def _select_model_once(self, page, selectors: dict[str, Any], model: str) -> None:
        picker_selector = selectors.get("model_picker_selector")
        option_selector = selectors.get("model_option_selector")
        if not picker_selector or not option_selector:
            raise ProviderError("Model selection selectors are not configured for this browser provider.")
        timeout = int(self.config.get("timeout_ms", 300_000))
        submenu_selector = selectors.get("model_submenu_selector")
        queued: list[tuple[str, ...]] = [()]
        visited: set[tuple[str, ...]] = set()
        while queued:
            path = queued.pop(0)
            if path in visited or len(path) > 3:
                continue
            visited.add(path)
            if not self._open_model_path(
                    page, picker_selector, submenu_selector, option_selector, path, timeout):
                continue
            for option in page.locator(option_selector).all():
                if (option.is_visible()
                        and _model_label(option.inner_text()).casefold() == model.casefold()):
                    option.click(timeout=timeout)
                    confirm_deadline = time.monotonic() + min(timeout, 15_000) / 1_000
                    observed = ""
                    while time.monotonic() < confirm_deadline:
                        try:
                            picker = self._wait_for_model_picker(page, picker_selector, timeout)
                            labels = self._model_control_labels(picker)
                            observed = next(
                                (label for label in labels if label), observed)
                            if any(_model_matches(model, label) for label in labels):
                                self._mark_state("model_confirmed", model)
                                return
                        except ProviderError as exc:
                            if "More than one" in str(exc):
                                raise
                        except Exception:
                            # The control can be replaced while the UI applies a model.
                            pass
                        page.wait_for_timeout(250)
                    raise ProviderError(
                        f"The model control did not confirm {model!r}; it still showed "
                        f"{observed or 'an unknown value'!r}. No request was sent.")
            queued.extend(path + (label,)
                          for label in self._visible_labels(page, submenu_selector))
        self._close_model_menu(page, 4)
        raise ProviderError(
            f"The preferred model {model!r} is no longer available. Refresh the model list.")

    def _preferred_model_is_active(
            self, page, selectors: dict[str, Any], model: str) -> bool:
        picker_selector = selectors.get("model_picker_selector")
        if not picker_selector:
            raise ProviderError(
                "Model selection selectors are not configured for this browser provider.")
        try:
            picker = self._primary_model_picker(page, picker_selector)
        except ProviderError as exc:
            if "More than one" in str(exc):
                raise
            return False
        return any(_model_matches(model, label)
                   for label in self._model_control_labels(picker))

    def _open_model_path(self, page, picker_selector: str, submenu_selector: str | None,
                         option_selector: str, path: tuple[str, ...], timeout: int) -> bool:
        for attempt in range(2):
            try:
                self._close_model_menu(page, 4)
                picker = self._wait_for_model_picker(page, picker_selector, timeout)
                picker.click(timeout=timeout)
                self._wait_for_visible_options(page, option_selector, timeout)
                for label in path:
                    target = self._visible_option(page, submenu_selector, label)
                    if target is None:
                        return False
                    target.click(timeout=timeout)
                    page.wait_for_timeout(300)
                return True
            except ProviderError:
                raise
            except Exception:
                if attempt == 0:
                    page.wait_for_timeout(300)
                    continue
                return False
        return False

    @staticmethod
    def _visible_option(page, selector: str | None, label: str):
        if not selector:
            return None
        for option in page.locator(selector).all():
            if (option.is_visible()
                    and _model_label(option.inner_text()).casefold() == label.casefold()):
                return option
        return None

    @staticmethod
    def _close_model_menu(page, attempts: int) -> None:
        for _ in range(attempts):
            page.keyboard.press("Escape")

    @staticmethod
    def _visible_labels(page, selector: str | None) -> list[str]:
        if not selector:
            return []
        found: list[str] = []
        for option in page.locator(selector).all():
            if not option.is_visible():
                continue
            label = _model_label(option.inner_text())
            if label and label.casefold() not in {item.casefold() for item in found}:
                found.append(label)
        return found

    def _wait_for_model_picker(self, page, selector: str, timeout: int):
        """Wait for the client-rendered top-level model selector."""
        deadline = time.monotonic() + min(timeout, 30_000) / 1_000
        last_error: ProviderError | None = None
        while time.monotonic() < deadline:
            try:
                return self._primary_model_picker(page, selector)
            except ProviderError as exc:
                if "More than one" in str(exc):
                    raise
                last_error = exc
            page.wait_for_timeout(250)
        raise ProviderError(
            "The model control did not render within 30 seconds.") from last_error

    @staticmethod
    def _primary_model_picker(page, selector: str):
        """Return the one top-level model control, never a model menu option."""
        candidates = []
        for node in page.locator(selector).all():
            if not node.is_visible():
                continue
            is_primary = bool(node.evaluate(
                """candidate => {
                  const role = (candidate.getAttribute('role') || '').toLocaleLowerCase();
                  const testId = (candidate.getAttribute('data-testid') || '')
                    .toLocaleLowerCase();
                  if (['menuitem', 'menuitemradio', 'option'].includes(role) ||
                      testId.includes('model-option')) return false;
                  return !candidate.closest('[role="menu"], [role="listbox"]');
                }"""
            ))
            if is_primary:
                candidates.append(node)
        if not candidates:
            raise ProviderError("The model control was not found.")
        if len(candidates) > 1:
            raise ProviderError(
                "More than one model control was found. No browser action was taken.")
        return candidates[0]

    @staticmethod
    def _model_control_labels(control) -> list[str]:
        visible_label = _model_label(str(control.inner_text() or ""))
        if visible_label:
            return [visible_label]
        values = [control.get_attribute("aria-label"), control.get_attribute("title")]
        found: list[str] = []
        for value in values:
            label = _model_label(str(value or ""))
            if label and label.casefold() not in {item.casefold() for item in found}:
                found.append(label)
        return found

    @staticmethod
    def _wait_for_visible_options(page, selector: str, timeout: int) -> None:
        page.wait_for_function(
            "selector => [...document.querySelectorAll(selector)].some(node => { "
            "const style = getComputedStyle(node); const rect = node.getBoundingClientRect(); "
            "return style.visibility !== 'hidden' && style.display !== 'none' && "
            "rect.width > 0 && rect.height > 0; })",
            arg=selector, timeout=timeout)

    @staticmethod
    def _contract_problem(content: dict[str, Any], role: str) -> str:
        """Return a concise role-contract defect, or an empty string when usable."""
        if role == "scope":
            # Scope discovery is workflow-managed. Empty or incomplete scope content must reach
            # the engine so it can expand context, request final synthesis, or use its constrained
            # deterministic fallback instead of exhausting chat-only contract retries.
            return ""
        if role == "implement":
            files = content.get("files")
            queries = content.get("context_queries")
            changed = content.get("changed_files")
            if isinstance(files, list) and files:
                return ""
            if isinstance(queries, list) and queries:
                return ""
            if isinstance(changed, list) and changed:
                return ""
            return "content must contain complete files, context_queries, or changed_files"
        if role == "review":
            decision = str(content.get("decision", "")).strip().casefold()
            result = str(content.get("result", "")).strip().casefold()
            approved = content.get("approved")
            accepted = {
                "approve", "approved", "changes_requested", "pass", "passed",
                "success", "fail", "failed", "reject", "rejected",
            }
            if decision in accepted or result in accepted or isinstance(approved, bool):
                return ""
            return "content must contain decision, approved, or result"
        return ""

    def _repair_role_contract(self, page, prompt, selectors: dict[str, Any],
                              request: ProviderRequest, parsed, raw: str,
                              exchange_dir: Path, response_selector: str):
        """Request bounded corrections for a valid envelope with an invalid role contract."""
        attempts = max(0, min(int(self.config.get("contract_retries", 2)), 3))
        for attempt in range(1, attempts + 1):
            problem = self._contract_problem(parsed.content, request.role)
            if not problem:
                return parsed, raw, attempt - 1
            repair = (
                "Correct only the response contract. Return one complete JSON envelope and no "
                "other text. Keep these exact identifiers: "
                f"run_id={json.dumps(request.run_id)}, task_id={json.dumps(request.task_id)}, "
                f"role={json.dumps(request.role)}, provider=\"assistant\". Contract defect: "
                f"{problem}. Use the attached package and return the complete required content."
            )
            previous = self._visible_texts(page, response_selector)
            self._submit(page, prompt, repair, selectors)
            raw = self._wait_for_response_text(page, selectors, request, previous)
            assert_no_secrets(raw, "browser contract repair response")
            (exchange_dir / f"{request.role}-contract-repair-{attempt}.txt").write_text(
                raw, encoding="utf-8")
            parsed = parse_response(_extract_json(raw), request, self.name)
        problem = self._contract_problem(parsed.content, request.role)
        if problem:
            raise ProviderError(
                f"{request.role} response still violates its contract after {attempts} repair "
                f"attempt(s): {problem}.")
        return parsed, raw, attempts

    def _inline_output_zip(self, content: dict[str, Any], request: ProviderRequest,
                           exchange_id: str) -> Path | None:
        """Build the implementation ZIP from validated inline complete-file content."""
        files = content.get("files")
        if not isinstance(files, list) or not files:
            return None
        task = request.payload.get("task", {})
        allowed_files = task.get("allowed_files", [])
        allowed = {str(path) for path in allowed_files}
        entries: list[tuple[str, str]] = []
        seen: set[str] = set()
        for item in files:
            if not isinstance(item, dict):
                raise ProviderError("Inline implementation files must be objects.")
            name = item.get("path")
            body = item.get("content")
            if not isinstance(name, str) or not isinstance(body, str):
                raise ProviderError("Inline implementation files need string path and content values.")
            path = Path(name)
            if (path.is_absolute() or name != path.as_posix() or
                    any(part in {"", ".", ".."} for part in path.parts)):
                raise ProviderError(f"Inline implementation returned an unsafe path: {name}")
            if name not in allowed:
                # Copilot can correctly remove an accidental transport-style .txt suffix from a
                # single source-file path, for example main.c.txt -> main.c. Permit only this
                # narrow, auditable correction when project policy allows new files.
                aliases = [approved for approved in allowed if approved == f"{name}.txt"]
                allow_new = bool(request.payload.get("allow_new_files", True))
                if len(allowed) == 1 and len(aliases) == 1 and allow_new:
                    if isinstance(allowed_files, list):
                        allowed_files.append(name)
                    allowed.add(name)
                    content.setdefault("path_corrections", []).append({
                        "approved_path": aliases[0],
                        "implementation_path": name,
                        "reason": "removed_trailing_txt_suffix",
                    })
                else:
                    raise ProviderError(
                        f"Inline implementation returned an unapproved path: {name}")
            if name in seen:
                raise ProviderError(f"Inline implementation returned a duplicate path: {name}")
            seen.add(name)
            entries.append((name, body))
        inline_paths = [name for name, _ in entries]
        declared = content.get("changed_files")
        if declared is None:
            content["changed_files"] = list(inline_paths)
        elif (not isinstance(declared, list)
              or any(not isinstance(name, str) for name in declared)
              or set(declared) != set(inline_paths)):
            raise ProviderError(
                "Inline implementation files do not match declared changed files.")
        deleted = content.get("deleted_files")
        if deleted is None:
            content["deleted_files"] = []
        elif not isinstance(deleted, list):
            raise ProviderError("Inline implementation deleted_files must be a list.")

        destination = self.evidence_dir / f"{exchange_id}-{request.task_id}-{request.role}-output.zip"
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, body in entries:
                archive.writestr(name, body.encode("utf-8"))
        if not zipfile.is_zipfile(destination):
            destination.unlink(missing_ok=True)
            raise ProviderError("The inline implementation output is not a valid ZIP file.")
        return destination

    def _download_output_zip(self, page, selectors: dict[str, Any],
                             request: ProviderRequest, exchange_id: str) -> Path:
        selector = selectors.get("output_download_selector")
        if not selector:
            raise ProviderError(
                "Configure selectors.output_download_selector for implementation ZIP files.")
        target = page.locator(selector).last
        timeout = int(self.config.get("timeout_ms", 300_000))
        target.wait_for(state="visible", timeout=timeout)
        with page.expect_download(timeout=timeout) as pending:
            target.click()
        download = pending.value
        safe_task = re.sub(r"[^A-Za-z0-9._-]+", "-", request.task_id).strip("-.") or "task"
        destination = self.evidence_dir / f"{exchange_id}-{safe_task}-{request.role}-output.zip"
        download.save_as(str(destination))
        if not zipfile.is_zipfile(destination):
            destination.unlink(missing_ok=True)
            raise ProviderError("The implementation output is not a valid ZIP file.")
        return destination

    def _launch_context(self, playwright, *, visible: bool | None = None):
        browser = str(self.config.get("browser") or "chromium").casefold()
        if browser not in {"chromium", "chrome", "msedge"}:
            raise ProviderError("Browser must be chromium, chrome, or msedge.")
        options: dict[str, Any] = {
            "headless": not (bool(self.config.get("visible", True)) if visible is None else visible)
        }
        if browser != "chromium":
            options["channel"] = browser
        return playwright.chromium.launch_persistent_context(str(self.profile_dir), **options)

    def _new_exchange_dir(self, digest: str) -> Path:
        root = self.evidence_dir / "exchanges"
        root.mkdir(parents=True, exist_ok=True)
        stem = digest[:12]
        candidate = root / stem
        suffix = 2
        while candidate.exists():
            candidate = root / f"{stem}-{suffix}"
            suffix += 1
        candidate.mkdir()
        return candidate

    def _save_failure_evidence(self, page, path: Path, stage: str, error: Exception,
                               states: list[dict[str, str]], *,
                               screenshot: Path | None = None) -> None:
        """Save a control-only page snapshot without cookies, tokens, or message text."""
        path.parent.mkdir(parents=True, exist_ok=True)
        diagnostic: dict[str, Any] = {
            "error": str(error),
            "stage": stage,
            "provider": self.name,
            "layout": self._layout_name or None,
            "expected_model": str(self.config.get("model") or "") or None,
            "expected_attachments": self._expected_attachments,
            "states": states,
        }
        screenshot = screenshot or path.with_suffix(".png")
        try:
            page.screenshot(path=str(screenshot), full_page=True)
            diagnostic["screenshot"] = screenshot.name
        except Exception as screenshot_error:
            diagnostic["screenshot_error"] = str(screenshot_error)
        try:
            diagnostic["url"] = page.url
            diagnostic["title"] = page.title()
            selectors = {**PAGE_OBJECTS.get(self.name, {}),
                         **self.config.get("selectors", {})}
            picker_selector = selectors.get("model_picker_selector")
            if picker_selector:
                try:
                    diagnostic["observed_model_labels"] = self._model_control_labels(
                        self._primary_model_picker(page, picker_selector))
                except ProviderError:
                    diagnostic["observed_model_labels"] = self._visible_labels(
                        page, picker_selector)
            else:
                diagnostic["observed_model_labels"] = []
            diagnostic["observed_attachments"] = page.locator("body").evaluate(
                "(body, names) => { const text = (body.innerText || '').toLocaleLowerCase(); "
                "return names.filter(name => text.includes(String(name).toLocaleLowerCase())); }",
                self._expected_attachments,
            )
            diagnostic["controls"] = page.locator(
                'button, [role="button"], [role="switch"], [role="menuitem"], '
                '[role="menuitemradio"], [role="option"], input, textarea, '
                '[contenteditable="true"]'
            ).evaluate_all(
                """nodes => nodes.slice(0, 250).map(node => {
                  const style = getComputedStyle(node);
                  const rect = node.getBoundingClientRect();
                  const visible = style.visibility !== 'hidden' && style.display !== 'none' &&
                                  rect.width > 0 && rect.height > 0;
                  const safeText = ['BUTTON', 'SUMMARY'].includes(node.tagName)
                    ? (node.innerText || '').replace(/\\s+/g, ' ').slice(0, 120) : '';
                  return {
                    tag: node.tagName.toLowerCase(),
                    role: node.getAttribute('role'),
                    name: (node.getAttribute('aria-label') ||
                           node.getAttribute('placeholder') || safeText).slice(0, 120),
                    test_id: (node.getAttribute('data-testid') || '').slice(0, 120),
                    type: node.getAttribute('type'),
                    visible,
                    disabled: Boolean(node.disabled) ||
                              node.getAttribute('aria-disabled') === 'true',
                    busy: node.getAttribute('aria-busy'),
                    checked: node.getAttribute('aria-checked')
                  };
                })"""
            )
        except Exception as inventory_error:
            diagnostic["controls"] = []
            diagnostic["inventory_error"] = str(inventory_error)
        path.write_text(json.dumps(diagnostic, indent=2), encoding="utf-8")


def _repair_unescaped_json_quotes(candidate: str) -> str:
    """Escape prose quotes inside JSON strings without changing structural quotes."""
    repaired: list[str] = []
    in_string = False
    escaped = False
    length = len(candidate)
    for index, char in enumerate(candidate):
        if escaped:
            repaired.append(char)
            escaped = False
            continue
        if char == "\\" and in_string:
            repaired.append(char)
            escaped = True
            continue
        if char != '"':
            repaired.append(char)
            continue
        if not in_string:
            in_string = True
            repaired.append(char)
            continue
        following = index + 1
        while following < length and candidate[following].isspace():
            following += 1
        next_char = candidate[following] if following < length else ""
        if next_char in {":", ",", "}", "]", ""}:
            in_string = False
            repaired.append(char)
        else:
            repaired.append('\\"')
    return "".join(repaired)


def _json_object_candidates(text: str) -> list[str]:
    """Return likely envelope objects from a response transcript, newest first."""
    starts = [match.start() for match in re.finditer(r'\{\s*"schema_version"', text)]
    candidates: list[str] = []
    for start in reversed(starts):
        for end in range(len(text), start, -1):
            if text[end - 1] != "}":
                continue
            candidate = text[start:end].strip()
            repaired = _repair_unescaped_json_quotes(candidate)
            try:
                json.loads(repaired)
            except json.JSONDecodeError:
                continue
            candidates.append(repaired)
            break
    return candidates


def _extract_json(text: str) -> str:
    stripped = text.strip()
    fences = [match.start() for match in re.finditer(r"```", stripped)]
    for index, opening in enumerate(fences[:-1]):
        content_start = opening + 3
        leading = len(stripped[content_start:]) - len(
            stripped[content_start:].lstrip())
        content_start += leading
        if stripped[content_start:content_start + 4].casefold() == "json":
            content_start += 4
        for closing in reversed(fences[index + 1:]):
            if closing <= content_start:
                continue
            candidate = stripped[content_start:closing].strip()
            repaired = _repair_unescaped_json_quotes(candidate)
            try:
                json.loads(repaired)
                return repaired
            except json.JSONDecodeError:
                continue
    candidates = _json_object_candidates(stripped)
    return candidates[0] if candidates else stripped


def _configured_value(value: object) -> str:
    shown = str(value or "").strip()
    return "" if shown.startswith("SET_") else shown


def _model_label(value: str) -> str:
    lines = [" ".join(line.split()) for line in value.splitlines() if line.strip()]
    return " · ".join(lines).removesuffix(" Selected").strip(" ✓")


def _model_matches(expected: str, observed: str) -> bool:
    left = " ".join(expected.casefold().split())
    right = " ".join(observed.casefold().split())
    return bool(left and right and (left == right or left in right or right in left))


def _response_score(value: str, request: ProviderRequest) -> tuple[int, int]:
    try:
        envelope = json.loads(_extract_json(value))
    except json.JSONDecodeError:
        envelope = None
    exact = bool(
        isinstance(envelope, dict)
        and str(envelope.get("run_id")) == request.run_id
        and str(envelope.get("task_id")) == request.task_id
        and str(envelope.get("role")) == request.role
    )
    contains_tokens = all(token in value for token in (
        request.run_id, request.task_id, request.role))
    return (2 if exact else 1 if contains_tokens else 0, len(value))


def make_chunks(serialized: str, maximum: int) -> list[str]:
    if maximum < 100:
        raise ValueError("Chunk size must be at least 100 characters.")
    pieces = [serialized[index:index + maximum] for index in range(0, len(serialized), maximum)] or [""]
    total = len(pieces)
    return [f"MAINTAIN CHUNK {index}/{total} SHA-256 {hashlib.sha256(piece.encode()).hexdigest()}\n{piece}"
            for index, piece in enumerate(pieces, 1)]


class M365CopilotBrowserProvider(BrowserProvider):
    def __init__(self, config: dict[str, Any], evidence_dir: Path) -> None:
        super().__init__("m365_copilot_browser", config, evidence_dir)


class ChatGPTBrowserProvider(BrowserProvider):
    def __init__(self, config: dict[str, Any], evidence_dir: Path) -> None:
        super().__init__("chatgpt_browser", config, evidence_dir)


PAGE_OBJECTS = {
    "chatgpt_browser": {
        "new_chat_name": "New chat", "prompt_role": "textbox",
        "sign_in_selector": (
            'a[data-testid="login-button"], button[data-testid="login-button"], '
            'button:has-text("Log in")'
        ),
        "attachment_selector": 'input[type="file"]',
        "response_selector": '[data-message-author-role="assistant"]',
        "response_envelope_selector": (
            'pre, code, [data-message-author-role="assistant"], [data-author="assistant"], '
            '[role="article"]'
        ),
        "generation_active_selector": '[data-testid="stop-button"]',
        "response_continue_selector": (
            'button:has-text("Continue generating"), '
            'button[aria-label*="Continue generating" i]'
        ),
        "send_button_selector": (
            'button[data-testid="send-button"], button[aria-label^="Send" i], '
            'button[aria-label^="Submit" i], button[title^="Send" i]'
        ),
        "user_message_selector": '[data-message-author-role="user"]',
        "upload_pending_selector": (
            '[data-testid*="upload-progress"], [aria-label*="Uploading"]'
        ),
        "attachment_ready_selector": (
            '[data-testid*="attachment"], [data-testid*="file-thumbnail"], '
            'button[aria-label*="Remove file" i]'
        ),
        "model_picker_selector": (
            'button.__composer-pill[aria-haspopup="menu"], '
            'button[data-testid="model-switcher-dropdown-button"], '
            'button[aria-label*="model" i]'
        ),
        "model_option_selector": (
            '[role="menuitemradio"], [role="menuitem"][data-testid*="model"]'
        ),
        "output_download_selector": (
            'a[download][href], a[href^="sandbox:"], a[href*="/files/"]'
        ),
    },
    "m365_copilot_browser": {
        "new_chat_name": "New chat", "prompt_role": "textbox",
        "prompt_selector": (
            'span[role="textbox"][aria-label="Message Copilot"], '
            '[role="textbox"][aria-label*="Message Copilot" i], '
            '[contenteditable="true"][aria-label*="Message Copilot" i]'
        ),
        "sign_in_selector": (
            'a[href*="login.microsoftonline.com"], button:has-text("Sign in"), '
            'a:has-text("Sign in")'
        ),
        "attachment_selector": 'input[type="file"]',
        "attachment_trigger_selector": (
            'button[data-testid="chat-input-attach-button"], '
            'button[aria-label="Add"], button[title="Add"]'
        ),
        "attachment_action_selector": (
            '[role="menuitem"]:has-text("Upload"), '
            '[role="menuitem"]:has-text("device"), '
            '[role="menuitem"]:has-text("computer"), '
            'button:has-text("Upload from this device"), '
            'button:has-text("Upload file")'
        ),
        "response_selector": (
            '[data-testid="copilot-response"], [data-testid*="response" i], '
            '[data-testid*="ai-message" i], [data-message-author-role="assistant"], '
            '[data-author="assistant"], [role="article"]'
        ),
        "response_envelope_selector": (
            'pre, code, [data-testid*="response" i], [data-testid*="message" i], '
            '[data-message-author-role="assistant"], [data-author="assistant"], '
            '[role="article"]'
        ),
        "generation_active_selector": '[aria-label="Stop generating"]',
        "response_continue_selector": (
            'button:has-text("Continue generating"), '
            'button[aria-label*="Continue generating" i], '
            'button:has-text("Continue response")'
        ),
        "send_button_selector": (
            'button[data-testid*="send" i], button[aria-label^="Send" i], '
            'button[aria-label^="Submit" i], button[title^="Send" i], '
            'button[title^="Submit" i]'
        ),
        "user_message_selector": (
            '[data-testid*="user-message" i], [data-message-author-role="user"], '
            '[data-author="user"]'
        ),
        "upload_pending_selector": (
            '[data-testid*="upload-progress" i], '
            '[data-testid*="attachment" i][aria-busy="true"], '
            '[data-testid*="file" i][aria-busy="true"]'
        ),
        "attachment_ready_selector": (
            '[data-testid*="attachment" i], [data-testid*="file-chip" i], '
            'button[aria-label*="Remove attachment" i]'
        ),
        "new_design_toggle_selector": (
            '[role="switch"][aria-label*="new design" i], '
            '[role="switch"][aria-label*="new experience" i], '
            '[role="switch"][aria-label*="new copilot" i], '
            'input[type="checkbox"][aria-label*="new design" i], '
            'input[type="checkbox"][aria-label*="new experience" i], '
            'button[aria-label*="new design" i], button[aria-label*="new experience" i], '
            'button[aria-label*="new copilot" i], button:text-is("New design"), '
            'button:has-text("Try the new design"), '
            'button:has-text("Switch to the new design"), '
            'button:has-text("Try the new Copilot")'
        ),
        "model_picker_selector": (
            'button[data-testid*="model" i], button[aria-label*="model" i], '
            'button[title*="model" i]'
        ),
        "model_option_selector": (
            '[role="menuitemradio"], [role="option"], [data-testid*="model-option" i], '
            '[role="menu"] button[data-testid*="model" i]'
        ),
        "model_submenu_selector": (
            '[role="menuitem"][aria-haspopup="menu"], '
            '[role="option"][aria-haspopup="menu"], '
            '[role="menuitem"][aria-expanded], [role="option"][aria-expanded], '
            '[role="menu"] button[aria-haspopup="menu"], '
            '[data-testid*="model-submenu" i], '
            '[role="menuitem"]:has-text("More"), [role="option"]:has-text("More"), '
            'button:text-is("More"), [role="menuitem"]:has-text("GPT models"), '
            '[role="option"]:has-text("GPT models"), button:has-text("GPT models"), '
            '[role="menuitem"]:text-is("GPT"), button:text-is("GPT"), '
            '[role="menuitem"]:text-is("OpenAI"), button:text-is("OpenAI"), '
            '[role="menuitem"]:text-is("ChatGPT"), button:text-is("ChatGPT")'
        ),
        "output_download_selector": (
            'a[download][href], a[href*="download"], button[aria-label*="Download"]'
        ),
    },
}
