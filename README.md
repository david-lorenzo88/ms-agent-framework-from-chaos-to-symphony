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

### Send back, on the approval gate

Pattern 10 suspends and asks a human. **Send back** is not a decline — it hands
the gated agent a message, which re-runs it and suspends again on the new
proposal, where **Approve** passes the proposal through untouched. Both answers
go back through `run(responses=...)`; the difference is only whether the
response carries any messages.

That means a send-back is only visible if the proposal actually changes. The
offline settlement agent therefore re-prices: it opens at the capped estimate —
services not delivered plus the tier's daily allowance — and concedes on each
send-back down to the customer's approval threshold, quoting what it came down
from. The
modal names the round, because the gate reopening within a second of a click
otherwise reads as a button that did nothing.

### What ends the group chat

The committee is the one pattern where agents react to each other, so it is the
one that most needs a termination rule you can point at. Five turns: the chair
opens by framing the decision, each specialist states a position once, and the
chair sums up with a figure — at which point `settled()` fires and the meeting
ends with a round to spare.

Two details make that demonstrable rather than accidental:

- **The condition asks who spoke, not just what was said.** Money is what this
  committee argues about, so the specialists quote it constantly. A condition
  that accepts any figure from anyone ends the meeting on the first specialist
  to open their mouth — a one-turn pipeline wearing a group chat's clothes. It
  is the chair's figure that settles it.
- **Nobody gets the floor twice running.** The orchestrator broadcasts a turn
  to everyone *except* the agent that produced it, then asks the next speaker
  to respond with an empty message list, trusting the broadcast to have carried
  the conversation. Select the same agent twice and it was sent nothing, so a
  real provider is handed a completion with no messages and refuses —
  `ChatClientInvalidRequestException: Messages are required for chat
  completions`. If the chair's summing-up does not settle the meeting, the
  floor therefore goes back round the specialists, which is what makes
  `max_rounds` a backstop you can actually reach rather than a crash.
- **And it asks whether a debate happened at all.** Reading the text is not
  enough once a real model is writing it: asked to open the meeting, one wrote
  the entire committee itself in a single turn, every specialist's position and
  figure included — so the chair's opening carried a figure and the meeting
  ended at round 0 with three agents who never spoke. Whether
  anyone else has taken a turn is a fact about the transcript, not about the
  prose, and no amount of fluent writing can fake it.
- **The chair closes early, not on the last permitted round.** Close on the cap
  and the condition and the cap fire together and look identical in the log.
  With a spare round left over, a meeting that ends is a meeting the condition
  ended — and `max_rounds` firing is a real fault worth seeing rather than the
  normal path.

### Full screen, for the room at the back

**Full screen**, next to *Run pattern*. The live log goes left, the interaction
diagram right, and everything else goes away. Escape or **Exit full screen**
returns you to the page with the run intact.

The panes are not copies. The real console and diagram are *moved* into the
overlay and put back on exit, so a run already in flight keeps streaming into
the same elements and the diagram keeps lighting up without either knowing
where it is being displayed.

It is a CSS overlay rather than the Fullscreen API, on purpose: native
fullscreen renders only the fullscreen element and its descendants, which would
hide the human-in-the-loop approval dialog — and a demo that silently swallows
its own approval gate is worse than one with a visible browser bar. Press F11
if you want the chrome gone too; it does the same job without breaking the
modal.

### Offline gives agents data a real model would not have

Worth knowing before you run this live, because it is the one way offline
flatters the demo. `ScriptedChatClient` reads the in-memory store directly: ask
it to "triage incident BTA-26109" and it looks the booking up and answers
from the real record. A live model asked the same thing sees a string that
looks like a reference and nothing else.

That is invisible until an agent's answer decides something. Conditional
routing's classifier had no tools, so against Foundry it graded every case the
same safe middle way and every run landed on the medium branch — the routing
looked broken when the agent had simply never been told anything. It now holds
`lookup_booking`, and is told to use it before grading.

The rule that follows: **an agent whose output steers the graph needs its own
way to get the facts.** An agent late in a chain is usually fine, because the
agents before it put their findings in the shared conversation — the writers in
sequential, human-in-the-loop and checkpoint-resume have no tools and do not
need any. An agent that speaks *first* has only the prompt: that is why the
reflection loop's writer holds `lookup_booking` and `check_eu261`, and so does
its reviewer, which cannot reject a promise of EU261 money without knowing
whether it is owed. The same goes for handoff triage and `screen_payment` — a
stolen card is not in the booking record.

### The diagram only ever describes the last run

Change the prompt and the diagram clears itself, the run state says *prompt
changed*, and nothing is claimed until you press **Run pattern** again.

That is not cosmetic. Loading an example is one click, and without it you get
the previous run's branch sitting in green above a prompt that would route
somewhere else entirely — pick *DUTY DESK* after running the
standard-queue case and `standard_queue` stays lit, which reads as conditional
routing being broken. The log is left alone: every line names its own booking,
it is the record of what actually happened, and the next run clears it anyway.

### Why only one specialist lights up

Handoff and group chat build a **fully connected graph**. After each turn the
active agent broadcasts the conversation to every other participant so their
histories stay in step — an `AgentExecutorRequest` with `should_respond=False`.
Each recipient really is invoked: it files the messages and returns without
calling its model.

So `executor_invoked` and `executor_completed` fire for every participant on
every turn. They are honest events, but they answer *who received a message*,
not *who worked the case* — and lighting the diagram off them turned all four
specialists green on the one pattern whose whole point is that exactly one was
chosen. The runner now skips the broadcasts when colouring boxes and says so in
the log instead, so the mechanism is still visible without the diagram claiming
work that never happened.

### Explaining the domain, and each case

Twelve patterns share one story, and an attendee who does not know what a
travel incident is cannot follow any of the twelve. Two places say so:

- **The domain** — the button in the top bar, and in the corner of every case
  card. A briefing on Baltic Travel Agency: what the business does, the five
  incident kinds, what a customer's tier controls, the cast of specialists,
  the ten tools they can call, and the five facts that decide every demo.
  It is assembled from the live store (`src/chaos_to_symphony/domain.py`), so
  the counts and thresholds it shows are the ones the agents work against.
  The button glows until it has been opened once. It deliberately does *not*
  open itself: a modal over the page on load swallows the first click, which
  breaks `scripts/drive.py` — and would do the same to a speaker presenting
  from a fresh browser profile.
- **What this case is about** — a card on every pattern screen, between the
  pattern card and the diagram. What is happening in the business, the hard
  numbers from the store, and *why this case, for this pattern* — because the
  cases were chosen so each pattern's argument is visible in the run, and if
  that reasoning only lives in the speaker's head the twelve demos look
  interchangeable. It comes from each pattern's own `CaseBrief`.

`make smoke` resolves every booking and invoice the copy cites against the
store, and every role the briefing names against the agents the patterns really
build. Prose is easy to leave out of a test suite, and it is exactly the thing
that rots quietly: renumber a seed row and every pattern still runs, every
diagram still lights up, and the card confidently describes a booking that is
not there.

### Seeing what each agent actually is

The **Agents** tab lists every agent in the selected pattern with its system
prompt, its tools, and the chat client it will really drive — so the box that
just lit up in the diagram can be opened and shown to an audience.

None of it is written down twice. It is read back out of the `Agent` objects
the pattern module built, so editing a prompt in `patterns/p04_handoff.py`
changes what the tab shows and there is nowhere for the two to drift apart.
Two things fall out of reading the built workflow rather than the source:

- **The handoff tools appear**, and they are not in the source. `HandoffBuilder`
  generates a `handoff_to_<target>` tool per permitted edge at build time, so
  the tab shows triage holding four of them and each specialist holding exactly
  one, back to triage. That *is* the routing policy, and it is worth showing
  rather than describing.
- **Agents nested a level down appear too.** Human-in-the-loop wraps its gated
  participant in an `AgentApprovalExecutor` and sub-workflow composition embeds
  a whole `Workflow`; both are walked, and anything found inside is tagged.

`make smoke` checks the tab can still find all 31 agents and that each has a
prompt — the introspection reaches into framework internals, so a dependency
bump could empty the panel without anything raising an error.

### Patterns that finish more than one way

Four of the twelve branch, and which branch you get is decided by the case you
type, not by the pattern. Conditional routing has three terminal desks,
sub-workflow composition either holds a refund at the payment-risk gate or
passes it through, the reflection loop either gets an approval or hits its
revision ceiling, and handoff routes to one of four specialists.

The reflection loop's endings follow from the letter policy, not from luck.
The offline reviewer applies the same four rules its prompt gives a live model
— no EU261 money that is not owed, the traveller's rights stated, a date on
every promise, no admission of liability — so the strike letter fails twice for
two different reasons and passes on the third draft, and the letter whose refund
waits on a silent hotel can never be dated and escalates.

Those patterns show an **endings box** under the *Run it* prompt: one prompt per
ending, each naming where it lands and the fact in the case that sends it there.
Click one to load it. The other eight patterns finish one way whatever you type,
so the box stays hidden rather than implying a choice that is not there.

The prompts are checked, not asserted — `make smoke` runs every one of them and
fails if it no longer reaches the ending it advertises. Case data, routing rules
and the offline client all have to agree for the claim to hold, and none of them
knows the promise exists.

### Typing a prompt into DevUI

Every pattern in DevUI takes the same thing the showcase site's *Run it* box
takes: a sentence. Out of the box eight of them do not. DevUI builds a
workflow's input control from whatever type its *start* executor declares, and
`SequentialBuilder` and the other four orchestration builders put an adapter in
front that accepts `Message` as well as `str`. DevUI prefers the `Message`, and
its frontend then fails to recognise it as a chat message — it looks for a
`text` field and the framework's `Message` carries `contents` — so instead of a
text box you get *Configure Workflow Inputs* asking for `role`, `contents`,
`author_name` and `message_id` before the workflow will run.

`devui_input.py` tells DevUI to pick `str` whenever the start executor accepts
one, which all twelve do. Same workflows, same dispatch, one text box
everywhere. Set `CHAOS_DEVUI_PROMPT_INPUT=0` to see DevUI's own behaviour —
useful if you want to show the difference, and the escape hatch if a future
DevUI build changes the internals this leans on.

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
the in-memory store lives per process, so the audit trail and the book of trips
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

A Riga travel agency, **Baltic Travel Agency**: twenty bookings — flights,
hotels and activities — for twelve customers, three tier policies, twenty hotels
across two channel managers with three days of sync logs, and sixteen supplier
invoices, all held in process memory (`src/chaos_to_symphony/memory.py`). No
database, no disk, no network. The store is frozen except for its audit trail,
so an agent that "updates" a booking has to go through `record()` and the trail
can never silently miss a decision.

Places, airports, airlines, hotels, SiteMinder and GetYourGuide carry real
names, so the demo sounds like the industry it is set in. The travellers, their
bookings and every incident are invented; none of it describes something that
actually happened. EU261 and the Package Travel Directive are simplified, but
every rule the demo leans on is the rule as written — the distance bands come
from real airport coordinates, and an air traffic control strike really does
cancel the compensation and not the care.

Every pattern works the same book of trips, so switching pattern on the site
changes the *orchestration*, not the problem — which is exactly the comparison
the session is trying to draw.

The site explains all of this to an audience under **The domain** in the top
bar, built from the store itself rather than written out a second time.

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
  devui_input.py  makes DevUI ask for a prompt, not a Message form
  introspect.py   reads each agent's prompt and tools off the built workflow
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

## Publishing it to Azure

One command, once you are logged in. The image builds in Azure, so Docker is
not needed locally either.

```bash
az login
./infra/deploy.sh
```

It prints the URL. To change anything, pass it in:

```bash
RESOURCE_GROUP=rg-baltic LOCATION=northeurope ./infra/deploy.sh
```

Four explicit steps — environment, registry, `az acr build`, then create or
update the app. It deliberately avoids `az containerapp up`, which is a
convenience wrapper that rejects the global `az` arguments and whose
source-to-cloud path crashes inside the CLI's own `queue_acr_build`
(`'NoneType' object has no attribute 'linux'`). The explicit commands do the
same work and are the documented code-to-cloud path.

### How it is put together

Container Apps gives an app **one** external port, so both processes share a
container: DevUI binds loopback only and the showcase reverse-proxies it —
the SPA at `/devui/`, its API at `/v1/`, plus `/health` and `/meta`. That
works because DevUI asks for all of them with relative URLs, and it means one
origin, no CORS, and DevUI never exposed directly to the internet.

The image installs `agent-framework-core` rather than the `agent-framework`
meta-package. The meta-package pulls every provider — bedrock, gemini,
mistral, ollama, qdrant, cosmos — for a **920 MB** install against **97 MB**
for what this actually imports. On a container that scales to zero, that
difference is cold-start time in front of an audience.

### Before you make it public

It deploys **offline** (`CHAOS_PROVIDER=offline`). That is the safe default and
it is deliberate: a public URL with no authentication, running a deterministic
scripted client, costs nothing and cannot be abused beyond burning a little CPU.

**Point it at a real model and that changes.** Anyone who finds the URL can run
workflows against your Azure OpenAI deployment, and DevUI ships with
`auth_enabled=False` here so it can be embedded. If you want a live model on a
public URL, put authentication in front of it first — Container Apps
authentication (Easy Auth) with Entra ID is the least work:

```bash
az containerapp auth microsoft update -n chaos-to-symphony -g rg-chaos-to-symphony \
  --client-id <app-id> --tenant-id <tenant> --yes
az containerapp auth update -n chaos-to-symphony -g rg-chaos-to-symphony \
  --unauthenticated-client-action RedirectToLoginPage
```

### If the deploy drops out

`az` talking to `management.azure.com` from a laptop is the least reliable part
of this. A dropped connection surfaces as a `ConnectTimeout` traceback, which
looks alarming but usually means only that the CLI lost the poll — the resource
it was waiting on carries on provisioning in Azure.

**Re-run the script.** Every step checks what already exists first, so it
resumes rather than starting over. To see where it got to:

```bash
az group show -n rg-chaos-to-symphony --query properties.provisioningState -o tsv
az containerapp env show -n env-chaos-to-symphony -g rg-chaos-to-symphony \
  --query properties.provisioningState -o tsv
```

If the traceback ends in `JSONDecodeError: Expecting property name enclosed in
double quotes`, that is not a malformed request. Azure Resource Manager answers
a transient outage with a **503 and an HTML page** — *"Our services aren't
available right now"*, with `Ref A/B/C` underneath — and the CLI's own error
handler then dies trying to `json.loads()` that HTML. The real error is further
up the traceback, and it is Azure's, not yours.

The script retries the short ARM calls four times over about half a minute,
which covers a blip. If it gives up, ARM is genuinely unavailable: wait and
re-run, every step resumes.

If the traceback ends in `ConnectTimeout` against `management.azure.com`,
mentioning `containerappOperationStatuses`, **the deploy was not refused**. The
CLI had already submitted the change and was sitting on one long poll waiting
for it to finish; what died was that connection, not the rollout.

The script now expects this: it asks for the change, then polls the app's own
`provisioningState` in short calls that can each fail harmlessly, and reports
what the service says rather than what the client's socket did. If you hit it
on an older copy, nothing is lost — check, and re-run:

```bash
az containerapp show -n chaos-to-symphony -g rg-chaos-to-symphony \
  --query properties.provisioningState -o tsv
curl -s https://<your-app>/api/health
```

`Succeeded` means it landed. Re-running `make deploy` is safe either way — every
step checks what already exists and resumes.

If the failure is instead a Python traceback ending in `AttributeError` or
`TypeError` deep inside the CLI, that is a CLI bug rather than your setup.
`az upgrade` is worth a try; failing that, build and push by hand and point the
script at the result:

```bash
az acr build --registry <acr-name> --image chaos-to-symphony:manual --file Dockerfile .
az containerapp update -n chaos-to-symphony -g rg-chaos-to-symphony \
  --image <acr-name>.azurecr.io/chaos-to-symphony:manual
```

If a timeout happens repeatedly, suspect the network between you and Azure
rather than the script — a VPN, a corporate proxy, or an IPv6 route that
black-holes. A quick check:

```bash
curl -sS -o /dev/null -w '%{http_code} in %{time_total}s\n' https://management.azure.com/
curl -4 -sS -o /dev/null -w 'IPv4: %{http_code}\n' https://management.azure.com/
```

If the plain call hangs and `-4` succeeds, it is IPv6.

### Running costs

One always-warm replica so the first visitor does not pay a cold start. After
the session, scale it to zero or delete the group:

```bash
MIN_REPLICAS=0 ./infra/deploy.sh
az group delete -n rg-chaos-to-symphony --yes --no-wait
```

### Providers, and what "offline" actually means

`CHAOS_PROVIDER` takes `offline` (default), `openai`, `azure` or `foundry`.

**Offline is not a mock of the framework.** The orchestration is entirely real:
real `SequentialBuilder`, real handoff tool calls, real checkpointing, real
OpenTelemetry spans. What is scripted is the *model* — `ScriptedChatClient`
implements the same `BaseChatClient` contract a provider implements and derives
its answers from the in-memory travel data.

The consequence worth knowing before you present: on offline, the **decisions**
are deterministic Python, not model reasoning. Which specialist a handoff routes
to, when the group chat converges, what the Magentic progress ledger says — all
computed from the booking's own fields. The pattern mechanics are genuine; the
judgement inside them is not. If your point is "watch the orchestration work",
offline is honest and repeatable. If your point is "watch the *agent decide*",
run it live.

**`AZURE_OPENAI_DEPLOYMENT` is the deployment name, not the model name** — the
left-hand column in the Foundry portal's Model deployments list. A deployment
called `chat-model` running gpt-4o is `chat-model`.

To run live, supply the settings and the deploy script does the rest — the key
goes in as a Container Apps secret, never a plain environment value:

```bash
CHAOS_PROVIDER=azure \
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/ \
AZURE_OPENAI_DEPLOYMENT=gpt-4o-mini \
AZURE_OPENAI_API_KEY=<key> ./infra/deploy.sh
```

Azure OpenAI is reached by giving `OpenAIChatClient` an `azure_endpoint` — there
is no `AzureOpenAIChatClient` in Agent Framework. Locally, each live provider is
an extra:

```bash
pip install '.[openai]'    # OpenAI and Azure OpenAI
pip install '.[foundry]'   # Foundry Agent Service
```

The deployed image ships **both**, so switching provider there is an
environment-variable change rather than a rebuild. That matters more than the
~30MB: a missing extra does not fail, it falls back — so an image built without
`foundry` would answer `CHAOS_PROVIDER=foundry` by quietly running the scripted
client while the badge claimed a live model. Trim it with
`INSTALL_EXTRAS='.[openai]' ./infra/deploy.sh` if you only need one.

### If a change does not show up in the browser

The site has no build step, so `app.js` and `styles.css` keep the same names
forever. A browser handed a file with no freshness header is entitled to invent
one — commonly a tenth of the file's age — so after a redeploy you can end up
holding the new `index.html` and a cached `app.js`. The new markup renders a
control the old script knows nothing about, the click does nothing, and there
is no error anywhere to explain it.

The app therefore serves `/`, `/app.js` and `/styles.css` with
`Cache-Control: no-cache`, which means *store it, but ask before using it*. An
ordinary reload is enough to pick up a redeploy. If you are looking at an older
deploy that predates this, one hard refresh (Ctrl/Cmd+Shift+R) clears it.

### Bringing your own Foundry project

**Settings**, top right. Paste your project endpoint, your model deployment and
an access token, then **Test & use**: the site makes one tiny request to prove
all three are right, and from then on the runs *you* start call your model.

```bash
az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv
```

Your account needs the **Azure AI User** role on the project — the same role
the deployed app's identity needs on its own.

This is built for a public deploy, so it is deliberately narrow:

- **The site's own settings are never shown.** The panel says whether the host
  configured a live model, not which endpoint or deployment. `/api/config`,
  `/api/patterns` and `/api/health` carry no endpoint, and a failing run has
  the host's endpoint scrubbed from the error it prints.
- **Nothing a visitor enters is kept on the server.** Endpoint and deployment
  sit in the browser's `localStorage`, the token in `sessionStorage` (gone with
  the tab), and all three travel with each run. The server holds them for the
  length of that run, in a context variable, so two visitors never see each
  other's model.
- **A token, not a key, and no ambient identity.** A pasted Entra token expires
  within the hour and can do only what the visitor's own account can. Without
  one the form refuses, so a visitor can never borrow the site's managed
  identity — which would otherwise reach any project the host granted it.
- **Only Foundry project endpoints.** `https://<resource>.services.ai.azure.com/api/projects/<project>`
  and nothing else, so the form cannot make the server call an arbitrary URL.
- **It does not reach DevUI.** DevUI builds its twelve workflows at start-up, in
  its own process, and always uses the site's own provider.

The site's own provider can only be changed by redeploying with different
environment variables. There is no longer a way to change it from the page:
with no authentication, a setting one visitor saved would have been what every
other visitor ran against.

If you keep a deploy public after an event and do not want strangers spending
your quota — through the patterns *or* the embedded DevUI — redeploy it
offline and let visitors bring their own:

```bash
CHAOS_PROVIDER=offline ./infra/deploy.sh
```

### Deploying against Foundry

Foundry does not take a key. `FoundryChatClient` authenticates with
`DefaultAzureCredential`, which inside a container app means the app's own
managed identity, so there are two extra moving parts the script handles:

```bash
CHAOS_PROVIDER=foundry \
FOUNDRY_PROJECT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project> \
FOUNDRY_MODEL=<deployment> ./infra/deploy.sh
```

It assigns the app a system-assigned managed identity, resolves the Foundry
resource from the endpoint's hostname, and grants that identity the role it
needs to call the project. Microsoft renamed this role family — **Azure AI User**
became **Foundry User** — and tenants do not all show the new name yet, so both
are tried in turn; `FOUNDRY_ROLE='<name>'` pins one. `FOUNDRY_SCOPE='<arm-id>'`
scopes the grant somewhere tighter than the account, such as a single project.

If either step cannot be completed — the resource is in another subscription, or
you cannot assign roles on it — the script says so and prints the exact command
to finish it by hand, rather than leaving you to discover it from a 401. Two
things worth knowing:

- **RBAC is eventually consistent.** The first model calls after a fresh grant
  can still come back 401 for a minute or two.
- **A missing role does not degrade gracefully.** The fallback in `clients.py`
  only wraps client *construction*, so with the endpoint set but no role the app
  reports provider `foundry` and then fails on every run. `/api/health` tells you
  which state you are in: `requestedProvider` is what you asked for, `provider`
  is what the app could actually build.

Check a provider before running twelve patterns against it:

```bash
make check
```

One minimal request, then a plain answer — including the API version and
endpoint it actually used, so you are never guessing what is in play:

```
The provider works. It is using:
  api_version : preview
  endpoint    : https://<resource>.openai.azure.com/openai/v1/
```

**Leave `AZURE_OPENAI_API_VERSION` unset.** Left alone, the framework targets
Azure OpenAI's **v1 surface** — `base_url` ending `/openai/v1/` with
`api_version` set to the literal string `preview`, not a date. The base URL is
that v1 path either way, so pinning a *dated* version sends a dated
`api-version` to a v1 endpoint, and the service answers `400 API version not
supported`. That is precisely how a hard-coded `2024-10-21` default broke the
first live run here.

Pin it only if your resource genuinely needs a dated version. If the check
fails with that error it walks the candidates — `preview` first, then the dated
ones — and tells you which your resource accepts.

The badge in the header reports the provider **actually in use**, not the one
configured. Ask for a provider that is not available and it turns red and says
so, rather than claiming a live model while every agent runs scripted.

---

## Credits and references

- [microsoft/agent-framework](https://github.com/microsoft/agent-framework) — the framework itself
- [Agent Framework docs](https://learn.microsoft.com/en-us/agent-framework/)
- [AI agent design patterns](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns) — Azure Architecture Center

Built against Agent Framework **1.19.0**, orchestrations **1.2.0**, DevUI
**1.0.0b260918**.

MIT licensed. The Baltic Summit template and logo remain the property of
Baltic Summit.
