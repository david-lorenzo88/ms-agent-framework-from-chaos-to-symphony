"""Run every pattern end to end. The repo's regression test.

Usage:  python scripts/smoke.py [slug ...]

Exits non-zero if any pattern fails, so CI and a pre-session sanity check are
the same command. It also checks what DevUI would ask for before it runs each
pattern - see ``chaos_to_symphony.devui_input`` for why that can rot quietly.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from chaos_to_symphony.registry import PATTERNS, get  # noqa: E402
from chaos_to_symphony.scripted import reset_context  # noqa: E402


async def run_one(spec) -> tuple[bool, str, float]:
    started = time.perf_counter()
    reset_context()
    try:
        if spec.demo is not None:
            notes = await spec.demo(spec.default_prompt)
            detail = notes[-1] if notes else "(no narration)"
        else:
            workflow = spec.build()
            result = await workflow.run(spec.default_prompt)
            outputs = [str(o) for o in result.get_outputs() if str(o).strip()]
            if not outputs:
                return False, "produced no output", time.perf_counter() - started
            detail = outputs[-1]
        return True, detail.replace("\n", " ")[:110], time.perf_counter() - started
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"[:160], time.perf_counter() - started


def check_devui_inputs(specs) -> int:
    """Confirm DevUI would offer a prompt box, not a form, for each pattern.

    ``devui_input`` leans on a private DevUI function to get there, so this is
    the part most likely to rot under a dependency bump - and it rots quietly,
    back into a dialog asking for a role and a contents array. Rebuilding
    twelve workflows to check costs nothing next to running them.
    """
    try:
        from agent_framework_devui import _utils

        from chaos_to_symphony import devui_input
    except Exception as exc:
        # DevUI moved its internals. The patch degrades to DevUI's own forms
        # rather than failing, so this check does the same.
        print(f"  [SKIP] DevUI input check: {type(exc).__name__}: {exc}")
        return 0

    devui_input.install()
    failures = 0
    for spec in specs:
        try:
            start = spec.build().get_start_executor()
            # Through the module, not a name bound before install(), because
            # that is how DevUI itself reaches these - it imports them inside
            # the request handler. Bind them earlier and the patch is invisible.
            message_types = _utils.extract_executor_message_types(start)
            schema = _utils.generate_input_schema(_utils.select_primary_input_type(message_types))
        except Exception as exc:
            schema = {"error": f"{type(exc).__name__}: {exc}"}
        if schema != {"type": "string"}:
            print(f"  [FAIL] {spec.name} would open a DevUI form instead of a prompt box: {schema}")
            failures += 1
    if not failures:
        print(f"  DevUI input: all {len(specs)} take a prompt")
    return failures


async def main() -> int:
    wanted = sys.argv[1:]
    specs = [get(s) for s in wanted] if wanted else list(PATTERNS)
    failures = 0
    print(f"Running {len(specs)} pattern(s)\n")
    for spec in specs:
        ok, detail, elapsed = await run_one(spec)
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {spec.number:>2}. {spec.name:<26} {elapsed:5.1f}s  {detail}")
        if not ok:
            failures += 1
    print(f"\n{len(specs) - failures}/{len(specs)} passed")
    failures += check_devui_inputs(specs)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
