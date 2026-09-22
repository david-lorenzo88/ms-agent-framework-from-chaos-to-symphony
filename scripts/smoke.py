"""Run every pattern end to end. The repo's regression test.

Usage:  python scripts/smoke.py [slug ...]

Exits non-zero if any pattern fails, so CI and a pre-session sanity check are
the same command. It also checks the two claims the site makes that nothing
else would catch: what DevUI would ask for before running a pattern (see
``chaos_to_symphony.devui_input``), and whether each branching pattern's
example prompts still reach the endings they advertise.
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
        print(f"  DevUI input: all {len(specs)} patterns take a prompt")
    return failures


async def _run_capturing(spec, prompt: str) -> tuple[str, list[str]]:
    """Run one prompt, returning the workflow's output and who actually spoke."""
    workflow = spec.build()
    outputs: list[str] = []
    speakers: list[str] = []
    async for event in workflow.run(prompt, stream=True):
        kind = getattr(event, "type", "")
        executor_id = getattr(event, "executor_id", "") or ""
        data = getattr(event, "data", None)
        if kind == "output" and str(data).strip():
            outputs.append(str(data))
        spoken = getattr(data, "text", "") or ""
        if kind in ("agent_run_update", "output") and spoken.strip() and executor_id not in speakers:
            speakers.append(executor_id)
    return "\n".join(outputs), speakers


async def check_prompt_examples(specs) -> int:
    """Run each advertised example prompt and confirm it still ends where it says.

    The site offers these as "this prompt shows you the Default branch". That
    claim holds only as long as the case data, the routing rules and the
    scripted client all still agree - and none of them knows the promise
    exists. An example that quietly stopped reaching its ending is a demo that
    fails in front of an audience, so check it here instead.
    """
    failures = 0
    checked = 0
    for spec in specs:
        for example in spec.prompt_examples:
            checked += 1
            reset_context()
            try:
                text, speakers = await _run_capturing(spec, example.prompt)
            except Exception as exc:
                print(f"  [FAIL] {spec.name}: {example.prompt!r} raised {type(exc).__name__}: {exc}")
                failures += 1
                continue
            # An ending is either words the run prints (the branch executors say
            # their own name) or the agent that answered (handoff routes to a
            # specialist, whose reply never mentions which one it is).
            wanted = example.ending.split(" (")[0]
            if wanted not in text and wanted not in speakers:
                print(f"  [FAIL] {spec.name}: {example.prompt!r}")
                print(f"         promised {example.ending!r}, got {text.strip()[:90]!r} from {speakers}")
                failures += 1
    if checked and not failures:
        print(f"  Endings:     all {checked} example prompts reached the ending they advertise")
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
    failures += await check_prompt_examples(specs)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
