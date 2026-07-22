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
            try:
                page.goto(str(self.config["url"]), wait_until="domcontentloaded")
                sign_in_selector = selectors.get("sign_in_selector")
                if sign_in_selector and page.locator(sign_in_selector).is_visible():
                    raise ProviderError("Interactive sign-in or MFA is required in the visible browser.")
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
                prompt = page.get_by_role(prompt_role).last
                serialized = json.dumps(asdict(request), ensure_ascii=False, separators=(",", ":"))
                digest = hashlib.sha256(serialized.encode()).hexdigest()
                exchange_dir = self._new_exchange_dir(digest)
                attachment_selector = selectors.get("attachment_selector")
                transport = "text"
                attachment_names: list[str] = []
                package_bytes = len(serialized.encode())
                if attachment_selector:
                    package = build_exchange_package(request, exchange_dir / "packages")
                    page.locator(attachment_selector).set_input_files(
                        [str(path) for path in package.paths]
                    )
                    upload_complete = selectors.get("upload_complete_selector")
                    if upload_complete:
                        page.locator(upload_complete).wait_for(
                            state="visible", timeout=int(self.config.get("timeout_ms", 300_000)))
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
                            prompt.fill(chunk)
                            prompt.press("Enter")
                            page.wait_for_function(
                                "([selector, hash]) => { const nodes = document.querySelectorAll(selector); "
                                "return nodes.length && nodes[nodes.length - 1].textContent.includes(hash); }",
                                arg=[acknowledgement, chunk_hash],
                                timeout=int(self.config.get("timeout_ms", 300_000)))
                        message = (f"{request.instructions}\nAll {len(chunks)} chunks are complete. "
                                   f"Package SHA-256: {digest}. Return the required envelope.")
                        transport = "chunks"
                prompt.fill(message)
                prompt.press("Enter")
                response = page.locator(response_selector).last
                response.wait_for(state="visible", timeout=int(self.config.get("timeout_ms", 300_000)))
                generation_selector = selectors.get("generation_active_selector")
                if generation_selector:
                    page.locator(generation_selector).wait_for(
                        state="hidden", timeout=int(self.config.get("timeout_ms", 300_000)))
                # text_content preserves patch indentation. inner_text collapses spaces.
                raw = response.text_content() or ""
                assert_no_secrets(raw, "browser response")
                (exchange_dir / f"{request.role}-initial.txt").write_text(raw, encoding="utf-8")
                repaired = False
                try:
                    parsed = parse_response(_extract_json(raw), request, self.name)
                except ProviderError:
                    repaired = True
                    previous = raw
                    prompt.fill(
                        "Your last response did not match the required envelope. Return only the "
                        "complete JSON envelope for the same run, task, and role.")
                    prompt.press("Enter")
                    page.wait_for_function(
                        "([selector, previous]) => { const nodes = document.querySelectorAll(selector); "
                        "return nodes.length && nodes[nodes.length - 1].textContent !== previous; }",
                        arg=[response_selector, previous],
                        timeout=int(self.config.get("timeout_ms", 300_000)))
                    response = page.locator(response_selector).last
                    response.wait_for(
                        state="visible", timeout=int(self.config.get("timeout_ms", 300_000)))
                    if generation_selector:
                        page.locator(generation_selector).wait_for(
                            state="hidden", timeout=int(self.config.get("timeout_ms", 300_000)))
                    raw = response.text_content() or ""
                    assert_no_secrets(raw, "browser repair response")
                    (exchange_dir / f"{request.role}-repair.txt").write_text(
                        raw, encoding="utf-8")
                    parsed = parse_response(_extract_json(raw), request, self.name)
                parsed = replace(
                    parsed,
                    conversation_id=f"{self.name}-{exchange_dir.name}",
                )
                if request.role == "implement":
                    output_zip = self._download_output_zip(
                        page, selectors, request, exchange_dir.name)
                    parsed.content["_maintain_output_zip"] = output_zip.name
                page.screenshot(path=str(exchange_dir / f"{request.role}.png"), full_page=True)
                (exchange_dir / f"{request.role}.txt").write_text(raw, encoding="utf-8")
                (exchange_dir / f"{request.role}-transport.json").write_text(
                    json.dumps({"transport": transport, "sha256": digest,
                                "bytes": package_bytes, "attachments": attachment_names,
                                "conversation_id": parsed.conversation_id,
                                "schema_repair": repaired,
                                "output_zip": (output_zip.name if request.role == "implement"
                                               else None)}),
                    encoding="utf-8")
                return parsed
            except Exception as exc:
                exchange_dir = locals().get("exchange_dir") or self._new_exchange_dir(
                    hashlib.sha256(f"{request.task_id}-{request.role}-{time.time_ns()}".encode()).hexdigest())
                diagnostic = {"error": str(exc)}
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
                raise ProviderError(f"Browser provider stopped safely: {exc}") from exc
            finally:
                context.close()

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
        "generation_active_selector": '[data-testid="stop-button"]',
        "output_download_selector": (
            'a[download][href], a[href^="sandbox:"], a[href*="/files/"]'
        ),
    },
    "m365_copilot_browser": {
        "new_chat_name": "New chat", "prompt_role": "textbox",
        "attachment_selector": 'input[type="file"]',
        "response_selector": '[data-testid="copilot-response"]',
        "generation_active_selector": '[aria-label="Stop generating"]',
        "output_download_selector": (
            'a[download][href], a[href*="download"], button[aria-label*="Download"]'
        ),
    },
}
