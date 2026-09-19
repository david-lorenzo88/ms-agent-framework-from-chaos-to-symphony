# From Chaos to Symphony

**Orchestrating agents with Microsoft Agent Framework** — Baltic Summit 2026
David Lorenzo &amp; Samir Makwana

Twelve orchestration patterns, each with a runnable demo, wired into
[DevUI](https://github.com/microsoft/agent-framework/tree/main/python/packages/devui)
and an interactive showcase site. **Everything runs offline** — no API key, no
network, no cost — because a conference demo that needs the venue wifi is a
conference demo that fails.

```bash
git clone https://github.com/david-lorenzo88/ms-agent-framework-from-chaos-to-symphony
cd ms-agent-framework-from-chaos-to-symphony
make install
make demo          # DevUI on :8080 and the showcase on :8000
```

---

## The twelve patterns

### The five orchestrations — stable in Agent Framework 1.0

| # | Pattern | In one line | API |
|---|---------|-------------|-----|
| 1 | **Sequential** | A pipeline; each agent refines the last one's work | `SequentialBuilder` |
| 2 | **Concurrent** | Fan out to specialists at once, fan in one answer | `ConcurrentBuilder` |
| 3 | **Group Chat** | One thread, an orchestrator holding the floor | `GroupChatBuilder` |
| 4 | **Handoff** | The agent holding the case decides who holds it next | `HandoffBuilder` |
| 5 | **Magentic** | A manager that plans, delegates, re-plans and stops | `MagenticBuilder` |

### The workflow graph — what sits underneath the five

| # | Pattern | In one line | API |
|---|---------|-------------|-----|
| 6 | **Conditional routing** | The model classifies; the graph decides | `add_switch_case_edge_group` |
| 7 | **Fan-out / fan-in** | Map over a collection, reduce to one answer | `add_fan_out_edges` / `add_fan_in_edges` |
| 8 | **Reflection loop** | Draft, critique, rewrite — with a ceiling | a cycle in the graph |
| 9 | **Sub-workflow composition** | A whole workflow becomes one node | `WorkflowExecutor` |

### Production — the parts that only matter once real users arrive

| # | Pattern | In one line | API |
|---|---------|-------------|-----|
| 10 | **Human in the loop** | The workflow suspends until a person answers | `.with_request_info(...)` |
| 11 | **Checkpoint &amp; resume** | Kill it halfway; a new instance finishes the job | `checkpoint_storage=` |
| 12 | **Guardrails &amp; degradation** | Three ways it dies, and the three lines that stop them | caps, fallbacks, timeouts |

Patterns 6–12 are new since the 2025 edition of this talk. Patterns 1–5 are the
same five as last year — **no new named pattern has shipped.** What changed is
everything around them, which is rather the point of the session.

---

## Running it

### Install

```bash
make install        # venv + dependencies
```

Uses [uv](https://github.com/astral-sh/uv) when it is on your PATH and falls
back to `python -m venv` + `pip` when it is not. Python 3.10 or newer.

### The showcase

```bash
make demo           # runs both servers
```

- **http://localhost:8000** — the showcase site: pick a pattern, read it, run
  it, watch the diagram light up and the log stream.
- **http://localhost:8080** — DevUI, with all twelve workflows registered. The
  site embeds it per pattern so you can drive a workflow through the
  framework's own tooling.

### Traces, and what DevUI can and cannot show

The **Traces** tab is the framework's own view of the run you just did: the
OpenTelemetry spans Agent Framework emits — `invoke_agent`, `execute_tool`,
`executor.process`, and the edge and message plumbing underneath — drawn as a
waterfall. Put Sequential and Concurrent side by side and the picture makes the
argument for you: a clean staircase against overlapping bars.

It exists because **DevUI can only display runs you start inside it.** Its
timeline is client-side state from the stream it opened, its frontend reads
only `entity_id` from the URL, it exposes no postMessage API, and this build
ships no server-side trace-retrieval endpoint. A run started from the
*Run pattern* button therefore cannot appear in the embedded DevUI timeline —
so the showcase captures the same telemetry itself rather than making you run
the pattern twice.

Routing runs through DevUI's `/v1/responses` API instead was tried and rejected:
the in-memory store lives per process, so the audit trail and stock of shipments
would mutate inside DevUI and vanish from this page, the human-in-the-loop
approval gate would break, and patterns 11 and 12 drive their workflow more than
once and cannot go through that API at all.

Deep-link a single pattern from a slide: `http://localhost:8000/?pattern=handoff`

### Checking it still works

Two harnesses, and they catch different things.

```bash
make smoke                        # all twelve, end to end, no browser
python scripts/smoke.py handoff   # just one
```

`smoke.py` calls `workflow.run()` directly. Fast, no browser, good for CI.

```bash
make demo                         # in one terminal
make drive                        # in another
python scripts/drive.py handoff   # just one
python scripts/drive.py --headed  # watch it happen
```

`drive.py` drives the real page: it runs each pattern, answers the approval
gate, and checks the run completed, produced output, lit up diagram nodes,
kept the diagram inside its viewBox, captured spans, and logged no errors —
in the browser or on the server. Every bug found in this repo so far lived in
exactly those places and none of them showed up in `smoke.py`. Run it before
the session. It exits non-zero if anything is wrong.

It needs Playwright's browser once:

```bash
.venv/bin/pip install playwright && .venv/bin/playwright install chromium
```

### With a real model

Offline is the default. To use a real provider, copy `.env.example` to `.env` and set:

```bash
CHAOS_PROVIDER=azure     # or: openai
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT=gpt-4o-mini
AZURE_OPENAI_API_KEY=...
```

Not one line of pattern code changes. That is the point worth making from the
stage: the orchestration layer does not know which client it is driving. If the
provider is selected but misconfigured, it degrades — loudly — back to offline
rather than throwing in front of an audience.

---

## How it stays offline

`ScriptedChatClient` (`src/chaos_to_symphony/scripted.py`) implements the same
`BaseChatClient` contract a real provider implements. It is not a stub: it
honours the three things the orchestration patterns actually depend on.

- **Text replies** per persona, quoting real rows from the in-memory store, so
  a transcript reads like work rather than lorem ipsum.
- **Tool calls**, including the `handoff_to_*` tools `HandoffBuilder` generates
  at build time. Without these, pattern 4 cannot route at all.
- **Structured output** — when `response_format` names a Pydantic model the
  reply is a valid instance of it, which is what the switch-case router parses.

It also answers Magentic's progress-ledger contract, which is strict JSON rather
than chat. Determinism comes from hashing the conversation rather than from a
counter, so concurrent participants cannot interfere with each other's script.

## The data

A fictional Tallinn freight forwarder, **Baltic Freight Group**: twenty
shipments, seven customers, a tariff schedule and three SLA bands, all held in
process memory (`src/chaos_to_symphony/memory.py`). No database, no disk, no
network. The store is frozen except for its audit trail, so an agent that
"updates" a shipment has to go through `record()` and the trail can never
silently miss a decision.

Every pattern works the same exception data, so switching pattern on the site
changes the *orchestration*, not the problem — which is exactly the comparison
the session is trying to draw.

---

## Layout

```
src/chaos_to_symphony/
  memory.py       the entire in-memory "database"
  scripted.py     the offline chat client
  clients.py      provider selection: offline | azure | openai
  tools.py        agent tools, all reading the in-memory store
  base.py         PatternSpec: teaching material + runnable workflow
  registry.py     the twelve patterns, in session order
  runner.py       drives a run, translates events into a live feed
  api.py          showcase backend (FastAPI + server-sent events)
  devui_app.py    registers all twelve workflows with DevUI
  patterns/       one module per pattern, p01…p12
web/              the showcase site: no framework, no CDN, no build step
deck/             the session deck and the script that builds it
scripts/smoke.py  runs all twelve; the repo's regression test
```

## The deck

`deck/from-chaos-to-symphony.pptx` is generated from the Baltic Summit template,
so every visual decision — colours, the Garet face, the page gradient, the
placeholder geometry — comes from the template rather than being hard-coded.

```bash
make deck           # rebuild from deck/content.py
make deck-qa        # geometry check: does the text fit its boxes?
```

Edit the words in `deck/content.py`; the builder never needs touching. Speaker
notes are keyed by slide *title*, not slide number, so inserting a slide cannot
silently shift every note onto the wrong one.

---

## Credits and references

- [microsoft/agent-framework](https://github.com/microsoft/agent-framework) — the framework itself
- [Agent Framework docs](https://learn.microsoft.com/en-us/agent-framework/)
- [AI agent design patterns](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns) — Azure Architecture Center

Built against Agent Framework **1.19.0**, orchestrations **1.2.0**, DevUI
**1.0.0b260918**.

MIT licensed. The Baltic Summit template and logo remain the property of
Baltic Summit.
