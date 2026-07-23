"""Visible Playwright providers for M365 Copilot and ChatGPT."""

from __future__ import annotations

import json
import hashlib
import os
import re
import time
import zipfile
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from maintain.errors import ProviderError
from maintain.exchange_package import build_exchange_package
from maintain.models import ProviderCapabilities, ProviderRequest
from maintain.locking import FileLock
from maintain.security import assert_no_secrets

from .base import Provider
from .command import parse_response


class BrowserProvider(Provider):
    capabilities = ProviderCapabilities(browser_automation=True, sandbox_code_execution=True)

    def __init__(self, name: str, config: dict[str, Any], evidence_dir: Path) -> None:
        self.name, self.config, self.evidence_dir = name, config, evidence_dir
        profile = str(config.get("profile_dir") or "")
        if not profile:
            raise ProviderError("The browser provider needs a dedicated profile directory.")
        self.profile_dir = Path(os.path.expandvars(profile)).expanduser().resolve()

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
                page.goto(str(self.config["url"]), wait_until="domcontentloaded")
                sign_in_selector = selectors.get("sign_in_selector")
                if sign_in_selector and page.locator(sign_in_selector).is_visible():
                    raise ProviderError(
                        "Interactive sign-in or MFA is required. Run maintain provider login first.")
                if expected_context:
                    context_label = page.locator(context_selector).inner_text(timeout=30_000).strip()
                    if expected_context.casefold() not in context_label.casefold():
                        raise ProviderError(
                            f"The browser context does not match {expected_context!r}.")
                if expected_identity:
                    identity_label = page.locator(identity_selector).inner_text(timeout=30_000).strip()
                    if expected_identity.casefold() not in identity_label.casefold():
                        raise ProviderError(
                            f"The signed-in identity does not match {expected_identity!r}.")
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
                page.goto(str(self.config["url"]), wait_until="domcontentloaded")
                try:
                    input("Complete sign-in in the browser. Press Enter here when sign-in is complete: ")
                finally:
                    context.close()
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"Browser login failed: {exc}") from exc

    def available_models(self) -> list[str]:
        """Read the models offered by the signed-in browser account."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ProviderError("Install Maintain with the browser extra and install Chromium.") from exc
        selectors = {**PAGE_OBJECTS.get(self.name, {}), **self.config.get("selectors", {})}
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.profile_dir.parent / f".{self.profile_dir.name}.maintain.lock"
        with FileLock(lock_path, f"browser profile {self.name}"), sync_playwright() as playwright:
            context = self._launch_context(playwright)
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(str(self.config["url"]), wait_until="domcontentloaded")
                sign_in_selector = selectors.get("sign_in_selector")
                if sign_in_selector and page.locator(sign_in_selector).is_visible():
                    raise ProviderError("Interactive sign-in or MFA is required in the visible browser.")
                self._enable_preferred_design(page, selectors)
                return self._model_options(page, selectors)
            except ProviderError:
                raise
            except Exception as exc:
                try:
                    page.screenshot(path=str(
                        self.evidence_dir / f"{self.name}-model-discovery-failure.png"),
                        full_page=True)
                except Exception:
                    pass
                raise ProviderError(f"Could not retrieve browser models: {exc}") from exc
            finally:
                context.close()

    def exchange(self, request: ProviderRequest):
        from playwright.sync_api import sync_playwright

        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        selectors = {**PAGE_OBJECTS.get(self.name, {}), **self.config.get("selectors", {})}
        prompt_role = selectors.get("prompt_role", "textbox")
        response_selector = selectors.get("response_selector")
        new_chat_name = selectors.get("new_chat_name", "New chat")
        if not response_selector:
            raise ProviderError("Configure selectors.response_selector for the approved web UI.")
        lock_path = self.profile_dir.parent / f".{self.profile_dir.name}.maintain.lock"
        with FileLock(lock_path, f"browser profile {self.name}"), sync_playwright() as playwright:
            context = self._launch_context(playwright)
            page = context.pages[0] if context.pages else context.new_page()
            stage = "open Copilot"
            try:
                page.goto(str(self.config["url"]), wait_until="domcontentloaded")
                sign_in_selector = selectors.get("sign_in_selector")
                if sign_in_selector and page.locator(sign_in_selector).is_visible():
                    raise ProviderError("Interactive sign-in or MFA is required in the visible browser.")
                self._enable_preferred_design(page, selectors)
                identity_selector = selectors.get("identity_selector")
                expected_context = _configured_value(
                    self.config.get("expected_tenant") or self.config.get("expected_workspace"))
                expected_identity = _configured_value(self.config.get("expected_identity"))
                if expected_context or expected_identity:
                    context_selector_name = (
                        "tenant_selector" if self.name == "m365_copilot_browser"
                        else "workspace_selector")
                    context_selector = selectors.get(context_selector_name)
                    if expected_context:
                        if not context_selector:
                            raise ProviderError("Configure a context selector for browser verification.")
                        context_label = page.locator(context_selector).inner_text(
                            timeout=30_000).strip()
                        if expected_context.casefold() not in context_label.casefold():
                            raise ProviderError(
                                f"The browser context does not match {expected_context!r}.")
                    if expected_identity:
                        if not identity_selector:
                            raise ProviderError("Configure an identity selector for browser verification.")
                        identity_label = page.locator(identity_selector).inner_text(
                            timeout=30_000).strip()
                        if expected_identity.casefold() not in identity_label.casefold():
                            raise ProviderError(
                                f"The signed-in identity does not match {expected_identity!r}.")
                page.get_by_role("link", name=new_chat_name).or_(
                    page.get_by_role("button", name=new_chat_name)).first.click(timeout=30_000)
                stage = "select model"
                selected_model = str(self.config.get("model") or "").strip()
                if selected_model:
                    self._select_model(page, selectors, selected_model)
                prompt = page.get_by_role(prompt_role).last
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
                    page.locator(attachment_selector).set_input_files(
                        [str(path) for path in package.paths]
                    )
                    self._wait_for_attachments(page, attachment_selector, package.paths, selectors)
                    digest = package.sha256
                    package_bytes = package.bytes
                    attachment_names = [path.name for path in package.paths]
                    message = (
                        f"Read all {len(package.paths)} attached package files. Start with TASK.md, "
                        f"then use the indexed CODEBASE.md and MANIFEST.json. Package SHA-256: "
                        f"{digest}. Follow the output instructions exactly."
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
                self._submit(page, prompt, message, selectors)
                stage = "read Copilot response"
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
                        "Your last response did not match the required envelope. Return only the "
                        "complete JSON envelope for the same run, task, and role.")
                    previous_responses = self._visible_texts(page, response_selector)
                    self._submit(page, prompt, repair_message, selectors)
                    raw = self._wait_for_response_text(
                        page, selectors, request, previous_responses)
                    assert_no_secrets(raw, "browser repair response")
                    (exchange_dir / f"{request.role}-repair.txt").write_text(
                        raw, encoding="utf-8")
                    parsed = parse_response(_extract_json(raw), request, self.name)
                parsed = replace(
                    parsed,
                    conversation_id=f"{self.name}-{exchange_dir.name}",
                )
                if request.role == "implement":
                    stage = "download implementation"
                    output_zip = self._download_output_zip(
                        page, selectors, request, exchange_dir.name)
                    parsed.content["_maintain_output_zip"] = output_zip.name
                page.screenshot(path=str(exchange_dir / f"{request.role}.png"), full_page=True)
                (exchange_dir / f"{request.role}.txt").write_text(raw, encoding="utf-8")
                (exchange_dir / f"{request.role}-transport.json").write_text(
                    json.dumps({"transport": transport, "sha256": digest,
                                "bytes": package_bytes, "attachments": attachment_names,
                                "model": selected_model or None,
                                "conversation_id": parsed.conversation_id,
                                "schema_repair": repaired,
                                "output_zip": (output_zip.name if request.role == "implement"
                                               else None)}),
                    encoding="utf-8")
                return parsed
            except Exception as exc:
                exchange_dir = locals().get("exchange_dir") or self._new_exchange_dir(
                    hashlib.sha256(f"{request.task_id}-{request.role}-{time.time_ns()}".encode()).hexdigest())
                diagnostic = {"error": str(exc), "stage": stage}
                try:
                    page.screenshot(path=str(exchange_dir / f"{request.role}-failure.png"),
                                    full_page=True)
                except Exception as screenshot_error:
                    diagnostic["screenshot_error"] = str(screenshot_error)
                try:
                    diagnostic["url"] = page.url
                    diagnostic["title"] = page.title()
                    excerpt = page.locator("body").inner_text(timeout=2_000)[:20_000]
                    assert_no_secrets(excerpt, "browser diagnostic")
                    diagnostic["visible_text"] = excerpt
                except Exception:
                    diagnostic["visible_text"] = "[omitted by safety check]"
                (exchange_dir / f"{request.role}-failure.json").write_text(
                    json.dumps(diagnostic), encoding="utf-8")
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
        start_timeout = int(self.config.get(
            "response_start_timeout_ms",
            min(int(self.config.get("timeout_ms", 300_000)), 90_000),
        ))
        deadline = time.monotonic() + start_timeout / 1_000
        latest = ""
        latest_at = time.monotonic()
        while time.monotonic() < deadline:
            candidates = self._response_candidates(
                page, response_selector, envelope_selector, request, previous_texts)
            if candidates:
                candidate = min(candidates, key=len)
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
                    return candidate
                generating = bool(generation_selector and any(
                    node.is_visible() for node in page.locator(generation_selector).all()))
                if not generating and time.monotonic() - latest_at >= 1.0:
                    return latest
            page.wait_for_timeout(250)
        if latest:
            return latest
        raise ProviderError(
            "Copilot did not expose its response to browser automation. "
            "The visible page was saved in the failure evidence.")

    @staticmethod
    def _response_candidates(page, response_selector: str, envelope_selector: str,
                             request: ProviderRequest, previous_texts: list[str]) -> list[str]:
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
            """(body, tokens) => {
              const visible = node => {
                const style = getComputedStyle(node);
                const rect = node.getBoundingClientRect();
                return style.visibility !== 'hidden' && style.display !== 'none' &&
                       rect.width > 0 && rect.height > 0;
              };
              const matches = node => {
                const text = node.textContent || '';
                return visible(node) && tokens.every(token => text.includes(token));
              };
              return [...body.querySelectorAll('*')]
                .filter(node => matches(node) && ![...node.children].some(matches))
                .map(node => node.textContent || '');
            }""",
            [request.run_id, request.task_id, request.role])
        for text in token_matches:
            if text and text not in found:
                found.append(text)
        return found

    @staticmethod
    def _visible_texts(page, selector: str) -> list[str]:
        return [node.text_content() or "" for node in page.locator(selector).all()
                if node.is_visible()]

    def _submit(self, page, prompt, message: str, selectors: dict[str, Any]) -> None:
        """Submit only after the web UI says its Send control is ready."""
        timeout = int(self.config.get("timeout_ms", 300_000))
        user_message_selector = selectors.get("user_message_selector")
        previous_user_messages = (page.locator(user_message_selector).count()
                                  if user_message_selector else 0)
        prompt.fill(message)
        send_selector = selectors.get("send_button_selector")
        if send_selector:
            send = page.locator(send_selector).last
            send.wait_for(state="visible", timeout=timeout)
            handle = send.element_handle(timeout=timeout)
            page.wait_for_function(
                "button => !button.disabled && button.getAttribute('aria-disabled') !== 'true'",
                arg=handle, timeout=timeout)
            page.wait_for_timeout(int(self.config.get(
                "send_settle_ms", 750 if self.name == "m365_copilot_browser" else 250)))
            page.wait_for_function(
                "button => !button.disabled && button.getAttribute('aria-disabled') !== 'true'",
                arg=handle, timeout=timeout)
            send.click(timeout=timeout)
        else:
            prompt.press("Enter")
        handle = prompt.element_handle(timeout=timeout)
        page.wait_for_function(
            "field => { const value = 'value' in field ? field.value : field.textContent; "
            "return !(value || '').trim(); }",
            arg=handle, timeout=timeout)
        if user_message_selector:
            page.wait_for_function(
                "([selector, count]) => document.querySelectorAll(selector).length > count",
                arg=[user_message_selector, previous_user_messages],
                timeout=int(self.config.get("submission_confirm_timeout_ms", 30_000)))

    def _wait_for_attachments(self, page, input_selector: str, paths: tuple[Path, ...] | list[Path],
                              selectors: dict[str, Any]) -> None:
        """Wait until every selected file is attached and the UI is stably ready."""
        timeout = int(self.config.get("timeout_ms", 300_000))
        names = [path.name for path in paths]
        file_input = page.locator(input_selector).last
        input_handle = file_input.element_handle(timeout=timeout)
        page.wait_for_function(
            "([field, count]) => field.files && field.files.length === count",
            arg=[input_handle, len(names)], timeout=timeout)

        complete_selector = selectors.get("upload_complete_selector")
        ready_selector = selectors.get("attachment_ready_selector")
        pending_selector = selectors.get("upload_pending_selector")
        readiness = """
            ([completeSelector, readySelector, pendingSelector, names, expected]) => {
              const visible = node => {
                const style = getComputedStyle(node);
                const rect = node.getBoundingClientRect();
                return style.visibility !== 'hidden' && style.display !== 'none' &&
                       rect.width > 0 && rect.height > 0;
              };
              const visibleNodes = selector => selector
                ? [...document.querySelectorAll(selector)].filter(visible) : [];
              const complete = visibleNodes(completeSelector).length > 0;
              const ready = visibleNodes(readySelector).length >= expected;
              const text = document.body.innerText || '';
              const named = names.every(name => text.includes(name));
              const pending = visibleNodes(pendingSelector).length > 0;
              return (complete || ready || named) && !pending;
            }
        """
        args = [complete_selector, ready_selector, pending_selector, names, len(names)]
        page.wait_for_function(readiness, arg=args, timeout=timeout)
        page.wait_for_timeout(int(self.config.get(
            "upload_settle_ms", 2_000 if self.name == "m365_copilot_browser" else 500)))
        page.wait_for_function(readiness, arg=args, timeout=timeout)

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
        return found

    def _select_model(self, page, selectors: dict[str, Any], model: str) -> None:
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
                    return
            queued.extend(path + (label,)
                          for label in self._visible_labels(page, submenu_selector))
        self._close_model_menu(page, 4)
        raise ProviderError(
            f"The preferred model {model!r} is no longer available. Refresh the model list.")

    def _open_model_path(self, page, picker_selector: str, submenu_selector: str | None,
                         option_selector: str, path: tuple[str, ...], timeout: int) -> bool:
        self._close_model_menu(page, 4)
        page.locator(picker_selector).last.click(timeout=timeout)
        self._wait_for_visible_options(page, option_selector, timeout)
        for label in path:
            target = self._visible_option(page, submenu_selector, label)
            if target is None:
                return False
            target.click(timeout=timeout)
            page.wait_for_timeout(300)
        return True

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

    @staticmethod
    def _wait_for_visible_options(page, selector: str, timeout: int) -> None:
        page.wait_for_function(
            "selector => [...document.querySelectorAll(selector)].some(node => { "
            "const style = getComputedStyle(node); const rect = node.getBoundingClientRect(); "
            "return style.visibility !== 'hidden' && style.display !== 'none' && "
            "rect.width > 0 && rect.height > 0; })",
            arg=selector, timeout=timeout)

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


def _extract_json(text: str) -> str:
    stripped = text.strip()
    if "```" in stripped:
        parts = stripped.split("```")
        for candidate in parts[1::2]:
            candidate = candidate.removeprefix("json").strip()
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                continue
    return stripped


def _configured_value(value: object) -> str:
    shown = str(value or "").strip()
    return "" if shown.startswith("SET_") else shown


def _model_label(value: str) -> str:
    lines = [" ".join(line.split()) for line in value.splitlines() if line.strip()]
    return " · ".join(lines).removesuffix(" Selected").strip(" ✓")


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
        "attachment_selector": 'input[type="file"]',
        "response_selector": '[data-message-author-role="assistant"]',
        "response_envelope_selector": (
            'pre, code, [data-message-author-role="assistant"], [data-author="assistant"], '
            '[role="article"]'
        ),
        "generation_active_selector": '[data-testid="stop-button"]',
        "send_button_selector": (
            'button[data-testid="send-button"], button[aria-label^="Send"]'
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
        "attachment_selector": 'input[type="file"]',
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
        "send_button_selector": (
            'button[data-testid*="send" i], button[aria-label^="Send" i], '
            'button[title^="Send" i]'
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
