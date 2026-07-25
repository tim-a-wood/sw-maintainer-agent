from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any
try:
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:
    def sync_playwright():
        raise RuntimeError("Playwright is not installed. Install dependencies before running browser automation.")
INPUT_SELECTORS = ['textarea[aria-label*="Copilot" i]', 'textarea[placeholder*="Message" i]', 'textarea[placeholder*="Ask" i]', '[contenteditable="true"]']
SEND_BUTTON_SELECTORS = ['button[aria-label*="Send" i]', 'button[aria-label*="Submit" i]', 'button[title*="Send" i]']
GENERATION_INDICATORS = ['button[aria-label*="Stop" i]', '[aria-busy="true"]', 'text=/Stop generating/i']
ATTACH_BUTTON_SELECTORS = ['button[aria-label*="Attach" i]', 'button[aria-label*="Upload" i]', 'button[title*="Attach" i]', 'button[title*="Upload" i]', 'button:has-text("Attach")', 'button:has-text("Upload")']
ALLOWED_ATTACH_EXTENSIONS = {'.txt', '.md', '.markdown', '.json', '.html', '.htm', '.xml', '.yaml', '.yml', '.csv', '.tsv', '.py', '.js', '.ts', '.css', '.cmd', '.bat', '.ps1', '.zip'}
ELEMENT_TIMEOUT_MS = 30_000
GLOBAL_TIMEOUT_MS = 90_000
POLL_INTERVAL_MS = 250
@dataclass
class GlobalDeadline:
    started: float
    timeout_ms: int = GLOBAL_TIMEOUT_MS
    def remaining_ms(self) -> int:
        return max(1, int(self.timeout_ms - (time.monotonic() - self.started) * 1000))
    def bounded_timeout(self, requested_ms: int) -> int:
        return max(1, min(requested_ms, self.remaining_ms()))
def normalize_url(url: str) -> str:
    return (url or '').replace('&amp;', '&')
def first_visible_locator(page: Any, selectors: list[str], timeout_ms: int) -> Any | None:
    end = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < end:
        for selector in selectors:
            try:
                loc = page.locator(selector).last
                if loc.count() > 0 and loc.is_visible(timeout=250):
                    return loc
            except Exception:
                pass
        page.wait_for_timeout(POLL_INTERVAL_MS)
    return None
def launch_context(playwright: Any, profile_dir: Path, browser_channel: str):
    Path(profile_dir).mkdir(parents=True, exist_ok=True)
    opts = {'user_data_dir': str(profile_dir), 'headless': False, 'viewport': {'width': 1400, 'height': 950}, 'args': ['--start-maximized'], 'accept_downloads': True}
    if browser_channel:
        opts['channel'] = browser_channel
    return playwright.chromium.launch_persistent_context(**opts)
def get_or_create_page(ctx: Any):
    return ctx.pages[0] if ctx.pages else ctx.new_page()
def wait_for_chat_ready(page: Any, deadline: GlobalDeadline):
    loc = first_visible_locator(page, INPUT_SELECTORS, deadline.bounded_timeout(ELEMENT_TIMEOUT_MS))
    if loc is None:
        raise RuntimeError('Chat input not found. If session expired, run /login first.')
    return loc
def validate_attach_file(input_file: Path) -> Path:
    path = Path(input_file).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f'Input file not found: {path}')
    if path.is_dir():
        raise IsADirectoryError(f'Input path is a directory: {path}')
    if path.suffix.lower() not in ALLOWED_ATTACH_EXTENSIONS:
        raise ValueError(f'Input file extension is not allowed: {path.suffix.lower() or "<none>"}')
    return path
def attach_file_to_chat(page: Any, input_file: Path, deadline: GlobalDeadline) -> None:
    path = validate_attach_file(input_file)
    file_input = page.locator('input[type="file"]').last
    if file_input.count() == 0:
        button = first_visible_locator(page, ATTACH_BUTTON_SELECTORS, deadline.bounded_timeout(ELEMENT_TIMEOUT_MS))
        if button is None:
            raise RuntimeError('No file input or attach/upload button was found in the chat UI.')
        button.click(timeout=deadline.bounded_timeout(ELEMENT_TIMEOUT_MS))
        file_input = page.locator('input[type="file"]').last
    file_input.set_input_files(str(path), timeout=deadline.bounded_timeout(ELEMENT_TIMEOUT_MS))

SEND_READY_SELECTORS = [
    'button[aria-label*="Send" i]:not([disabled]):not([aria-disabled="true"])',
    '[role="button"][aria-label*="Send" i]:not([aria-disabled="true"])',
]


def _locator_is_enabled(locator) -> bool:
    try:
        if not locator.is_visible(timeout=250):
            return False
    except Exception:
        return False
    try:
        if locator.get_attribute("disabled", timeout=250) is not None:
            return False
    except Exception:
        pass
    try:
        if (locator.get_attribute("aria-disabled", timeout=250) or "").lower() == "true":
            return False
    except Exception:
        pass
    return True


def _find_enabled_send_button(page, deadline, timeout_ms: int = 30_000):
    end = time.monotonic() + deadline.bounded_timeout(timeout_ms) / 1000
    selectors = SEND_READY_SELECTORS + SEND_BUTTON_SELECTORS
    while time.monotonic() < end:
        for selector in selectors:
            try:
                loc = page.locator(selector).last
                if loc.count() > 0 and _locator_is_enabled(loc):
                    return loc
            except Exception:
                continue
        page.wait_for_timeout(250)
    return None


def _insert_prompt_text(page, box, prompt: str, deadline) -> None:
    box.click(timeout=deadline.bounded_timeout(10_000))
    try:
        box.fill(prompt, timeout=deadline.bounded_timeout(30_000))
        return
    except Exception as fill_error:
        print(f"[WARN] Prompt fill failed; using keyboard insertion fallback: {fill_error}", flush=True)
    box.click(timeout=deadline.bounded_timeout(10_000))
    try:
        box.press("Control+A", timeout=deadline.bounded_timeout(5_000))
        box.press("Backspace", timeout=deadline.bounded_timeout(5_000))
    except Exception:
        pass
    page.keyboard.insert_text(prompt)



def submit_prompt(page, box, prompt: str, deadline: GlobalDeadline) -> None:
    _insert_prompt_text(page, box, prompt, deadline)

    send = _find_enabled_send_button(page, deadline, timeout_ms=45_000)
    if send is not None:
        send.click(timeout=deadline.bounded_timeout(15_000))
        return

    # Last-resort path: some Copilot input variants submit on Enter even when the
    # visible toolbar button has not updated yet. Do this after the enabled-button
    # wait so Playwright does not burn its full click timeout on a disabled button.
    try:
        box.press("Enter", timeout=deadline.bounded_timeout(10_000))
        return
    except Exception as enter_error:
        raise RuntimeError(
            "Copilot Send button did not become enabled after prompt insertion, "
            "and Enter submission also failed."
        ) from enter_error


def wait_for_response_complete(page: Any, deadline: GlobalDeadline) -> None:
    quiet_since = None
    while deadline.remaining_ms() > 1:
        busy = first_visible_locator(page, GENERATION_INDICATORS, 250) is not None
        if busy:
            quiet_since = None
        else:
            quiet_since = quiet_since or time.monotonic()
            if time.monotonic() - quiet_since >= 3:
                return
        page.wait_for_timeout(POLL_INTERVAL_MS)
    raise TimeoutError('Automation timed out while waiting for Copilot response.')
def run_login_flow(url: str, profile_dir: Path, browser_channel: str) -> int:
    with sync_playwright() as pw:
        ctx = launch_context(pw, profile_dir, browser_channel)
        try:
            page = get_or_create_page(ctx)
            page.goto(normalize_url(url), wait_until='domcontentloaded', timeout=ELEMENT_TIMEOUT_MS)
            print('\nLogin mode started. Complete authentication in the browser, then press ENTER here.\n', flush=True)
            input()
            return 0
        finally:
            ctx.close()

NEW_CHAT_SELECTORS = [
    'button[aria-label*="New chat" i]', 'a[aria-label*="New chat" i]',
    'button[title*="New chat" i]', '[role="button"]:has-text("New chat")',
]
MESSAGE_SELECTORS = ['[data-content="ai-message"]','[data-testid*="message" i]','article']

@dataclass(frozen=True)
class ChatSession:
    url: str
    fingerprint: str

def start_fresh_chat(page: Any, deadline: GlobalDeadline) -> ChatSession:
    """Create and verify a stateless Copilot chat before attachment."""
    import hashlib
    candidates=[]
    for selector in NEW_CHAT_SELECTORS:
        try:
            loc=page.locator(selector)
            for index in range(loc.count()):
                item=loc.nth(index)
                if item.is_visible(timeout=250): candidates.append(item)
        except Exception:
            continue
    if len(candidates)!=1:
        raise RuntimeError(f"Expected exactly one supported new-chat action; found {len(candidates)}.")
    candidates[0].click(timeout=deadline.bounded_timeout(ELEMENT_TIMEOUT_MS))
    box=wait_for_chat_ready(page,deadline)
    try:
        value=box.input_value(timeout=1000)
    except Exception:
        value=(box.text_content(timeout=1000) or "")
    if value.strip(): raise RuntimeError("Fresh-chat composer is not empty.")
    for selector in MESSAGE_SELECTORS:
        try:
            if page.locator(selector).count()>0: raise RuntimeError("Prior conversation messages remain after new-chat activation.")
        except RuntimeError: raise
        except Exception: pass
    url=str(getattr(page,"url","") or "")
    fingerprint=hashlib.sha256(f"{url}|{time.time_ns()}".encode()).hexdigest()[:24]
    return ChatSession(url,fingerprint)
