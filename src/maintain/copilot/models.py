from __future__ import annotations
import re, time
from typing import Any

MODEL_CONTROL_SELECTORS = ['button[aria-label*="model" i]', 'button[title*="model" i]', '[aria-label*="model" i]', 'button:has-text("Auto")', 'button:has-text("Quick response")', 'button:has-text("Think")']
GPT_SUBMENU_SELECTORS = ['text=/^GPT$/i', 'button:has-text("GPT")', '[role="menuitem"]:has-text("GPT")']
ELEMENT_TIMEOUT_MS = 30_000
POLL_INTERVAL_MS = 250

def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()

def model_text_candidates(model: str) -> list[str]:
    cleaned = normalize_text(model)
    lowered = cleaned.lower()
    candidates: list[str] = []
    if lowered in {"gpt 5.5 think", "gpt 5.5 think deeper"}:
        candidates.extend(["GPT 5.5 Think deeper", "GPT 5.5 Think Deeper", "GPT 5.5 Think", "Think deeper", "Think Deeper"])
    elif lowered == "think deeper":
        candidates.extend(["GPT 5.5 Think deeper", "Think deeper", "Think Deeper"])
    else:
        candidates.append(cleaned)
    candidates.append(cleaned.replace(" ", ""))
    candidates.append(cleaned.replace(" ", "-"))
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))

def model_label_matches(actual: str, expected: str) -> bool:
    actual_norm = normalize_text(actual).lower()
    expected_norm = normalize_text(expected).lower()
    if not actual_norm or not expected_norm:
        return False
    if expected_norm in actual_norm or actual_norm in expected_norm:
        return True
    return expected_norm == "gpt 5.5 think deeper" and actual_norm.startswith("gpt 5.5 think")

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

def current_model_control_text(page: Any) -> str:
    parts: list[str] = []
    for selector in MODEL_CONTROL_SELECTORS:
        try:
            loc = page.locator(selector).last
            if loc.count() <= 0 or not loc.is_visible(timeout=250):
                continue
            for getter in (loc.inner_text, loc.text_content):
                try:
                    text = normalize_text(getter(timeout=500) or "")
                    if text:
                        parts.append(text)
                except Exception:
                    pass
            for attr in ("aria-label", "title"):
                try:
                    value = normalize_text(loc.get_attribute(attr, timeout=500) or "")
                    if value:
                        parts.append(value)
                except Exception:
                    pass
        except Exception:
            continue
    return " | ".join(dict.fromkeys(parts))

def model_appears_selected(page: Any, labels: list[str]) -> bool:
    control_text = current_model_control_text(page)
    for label in labels:
        if model_label_matches(control_text, label):
            print(f"[INFO] Requested model appears selected in model control: {label}", flush=True)
            return True
    if control_text:
        print(f"[WARN] Requested model is not selected; current model control text: {control_text}", flush=True)
    return False

def click_first_model_text(page: Any, labels: list[str], timeout_ms: int) -> bool:
    end = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < end:
        for label in labels:
            escaped = re.escape(label)
            locators = [
                page.locator(f'[role="menuitem"]:has-text("{label}")').last,
                page.locator(f'[role="option"]:has-text("{label}")').last,
                page.locator(f'[role="radio"]:has-text("{label}")').last,
                page.locator(f'button:has-text("{label}")').last,
                page.get_by_text(label, exact=True).last,
                page.locator(rf'text=/^\s*{escaped}\s*$/i').last,
                page.get_by_text(label, exact=False).last,
                page.locator(f'text=/{escaped}/i').last,
            ]
            for locator in locators:
                try:
                    if locator.count() > 0 and locator.is_visible(timeout=250):
                        locator.scroll_into_view_if_needed(timeout=1_000)
                        locator.click(timeout=2_000)
                        print(f"[INFO] Clicked model option: {label}", flush=True)
                        return True
                except Exception:
                    continue
        page.wait_for_timeout(POLL_INTERVAL_MS)
    return False

def open_gpt_submenu_if_present(page: Any, deadline: Any) -> bool:
    loc = first_visible_locator(page, GPT_SUBMENU_SELECTORS, min(5_000, deadline.bounded_timeout(ELEMENT_TIMEOUT_MS)))
    if loc is None:
        return False
    try:
        loc.click(timeout=deadline.bounded_timeout(ELEMENT_TIMEOUT_MS))
        return True
    except Exception:
        return False

def select_copilot_model(page: Any, model: str, deadline: Any) -> None:
    requested = normalize_text(model or "")
    if not requested or requested.lower() in {"auto", "none", "skip", "off", "disabled"}:
        print(f"[INFO] Model selection skipped: {requested or '<empty>'}.", flush=True)
        return
    labels = model_text_candidates(requested)
    print(f"[INFO] Selecting Copilot model/mode: {requested}", flush=True)
    control = first_visible_locator(page, MODEL_CONTROL_SELECTORS, deadline.bounded_timeout(ELEMENT_TIMEOUT_MS))
    if control is None:
        raise RuntimeError(f"Model selector control not found while selecting: {requested}")
    if model_appears_selected(page, labels):
        return
    control.click(timeout=deadline.bounded_timeout(ELEMENT_TIMEOUT_MS))
    print("[INFO] Opened Copilot model selector.", flush=True)
    page.wait_for_timeout(1_500)
    if click_first_model_text(page, labels, 10_000):
        page.wait_for_timeout(1_500)
        if model_appears_selected(page, labels):
            return
    control = first_visible_locator(page, MODEL_CONTROL_SELECTORS, 3_000)
    if control is not None:
        try:
            control.click(timeout=deadline.bounded_timeout(ELEMENT_TIMEOUT_MS))
            page.wait_for_timeout(1_000)
        except Exception:
            pass
    if open_gpt_submenu_if_present(page, deadline):
        page.wait_for_timeout(1_500)
        if click_first_model_text(page, labels, 10_000):
            page.wait_for_timeout(1_500)
            if model_appears_selected(page, labels):
                return
    if model_appears_selected(page, labels):
        return
    raise RuntimeError(
        "Requested Copilot model/mode was not confirmed as selected: "
        + requested
        + ". Tried labels: "
        + ", ".join(labels)
        + "\nCurrent model control text:\n"
        + current_model_control_text(page)
    )
