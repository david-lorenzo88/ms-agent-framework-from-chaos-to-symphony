"""The session deck's content, separated from the machinery that builds it.

Edit here, then run ``python deck/build_deck.py``. Keeping the words out of
the XML code means a slide can be reworded without going anywhere near OOXML.
"""

from __future__ import annotations

TITLE = "FROM CHAOS TO SYMPHONY"
SUBTITLE = "Orchestrating agents with Microsoft Agent Framework"
SPEAKERS = ("David Lorenzo", "Samir Makwana")

#: Each entry is (kind, payload). ``kind`` picks the template layout to clone.
#:   section  -> title only
#:   two      -> title + 2 columns (header, bullets)
#:   four     -> title + 4 columns (header, bullets)
#:   one      -> title + a single body column
SLIDES: list[tuple[str, dict]] = [
    # ── where we left off ───────────────────────────────────────────
    ("section", {"title": "ONE YEAR ON"}),
    ("two", {
        "title": "WHAT CHANGED SINCE LAST SUMMIT",
        "cols": [
            ("Baltic Summit 2025", [
                "Agent Framework in public preview",
                "Five orchestration patterns, explained",
                "Two of them actually demoed",
                "AutoGen and Semantic Kernel still separate",
            ]),
            ("Today", [
                "1.0 GA since 2 April 2026 — Python and .NET",
                "Same five patterns, all now stable",
                "All twelve demos in this session run live",
                "Convergence done; Build 2026 added agent harness, hosted agents, CodeAct",
            ]),
        ],
    }),
    ("two", {
        "title": "THE HONEST HEADLINE",
        "cols": [
            ("No new named pattern shipped", [
                "It is still Sequential, Concurrent, Group Chat, Handoff, Magentic",
                "Anyone selling you a sixth is selling you a graph",
            ]),
            ("Everything around them did", [
                "The workflow graph matured — routing, loops, map-reduce, composition",
                "Checkpoints became fully replayable",
                "Approval gates became first-class workflow state",
                "The patterns did not change. The production scaffolding did.",
            ]),
        ],
    }),

    # ── the five ────────────────────────────────────────────────────
    ("section", {"title": "THE FIVE ORCHESTRATIONS"}),
    ("four", {
        "title": "01 · SEQUENTIAL",
        "cols": [
            ("Use when", ["Steps genuinely depend on each other", "You need a reproducible pipeline", "Refinement improves each stage"]),
            ("Avoid when", ["The work is embarrassingly parallel", "One agent could do every stage", "You need routing or backtracking"]),
            ("How it fails", ["Error amplification: a wrong fact at stage one becomes established truth by stage three"]),
            ("API", ["SequentialBuilder(participants=[...])", "output_from='all'"]),
        ],
    }),
    ("four", {
        "title": "02 · CONCURRENT",
        "cols": [
            ("Use when", ["Specialists assess one input from different angles", "You want uncorrelated views", "Latency matters"]),
            ("Avoid when", ["There is a strict order of operations", "Participants write shared state", "You have no aggregation rule"]),
            ("How it fails", ["Cost multiplies by N, and the aggregator averages away a real disagreement"]),
            ("API", ["ConcurrentBuilder(participants=[...])", ".with_aggregator(executor)"]),
        ],
    }),
    ("four", {
        "title": "03 · GROUP CHAT",
        "cols": [
            ("Use when", ["The decision benefits from debate", "Maker-checker or a quality gate", "A human may read or join the thread"]),
            ("Avoid when", ["Simple delegation would do", "Dialogue rounds would breach an SLA", "Nothing can objectively judge 'done'"]),
            ("How it fails", ["Infinite agreement: participants politely restate each other until the budget runs out"]),
            ("API", ["GroupChatBuilder(selection_func=...)", "max_rounds= / termination_condition="]),
        ],
    }),
    ("four", {
        "title": "04 · HANDOFF",
        "cols": [
            ("Use when", ["The right expert is not known up front", "Escalation is normal", "Ownership should move with the case"]),
            ("Avoid when", ["You need parallel analysis", "A central manager should control flow", "The pipeline is fixed"]),
            ("How it fails", ["Hot potato: two specialists each think the case is the other's, and pass it back and forth"]),
            ("API", ["HandoffBuilder().with_start_agent(a)", ".add_handoff(source, [targets])"]),
        ],
    }),
    ("four", {
        "title": "05 · MAGENTIC",
        "cols": [
            ("Use when", ["The problem is genuinely open-ended", "Several rounds of research are needed", "You want plan review before work starts"]),
            ("Avoid when", ["The task is linear — Sequential is far cheaper", "You only need parallel outputs", "Dynamic multi-round is not permitted"]),
            ("How it fails", ["Unbounded spend: a language model decides at runtime how many rounds your budget buys"]),
            ("API", ["MagenticBuilder(manager_agent=...)", "max_round_count / max_stall_count"]),
        ],
    }),

    # ── the graph ───────────────────────────────────────────────────
    ("section", {"title": "UNDERNEATH THE FIVE: THE WORKFLOW GRAPH"}),
    ("two", {
        "title": "WHEN NONE OF THE FIVE FIT",
        "cols": [
            ("06 · Conditional routing", [
                "A switch-case edge group picks exactly one branch",
                "The model classifies; plain Python decides",
                "Routing you can unit-test without calling a model",
                "Fails as: the silent Default nobody reads",
            ]),
            ("07 · Fan-out / fan-in", [
                "Map over a collection, join at one reducer",
                "Most nodes here are not agents — and should not be",
                "Spend model calls on judgement, not arithmetic",
                "Fails as: one straggler sets the latency",
            ]),
        ],
    }),
    ("two", {
        "title": "CYCLES AND COMPOSITION",
        "cols": [
            ("08 · Reflection loop", [
                "Draft, critique, rewrite — a real cycle in the graph",
                "The interesting part is the exit, not the loop",
                "Cap the iterations and escalate to a person",
                "Fails as: a critic that approves everything to end the conversation",
            ]),
            ("09 · Sub-workflow composition", [
                "WorkflowExecutor makes a whole workflow one node",
                "The child keeps its own graph, state, tests and owner",
                "How a shared gate gets reused without editing a huge graph",
                "Fails as: error opacity — the parent sees a node fail, not the step",
            ]),
        ],
    }),

    # ── production ──────────────────────────────────────────────────
    ("section", {"title": "PRODUCTION"}),
    ("two", {
        "title": "10 · HUMAN IN THE LOOP",
        "cols": [
            ("The mechanism", [
                ".with_request_info(agents=[...]) makes a participant a gate",
                "The workflow emits request_info and suspends",
                "It resumes via run(responses=...) — the pause is state, not a held coroutine",
                "So an approval can outlive the process that asked for it",
            ]),
            ("What it costs you", [
                "Rubber-stamping: forty approvals an hour are forty rubber stamps",
                "Gate on a threshold, not on every case",
                "Give the approver the figures, not the transcript",
                "Alarm the queue — a gate nobody answers is an outage that looks quiet",
            ]),
        ],
    }),
    ("two", {
        "title": "11 · CHECKPOINT AND RESUME",
        "cols": [
            ("The mechanism", [
                "Pass checkpoint_storage to any builder",
                "State persists as the workflow advances",
                "Resume by id — in another object, process or pod",
                "In the demo the first instance is discarded mid-run, and a new one finishes the job",
            ]),
            ("What it costs you", [
                "Replayed side effects: resuming re-enters nodes that already sent the email",
                "Make effectful nodes idempotent on a key from workflow state",
                "A checkpoint is a copy of your data — same retention rules apply",
            ]),
        ],
    }),
    ("four", {
        "title": "12 · THREE WAYS IT DIES IN PRODUCTION",
        "cols": [
            ("The runaway loop", ["Two agents that never concede", "Contained by a hard round cap", "Not by agreement"]),
            ("The dead provider", ["The specialist's client returns 503", "Contained by a labelled fallback path", "The caller still gets an answer"]),
            ("The hung call", ["A tool that never returns", "Contained by a timeout", "Proceed without it, flag for review"]),
            ("Then watch them", ["A cap that always fires means work never finishes", "A busy fallback is a silent outage", "Alert on the rate, not the event"]),
        ],
    }),

    # ── demo ────────────────────────────────────────────────────────
    ("section", {"title": "DEMO: ALL TWELVE, LIVE"}),
    ("two", {
        "title": "WHAT YOU ARE ABOUT TO SEE",
        "cols": [
            ("The setup", [
                "Baltic Freight Group — a freight forwarder with twenty shipments in trouble",
                "Every pattern works the same exception data",
                "The whole database is in process memory",
                "DevUI shows the framework's own view: graph, events, streamed interaction",
            ]),
            ("Why it will work on this wifi", [
                "Default provider is a deterministic offline client",
                "No key, no network, no cost — same code path as Azure OpenAI",
                "One environment variable switches to a real model",
                "Graceful degradation is not just slide 18; it is how the demo ships",
            ]),
        ],
    }),

    # ── governance ──────────────────────────────────────────────────
    ("two", {
        "title": "GOVERNANCE, COST AND WHAT TO MEASURE",
        "cols": [
            ("Cap before you ship", [
                "Every loop: max rounds, max stalls, max resets",
                "Every external call: a timeout",
                "Every specialist: a fallback",
                "Every money decision: a threshold and a named human",
            ]),
            ("Measure after you ship", [
                "Tokens and cost per completed workflow, not per call",
                "Rate at which each guardrail fires",
                "Approval queue depth and time-to-decision",
                "Fallback share of traffic — the metric that finds silent outages",
            ]),
        ],
    }),

    # ── resources ───────────────────────────────────────────────────
    ("one", {
        "title": "RESOURCES",
        "body": [
            "This session — code, demos and the showcase site:",
            "github.com/david-lorenzo88/ms-agent-framework-from-chaos-to-symphony",
            "",
            "Microsoft Agent Framework: github.com/microsoft/agent-framework",
            "Docs: learn.microsoft.com/agent-framework",
            "Agent design patterns: learn.microsoft.com/azure/architecture/ai-ml/guide/ai-agent-design-patterns",
            "",
            "David Lorenzo — linkedin.com/in/davidlorenzolopez",
            "Samir Makwana",
        ],
    }),
    ("section", {"title": "THANK YOU"}),
]

#: Speaker notes, keyed by slide title.
#:
#: Deliberately not keyed by slide number: inserting one slide would silently
#: shift every note after it onto the wrong slide, which is exactly what
#: happened the first time this deck was built.
NOTES: dict[str, str] = {
    TITLE: (
        "Welcome. Same title as last year, deliberately - this is the second movement, not a repeat. "
        "Last year we explained five patterns and demoed two of them. This year every pattern runs."
    ),
    "THE HONEST HEADLINE": (
        "The framing that sets up the whole session: nothing new was named, and that is the point. "
        "If you came expecting a sixth pattern, what you get instead is the layer underneath and the "
        "production scaffolding - the part that decides whether this survives contact with users."
    ),
    "01 \u00b7 SEQUENTIAL": (
        "Sequential is what most multi-agent problems actually are. Start here and escalate only when a "
        "linear chain genuinely cannot express the work."
    ),
    "05 \u00b7 MAGENTIC": (
        "The most capable and the most expensive pattern. The three ceilings are not optional - without "
        "them a language model decides at runtime how much your budget buys."
    ),
    "WHEN NONE OF THE FIVE FIT": (
        "This is the material that did not exist in last year's talk. When someone asks 'what if none of "
        "the five fit', this is the answer: build the graph yourself."
    ),
    "11 \u00b7 CHECKPOINT AND RESUME": (
        "The demo proves durability the only honest way - we throw the first workflow instance away and "
        "let a brand new one finish the job from the checkpoint alone."
    ),
    "12 \u00b7 THREE WAYS IT DIES IN PRODUCTION": (
        "The section the abstract promised and most conference talks skip. All three of these are "
        "invisible in a demo and inevitable in production."
    ),
    "DEMO: ALL TWELVE, LIVE": (
        "Switch to the showcase site. Pattern rail on the left, run each one, DevUI tab shows the "
        "framework's own view. The human-in-the-loop pattern will actually stop and wait for a click."
    ),
    "RESOURCES": (
        "Everything is in the repo, including the offline client, so anyone can clone it and run all "
        "twelve patterns on the train home."
    ),
}
