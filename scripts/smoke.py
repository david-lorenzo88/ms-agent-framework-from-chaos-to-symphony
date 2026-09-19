"""Run every pattern end to end. The repo's regression test.

Usage:  python scripts/smoke.py [slug ...]

Exits non-zero if any pattern fails, so CI and a pre-session sanity check are
the same command.
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
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
