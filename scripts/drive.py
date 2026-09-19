"""Drive every pattern through the real browser UI and report what broke.

``scripts/smoke.py`` calls ``workflow.run()`` directly. That is useful, but it
never touches the request_info path, the diagram highlighting, the trace
capture or the approval modal - and every bug found in this repo so far has
been in exactly those places. This drives the actual page instead.

    python scripts/drive.py                 # all twelve
    python scripts/drive.py handoff         # one
    python scripts/drive.py --headed        # watch it

Needs both servers up (``make demo``) and Playwright's Chromium:

    pip install playwright && playwright install chromium

Exits non-zero if anything is wrong, so it works as a pre-session check.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request
from typing import Any

BASE = os.getenv("SHOWCASE_URL", "http://localhost:8000")
#: Some sandboxes ship a prebuilt Chromium that Playwright's own download misses.
CHROME = os.getenv("CHROME_PATH") or None

RUN_TIMEOUT_SECONDS = 90
APPROVAL_TIMEOUT_MS = 40_000


def patterns() -> list[dict[str, Any]]:
    try:
        with urllib.request.urlopen(f"{BASE}/api/patterns", timeout=5) as response:
            return json.load(response)["patterns"]
    except Exception as exc:
        print(f"Cannot reach the showcase at {BASE} ({exc}).\nStart it with:  make demo")
        raise SystemExit(2) from None


async def drive_one(page: Any, pattern: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    """Run one pattern in the browser and gather everything worth asserting on."""
    slug = pattern["slug"]
    await page.goto(f"{BASE}/?pattern={slug}", wait_until="networkidle")
    await page.wait_for_timeout(250)
    before = len(errors)

    async def approve_if_asked() -> bool:
        # The human-in-the-loop pattern suspends; answer it so the run can finish.
        try:
            await page.wait_for_selector("#approvalModal:not([hidden])", timeout=APPROVAL_TIMEOUT_MS)
            await page.click("#approveBtn")
            return True
        except Exception:
            return False

    watcher = asyncio.create_task(approve_if_asked())
    await page.click("#runBtn")

    state = "timeout"
    for _ in range(RUN_TIMEOUT_SECONDS * 2):
        await page.wait_for_timeout(500)
        state = await page.inner_text("#runState")
        if state in ("complete", "disconnected"):
            break
    if watcher.done():
        approved = watcher.result()
    else:
        # cancel() returns True when it succeeds, which is not the same thing
        # as the approval having happened.
        watcher.cancel()
        approved = False

    stats = await page.evaluate("""() => {
        const lines = [...document.querySelectorAll('#console .line')];
        return {
            lines:   lines.length,
            outputs: lines.filter(l => l.classList.contains('line-output')).length,
            errors:  lines.filter(l => l.classList.contains('line-error'))
                          .map(l => l.querySelector('.msg')?.textContent || '').slice(0, 3),
            lit:     document.querySelectorAll('.node.is-done, .node.is-active').length,
            nodes:   document.querySelectorAll('.node').length,
            fits: (() => {
                const svg = document.getElementById('diagram');
                const vb = (svg.getAttribute('viewBox') || '0 0 0 0').split(' ').map(Number);
                const boxes = [...svg.querySelectorAll('.node rect')]
                    .map(r => [+r.getAttribute('x'), +r.getAttribute('y')]);
                if (!boxes.length) return false;
                return Math.max(...boxes.map(b => b[0])) + 152 <= vb[2]
                    && Math.max(...boxes.map(b => b[1])) + 46 <= vb[3];
            })(),
        };
    }""")

    await page.click('.tab[data-tab="traces"]')
    await page.wait_for_timeout(350)
    stats["spans"] = await page.locator(".wf-row").count()
    await page.click('.tab[data-tab="log"]')

    stats["state"] = state
    stats["approved"] = approved
    stats["js"] = errors[before:]
    return stats


def faults(pattern: dict[str, Any], s: dict[str, Any]) -> list[str]:
    """Everything genuinely wrong with this run. Empty means clean."""
    out: list[str] = []
    if s["state"] != "complete":
        out.append(f"did not complete (state={s['state']})")
    if s["outputs"] == 0:
        out.append("produced no output")
    if s["errors"]:
        out.append(f"error line: {s['errors'][0][:80]}")
    if not s["fits"]:
        out.append("diagram overflows its viewBox")
    if s["lit"] == 0 and not pattern["hasCustomRunner"]:
        out.append("no diagram nodes lit up")
    if s["spans"] == 0:
        out.append("no spans captured")
    if s["js"]:
        out.append(f"page error: {s['js'][0][:80]}")
    return out


async def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    headed = "--headed" in sys.argv

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Playwright is missing.\n  pip install playwright && playwright install chromium")
        return 2

    wanted = patterns()
    if args:
        wanted = [p for p in wanted if p["slug"] in args]
        if not wanted:
            print(f"No pattern matched {args}")
            return 2

    async with async_playwright() as p:
        launch: dict[str, Any] = {"headless": not headed}
        if CHROME:
            launch["executable_path"] = CHROME
            launch["args"] = ["--no-sandbox"]
        browser = await p.chromium.launch(**launch)
        page = await browser.new_page(viewport={"width": 1600, "height": 1150})

        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

        print(f"Driving {len(wanted)} pattern(s) at {BASE}\n")
        print(f'{"pattern":<20} {"state":<10} {"out":>4} {"nodes":>7} {"spans":>6}  notes')
        print("-" * 78)

        broken = 0
        for pattern in wanted:
            stats = await drive_one(page, pattern, errors)
            problems = faults(pattern, stats)
            broken += bool(problems)
            note = "; ".join(problems) if problems else ("ok" + (" (approved)" if stats["approved"] else ""))
            print(f'{pattern["slug"]:<20} {stats["state"]:<10} {stats["outputs"]:>4} '
                  f'{stats["lit"]:>3}/{stats["nodes"]:<3} {stats["spans"]:>6}  {note}')

        await browser.close()

    print(f"\n{len(wanted) - broken}/{len(wanted)} clean")
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
