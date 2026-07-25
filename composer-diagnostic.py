from __future__ import annotations

import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

PROFILE_DIR = Path.home() / ".maintain" / "browser" / "m365"
OUTPUT_PATH = Path.cwd() / "composer-diagnostic.json"
COPILOT_URL = "https://copilot.cloud.microsoft/?internalredirect=M365Cloud&auth=2"
TEST_TEXT = "MAINTAIN_COMPOSER_DIAGNOSTIC"


def element_details(locator):
    return locator.evaluate(
        """node => ({
            tag: node.tagName,
            role: node.getAttribute("role"),
            ariaLabel: node.getAttribute("aria-label"),
            contenteditable: node.getAttribute("contenteditable"),
            dataLexicalEditor: node.getAttribute("data-lexical-editor"),
            className: String(node.className || ""),
            textContent: node.textContent || "",
            innerText: node.innerText || "",
            value: "value" in node ? node.value : null,
            outerHTML: node.outerHTML.slice(0, 8000)
        })"""
    )


def visible(locator):
    try:
        return locator.is_visible()
    except Exception:
        return False


def main():
    result = {
        "url": None,
        "title": None,
        "before": [],
        "after_fill": [],
        "after_keyboard": [],
        "errors": [],
    }

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            channel="msedge",
            headless=False,
        )

        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(
                COPILOT_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            page.wait_for_timeout(10000)

            result["url"] = page.url
            result["title"] = page.title()

            selectors = [
                '[role="textbox"]',
                '[contenteditable="true"]',
                'textarea',
                'input[placeholder*="Message" i]',
                '[data-lexical-editor="true"]',
            ]

            for selector in selectors:
                locator = page.locator(selector)
                for index in range(locator.count()):
                    node = locator.nth(index)
                    if visible(node):
                        details = element_details(node)
                        details["selector"] = selector
                        details["index"] = index
                        result["before"].append(details)

            textbox = page.get_by_role("textbox", name="Message Copilot").last
            textbox.wait_for(state="visible", timeout=30000)
            textbox.click()

            try:
                textbox.fill(TEST_TEXT)
                page.wait_for_timeout(1000)
            except Exception as error:
                result["errors"].append(f"fill: {type(error).__name__}: {error}")

            for selector in selectors:
                locator = page.locator(selector)
                for index in range(locator.count()):
                    node = locator.nth(index)
                    if visible(node):
                        details = element_details(node)
                        details["selector"] = selector
                        details["index"] = index
                        result["after_fill"].append(details)

            if not any(
                TEST_TEXT in str(item.get("textContent", ""))
                or TEST_TEXT in str(item.get("innerText", ""))
                or TEST_TEXT in str(item.get("value", ""))
                for item in result["after_fill"]
            ):
                textbox = page.get_by_role("textbox", name="Message Copilot").last
                textbox.click()
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                page.keyboard.insert_text(TEST_TEXT)
                page.wait_for_timeout(1000)

            for selector in selectors:
                locator = page.locator(selector)
                for index in range(locator.count()):
                    node = locator.nth(index)
                    if visible(node):
                        details = element_details(node)
                        details["selector"] = selector
                        details["index"] = index
                        result["after_keyboard"].append(details)

            result["document_active_element"] = page.evaluate(
                """() => {
                    const node = document.activeElement;
                    return node ? {
                        tag: node.tagName,
                        role: node.getAttribute("role"),
                        ariaLabel: node.getAttribute("aria-label"),
                        contenteditable: node.getAttribute("contenteditable"),
                        className: String(node.className || ""),
                        textContent: node.textContent || "",
                        innerText: node.innerText || "",
                        value: "value" in node ? node.value : null,
                        outerHTML: node.outerHTML.slice(0, 8000)
                    } : null;
                }"""
            )

            result["diagnostic_text_visible"] = page.get_by_text(
                TEST_TEXT,
                exact=False,
            ).count() > 0

            try:
                textbox = page.get_by_role("textbox", name="Message Copilot").last
                textbox.click()
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
            except Exception as error:
                result["errors"].append(
                    f"cleanup: {type(error).__name__}: {error}"
                )

            OUTPUT_PATH.write_text(
                json.dumps(result, indent=2),
                encoding="utf-8",
            )

            print(f"Diagnostic saved: {OUTPUT_PATH}")
            print(f"Final URL: {page.url}")
            print(f"Textbox candidates before input: {len(result['before'])}")
            print(f"Textbox candidates after fill: {len(result['after_fill'])}")
            print(
                "Diagnostic text visible:",
                result["diagnostic_text_visible"],
            )
            print(f"Errors: {len(result['errors'])}")

        finally:
            context.close()


if __name__ == "__main__":
    main()