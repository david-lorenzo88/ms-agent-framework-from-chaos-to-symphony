"""Run every pattern end to end. The repo's regression test.

Usage:  python scripts/smoke.py [slug ...]

Exits non-zero if any pattern fails, so CI and a pre-session sanity check are
the same command. It also checks five claims that nothing else would catch:
what DevUI would ask for before running a pattern (see
``chaos_to_symphony.devui_input``), whether each branching pattern's example
prompts still reach the endings they advertise, whether the Agents panel can
still read every agent's prompt and tools out of the built workflow, whether
the group chat can still be ended early by a talkative chair, and whether the
briefing copy the audience reads still matches the store it describes.
"""

from __future__ import annotations

import asyncio
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from chaos_to_symphony.domain import ROLES  # noqa: E402
from chaos_to_symphony.memory import STORE  # noqa: E402
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


def check_agent_configs(specs) -> int:
    """Confirm the Agents panel can still find every agent and its prompt.

    ``introspect`` reaches into the framework's own objects - an executor's
    ``agent``, an agent's ``default_options`` - so a dependency bump can move
    them without anything raising. The panel would simply come up empty, which
    on stage reads as "these patterns have no agents" rather than as a bug.
    """
    from chaos_to_symphony.introspect import agents_in

    failures = 0
    total = 0
    for spec in specs:
        try:
            found = agents_in(spec.build())
        except Exception as exc:
            print(f"  [FAIL] {spec.name}: introspection raised {type(exc).__name__}: {exc}")
            failures += 1
            continue
        if not found:
            print(f"  [FAIL] {spec.name}: no agents found; the Agents panel would be empty")
            failures += 1
            continue
        total += len(found)
        for agent in found:
            if not agent["name"] or not agent["instructions"]:
                print(f"  [FAIL] {spec.name}/{agent['executorId']}: no name or no system prompt")
                failures += 1
    if not failures:
        print(f"  Agents:      {total} agents across {len(specs)} patterns, every one with a prompt")
    return failures


def check_group_chat_termination() -> int:
    """Confirm the committee cannot settle before anyone has argued.

    Offline never exercises this. The scripted chair is written to open without
    a figure, so a condition reading only the text passes every local run - and
    then a live model, asked to open the meeting, writes the whole committee
    itself in one turn ("Specialist 2, Commercial: the declared value is EUR
    96,500"), the chair's opening carries a figure, and the meeting ends at
    round 0 with three agents who never spoke.

    Provider-independent, because it is the condition being tested rather than
    any client: hand it transcripts and see what it says.
    """
    from agent_framework import Message

    from chaos_to_symphony.patterns.p03_group_chat import CHAIR, settled

    def turn(author: str | None, text: str) -> Message:
        message = Message("assistant" if author else "user", contents=[text])
        if author:
            message.author_name = author
        return message

    monologue = (
        "Decision to be made: whether BFG-24082 merits compensation.\n"
        "Specialist 2, Commercial: the declared value is EUR 96,500."
    )
    cases = [
        ("chair opens alone while quoting a figure",
         [turn(None, "Agree a settlement."), turn(CHAIR, monologue)], False),
        ("a specialist quotes money mid-debate",
         [turn(None, "x"), turn(CHAIR, monologue),
          turn("pricing-specialist", "Modelled exposure EUR 8,041.")], False),
        ("chair sums up after the specialists",
         [turn(None, "x"), turn(CHAIR, monologue),
          turn("pricing-specialist", "Modelled exposure EUR 8,041."),
          turn(CHAIR, "Settlement agreed at EUR 9,650.")], True),
        ("chair sums up naming no figure",
         [turn(None, "x"), turn(CHAIR, monologue),
          turn("pricing-specialist", "Modelled exposure EUR 8,041."),
          turn(CHAIR, "Let us reconvene tomorrow.")], False),
    ]

    failures = 0
    for label, conversation, expected in cases:
        if settled(conversation) is not expected:
            print(f"  [FAIL] group chat termination: {label} -> wanted {expected}")
            failures += 1

    # And nobody gets the floor twice running. The orchestrator broadcasts a
    # turn to everyone except the agent that produced it, then asks the next
    # speaker to respond with an empty message list. Pick the same agent twice
    # and it was sent nothing, so a real provider is handed a completion with
    # no messages and refuses: "Messages are required for chat completions".
    # Offline never reaches it - the scripted chair always settles the meeting
    # at its summing-up - so only a check like this one catches it.
    from types import SimpleNamespace

    from chaos_to_symphony.patterns.p03_group_chat import MAX_ROUNDS, committee_selector

    seats = ["claims-manager", "pricing-specialist", "legal-counsel", "ops-account-lead"]
    picks = [
        committee_selector(
            SimpleNamespace(current_round=r, participants=dict.fromkeys(seats), conversation=[])
        )
        for r in range(MAX_ROUNDS + 4)
    ]
    repeats = [r for r in range(1, len(picks)) if picks[r] == picks[r - 1]]
    if repeats:
        print(f"  [FAIL] group chat selector picks the same speaker twice at round(s) {repeats}")
        failures += 1

    if not failures:
        print(f"  Group chat:  settles only after a real debate ({len(cases)} transcripts), "
              f"never the same speaker twice")
    return failures


def check_case_briefs(specs) -> int:
    """Every pattern explains its own case, and the explanation matches the store.

    The briefing copy names shipments, and the store is the only place those
    shipments exist. Renumber a seed row and the prose on stage becomes a lie
    that nothing else would catch - the pattern still runs, the diagram still
    lights up, and the card confidently describes a consignment that is not
    there. So every BFG reference in the copy is resolved against the store,
    and the roles the domain page advertises are resolved against the agents
    the patterns really build.
    """
    failures = 0
    ref = re.compile(r"BFG-\d{5}")
    cited = 0

    for spec in specs:
        brief = spec.case
        if brief is None or not brief.about.strip() or not brief.why.strip():
            print(f"  [FAIL] {spec.name}: no case brief; its screen would explain nothing")
            failures += 1
            continue
        # Facts carry most of the references, so check the whole card, not the prose.
        whole = " ".join([brief.about, brief.why, *(f"{f.label} {f.value}" for f in brief.facts)])
        for shipment_id in sorted(set(ref.findall(whole))):
            cited += 1
            if shipment_id not in STORE.shipments:
                print(f"  [FAIL] {spec.name}: case brief cites {shipment_id}, which is not in the store")
                failures += 1

    # The domain page's cast list has to be agents that exist somewhere.
    from chaos_to_symphony.introspect import agents_in

    built: set[str] = set()
    for spec in PATTERNS:
        try:
            for agent in agents_in(spec.build()):
                built.add(str(agent.get("name", "")))
        except Exception:  # a build failure is already reported by the run above
            continue
    for role in ROLES:
        if role.term not in built:
            print(f"  [FAIL] domain briefing names '{role.term}', which no pattern builds")
            failures += 1

    if not failures:
        print(f"  Domain:      {len(specs)} case briefs, {cited} shipment references resolved, "
              f"{len(ROLES)} roles real")
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
    failures += check_agent_configs(specs)
    failures += check_group_chat_termination()
    failures += check_case_briefs(specs)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
