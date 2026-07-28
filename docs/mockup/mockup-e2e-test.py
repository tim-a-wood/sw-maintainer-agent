import sys
"""End-to-end browser test for the specification mockup.

Run: python3 mockup-e2e-test.py
Needs: pip install playwright, and a Chromium that Playwright can find
(PLAYWRIGHT_BROWSERS_PATH or a default install).
"""
import os
from playwright.sync_api import sync_playwright

url = "file://" + os.path.abspath(os.path.join(os.path.dirname(__file__), "simple-ui-mockup.html"))
failures = []

def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get("MOCKUP_CHROME") or None,
                                args=["--no-sandbox"])
    page = browser.new_page(viewport={"width": 900, "height": 950})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    page.wait_for_timeout(400)

    check("overlay hidden means display none",
          page.evaluate("getComputedStyle(document.getElementById('overlay')).display") == "none")
    check("home renders", page.locator("text=Change software").count() == 1)

    # ---- history of past runs, read-only ----
    page.locator(".bigchoice", has_text="History").click()
    page.wait_for_timeout(150)
    check("history lists saved run 0142", page.locator("text=Run 0142").count() == 1)
    page.locator("button:has-text('Run 0141')").click()
    page.wait_for_timeout(150)
    check("saved run timeline read-only", page.locator("text=The timeline is read-only").count() == 1)
    check("saved run shows a revert event", page.locator("text=Went back to iteration 3").count() == 1)
    check("saved run shows superseded marks", page.locator(".tagsuper").count() == 2)
    page.get_by_role("button", name="Back", exact=True).click()
    page.wait_for_timeout(100)
    page.get_by_role("button", name="Back", exact=True).click()
    page.wait_for_timeout(100)

    # ---- describe with run files ----
    page.get_by_role("button", name="Change software").click()
    page.get_by_role("button", name="Use an example").click()
    page.get_by_role("button", name="Import…").click()
    page.wait_for_timeout(100)
    check("run file chip added", page.locator(".chip").count() == 1)
    page.get_by_role("button", name="Start", exact=True).click()
    page.wait_for_timeout(150)

    # ---- plan send: attachments and package contents ----
    check("plan send screen", page.locator("text=Copilot makes the plan.").count() == 1)
    check("packet inherits run file", page.locator(".chip .cname").count() == 1)
    page.get_by_role("button", name="Add files…").click()
    page.wait_for_timeout(100)
    check("attachment added to packet", page.locator(".chip .cname").count() == 2)
    page.locator("details.pkg-details summary").click()
    check("contents show configured documents", page.locator("text=configured document").count() == 1)
    check("contents show attachments count", page.locator("text=2 files from you").count() == 1)
    page.get_by_role("button", name="Copy OneDrive link").click()
    page.wait_for_selector("text=In sync. The link is in the clipboard.", timeout=8000)
    page.get_by_role("button", name="Continue", exact=True).click()
    page.wait_for_timeout(150)
    page.get_by_role("button", name="Paste reply").click()
    page.wait_for_timeout(2000)
    check("plan check 3 tasks", page.locator("text=Copilot proposes 3 tasks.").count() == 1)

    # ---- rescope ----
    page.get_by_role("button", name="Ask for changes").click()
    page.wait_for_timeout(150)
    page.get_by_role("button", name="Send the note").click()
    page.wait_for_timeout(150)
    check("plan again send", page.locator("text=again (2)").count() == 1)
    page.get_by_role("button", name="Copy file").click()
    page.get_by_role("button", name="Continue", exact=True).click()
    page.wait_for_timeout(150)
    page.get_by_role("button", name="Paste reply").click()
    page.wait_for_timeout(2000)
    check("plan changed to 2 tasks", page.locator("text=Copilot proposes 2 tasks.").count() == 1)

    # ---- build ----
    page.get_by_role("button", name="Accept the plan").click()
    page.wait_for_timeout(150)
    check("build send screen", page.locator("text=Copilot writes the code.").count() == 1)
    page.get_by_role("button", name="Export…").click()
    page.get_by_role("button", name="Continue", exact=True).click()
    page.wait_for_timeout(150)
    page.get_by_role("button", name="Import…").click()
    page.wait_for_timeout(150)
    page.locator(".dlpick .chip").click()
    page.wait_for_timeout(3200)
    check("review send after build", page.locator("text=Copilot examines the change.").count() == 1)

    # ---- review round 1 -> finding -> repair ----
    page.get_by_role("button", name="Copy file").click()
    page.get_by_role("button", name="Continue", exact=True).click()
    page.wait_for_timeout(150)
    page.get_by_role("button", name="Paste reply").click()
    page.wait_for_timeout(2000)
    check("review finding shown", page.locator("text=Copilot found 1 point to repair.").count() == 1)
    page.get_by_role("button", name="Repair with Copilot").click()
    page.wait_for_timeout(150)
    check("repair send screen", page.locator("text=Copilot repairs the code.").count() == 1)
    page.get_by_role("button", name="Copy file").click()
    page.get_by_role("button", name="Continue", exact=True).click()
    page.wait_for_timeout(150)
    page.get_by_role("button", name="Import…").click()
    page.wait_for_timeout(150)
    page.locator(".dlpick .chip").click()
    page.wait_for_timeout(3200)
    check("review round 2 send", page.locator("text=· again").count() >= 1)

    # ---- review approves -> tests ----
    page.get_by_role("button", name="Copy file").click()
    page.get_by_role("button", name="Continue", exact=True).click()
    page.wait_for_timeout(150)
    page.get_by_role("button", name="Paste reply").click()
    page.wait_for_selector("text=All checks passed.", timeout=15000)
    check("checks passed", True)

    # ---- live timeline, go back, superseded ----
    page.locator(".foot-btn", has_text="History").click()
    page.wait_for_timeout(150)
    check("live timeline shows review approved", page.locator(".it", has_text="Review approved").count() == 1)
    check("undo button enabled", page.get_by_role("button", name="Undo the last iteration").is_enabled())
    page.locator(".it", has_text="Plan approved").locator(".it-act").click()
    page.wait_for_timeout(150)
    check("revert confirm opens", page.locator("text=Go back to iteration").count() >= 1)
    page.get_by_role("button", name="Go back", exact=True).click()
    page.wait_for_timeout(200)
    check("revert lands on build send", page.locator("text=Copilot writes the code.").count() == 1)
    page.locator(".foot-btn", has_text="History").click()
    page.wait_for_timeout(150)
    check("timeline shows revert event", page.locator(".it", has_text="Went back to iteration").count() >= 1)
    check("later iterations superseded", page.locator(".tagsuper").count() >= 3)

    # ---- navigator: jump to save, finish run ----
    page.select_option("#navSel", "save")
    page.wait_for_timeout(200)
    check("navigator jumps to save", page.locator("text=files changed.").count() == 1)
    page.get_by_role("button", name="Accept and save").click()
    page.wait_for_timeout(150)
    check("done screen", page.locator("text=The change is saved.").count() == 1)
    page.get_by_role("button", name="View the history").click()
    page.wait_for_timeout(150)
    check("history shows saved 0143", page.locator("button:has-text('Run 0143')").count() == 1)

    # ---- settings: task prompt override + documents ----
    page.select_option("#navSel", "set-tasks")
    page.wait_for_timeout(200)
    check("task settings project tab", page.locator("text=Documents for every packet").count() == 1)
    page.locator(".seg button", has_text="Build").click()
    page.wait_for_timeout(100)
    check("build uses built-in prompt", page.locator("text=This task uses the built-in prompt.").count() == 1)
    page.get_by_role("button", name="Change the prompt for this task").click()
    page.wait_for_timeout(100)
    check("override textarea appears", page.locator("#tPrompt").count() == 1)
    page.locator("button:has-text('Add a document…')").click()
    page.wait_for_timeout(100)
    check("build document added", page.locator(".chip .cname").count() >= 1)
    page.get_by_role("button", name="Save", exact=True).click()
    page.wait_for_timeout(100)
    page.select_option("#navSel", "send-build")
    page.wait_for_timeout(200)
    page.locator("details.pkg-details summary").click()
    check("build packet counts project+task documents", page.locator("text=2 configured documents").count() == 1)

    print("page errors:", errors if errors else "none")
    browser.close()

sys.exit(1 if failures else 0)
