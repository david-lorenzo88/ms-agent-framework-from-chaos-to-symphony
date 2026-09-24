"""Pattern 11 - Checkpoint and resume.

New material this year. The workflow is run, killed halfway, and restarted from
its last checkpoint in a *different* Workflow instance - which is the only
honest way to demonstrate durability. Restarting the object you never lost
proves nothing.

The point on stage: checkpointing is what turns a long multi-agent workflow
from a thing that must survive a deploy into a thing that simply resumes after
one. Storage here is in memory, deliberately; the interface is identical for
FileCheckpointStorage or Cosmos.
"""

from __future__ import annotations

from agent_framework import Agent, InMemoryCheckpointStorage
from agent_framework.orchestrations import SequentialBuilder

from ..base import CaseBrief, CaseFact, DiagramEdge, DiagramNode, PatternSpec
from ..clients import chat_client
from ..memory import STORE
from ..tools import CASE_TOOLS

#: Shared between the original run and the resumed one. In production this is
#: the only piece that has to outlive the process.
CHECKPOINTS = InMemoryCheckpointStorage()

#: Checkpoints are scoped by workflow name, so the resumed instance must build
#: under the same name or it will not find the run it is meant to continue.
WORKFLOW_NAME = "CheckpointResume"


def _participants() -> list[Agent]:
    """Three stages, so there is something meaningful to resume into."""
    return [
        Agent(
            client=chat_client("intake-agent"),
            name="intake-agent",
            description="Establishes the facts.",
            instructions="State the facts of the exception in two sentences.",
            tools=CASE_TOOLS,
        ),
        Agent(
            client=chat_client("pricing-specialist"),
            name="pricing-specialist",
            description="Prices the exposure.",
            instructions="Price the exposure against the customer's SLA band.",
            tools=CASE_TOOLS,
        ),
        Agent(
            client=chat_client("writer-agent"),
            name="writer-agent",
            description="Writes the letter.",
            instructions="Write the customer letter conveying the outcome.",
        ),
    ]


def build():
    """A checkpointed sequential pipeline. One keyword turns durability on."""
    return SequentialBuilder(
        name=WORKFLOW_NAME,
        participants=_participants(),
        checkpoint_storage=CHECKPOINTS,
        output_from="all",
    ).build()


async def demo(prompt: str) -> list[str]:
    """Run, interrupt, rebuild from scratch, resume. Returns narration lines."""
    notes: list[str] = []
    STORE.record("checkpoint:demo", "start", prompt[:60], pattern="checkpoint-resume")

    # --- first run, abandoned deliberately after the first stage ----------
    first = build()
    stages = 0
    async for event in first.run(prompt, stream=True):
        if event.type == "executor_completed":
            stages += 1
            if stages >= 2:
                notes.append(f"Interrupted the run after {stages} executor(s) - simulating a pod restart.")
                break
    del first  # the object is gone; only the checkpoint store survives

    saved = await CHECKPOINTS.list_checkpoints(workflow_name=WORKFLOW_NAME)
    if not saved:
        notes.append("No checkpoint was written - nothing to resume from.")
        return notes

    latest = sorted(saved, key=lambda c: (c.iteration_count, c.timestamp))[-1]
    notes.append(f"{len(saved)} checkpoint(s) in storage. Latest: {str(latest.checkpoint_id)[:18]}.")

    # --- a brand new Workflow object, resuming the old run ----------------
    resumed = build()
    notes.append("Built a NEW workflow instance - it shares only the checkpoint store.")
    outputs: list[str] = []
    async for event in resumed.run(checkpoint_id=latest.checkpoint_id, stream=True):
        if event.type == "output":
            text = getattr(event.data, "text", None) or str(event.data)
            if text.strip():
                outputs.append(text.strip())
    # Streamed output arrives in chunks; join before narrating so the line reads
    # as one answer rather than as the transport's frame size.
    joined = " ".join(outputs)
    notes.append("Resumed from the checkpoint and ran to completion.")
    if joined:
        notes.append(f"  -> {joined[:260]}")
    STORE.record("checkpoint:demo", "resumed", f"{len(outputs)} outputs", pattern="checkpoint-resume")
    return notes


SPEC = PatternSpec(
    slug="checkpoint-resume",
    number=11,
    name="Checkpoint & resume",
    tier="production",
    tagline="Kill it halfway. A new instance picks up where the dead one stopped.",
    summary=(
        "Passing checkpoint storage to any builder makes the workflow persist its state as it advances. A run "
        "can then be resumed by id - in a different object, a different process, a different pod. This demo "
        "proves it the only way that counts: the first workflow instance is discarded mid-run and a freshly "
        "built one finishes the job from the checkpoint alone. Storage is in memory here; the same interface "
        "backs file and Cosmos storage."
    ),
    use_when=(
        "Runs are long enough that a deploy or a crash would otherwise lose real work.",
        "A human approval could take hours or days and must survive a restart.",
        "You need to replay or audit exactly what state the workflow was in.",
    ),
    avoid_when=(
        "Runs are seconds long and cheap to repeat - persistence is pure overhead.",
        "The workflow's side effects are not idempotent, so a replay would double-charge.",
        "State contains personal data you would then have to govern in another store.",
    ),
    maf_api=(
        "SequentialBuilder(..., checkpoint_storage=InMemoryCheckpointStorage())",
        "await storage.list_checkpoints()",
        "workflow.run(checkpoint_id=..., stream=True)",
    ),
    failure_mode=(
        "Replayed side effects. Resuming re-enters the graph, and any node that already sent an email or "
        "took a payment will happily do it again. Make effectful nodes idempotent with a key derived from "
        "workflow state - and remember a checkpoint is a copy of your data, subject to the same retention "
        "rules as everything else."
    ),
    scenario="A three-stage claim pipeline, interrupted after stage one by a pod restart.",
    case=CaseBrief(
        about=(
            "A consignment of diagnostic kits reached a Warsaw hospital tender three days late and the consignee has "
            "invoked a penalty clause. It is an ordinary three-stage claim: get the facts, price the exposure, write "
            "to the customer. Then, halfway through, the pod running it dies."
        ),
        why=(
            "Losing the run would mean re-doing every model call already paid for - and if a human approval were "
            "sitting in the middle of it, losing their decision too. So this demo proves the recovery the only way "
            "that counts. It does not pause and continue the same object. It throws the first workflow instance away "
            "entirely and builds a brand new one, which finishes the job from the checkpoint alone. Watch the log for "
            "the point where the first instance is discarded. Storage is in memory here; the same interface backs file "
            "and Cosmos storage."
        ),
        facts=(
            CaseFact("Shipment", "BFG-24090"),
            CaseFact("Lane", "Helsinki to Warsaw (FI-PL)"),
            CaseFact("What happened", "Three days late into a hospital tender; penalty clause invoked"),
            CaseFact("Declared value", "EUR 178,000"),
            CaseFact("Customer", "Helsinki Pharma Oy, gold tier"),
            CaseFact("The interruption", "Killed after stage one, resumed in a different instance"),
        ),
    ),
    default_prompt="Work the exception on shipment BFG-24090 and write the customer letter.",
    nodes=(
        DiagramNode("s1", "intake-agent", "agent"),
        DiagramNode("s2", "pricing-specialist", "agent"),
        DiagramNode("s3", "writer-agent", "agent"),
        DiagramNode("store", "InMemoryCheckpointStorage", "store"),
        DiagramNode("new", "New workflow instance", "executor"),
    ),
    edges=(
        DiagramEdge("s1", "s2", "stage 1 done"),
        DiagramEdge("s1", "store", "checkpoint", "dashed"),
        DiagramEdge("s2", "store", "checkpoint", "dashed"),
        DiagramEdge("store", "new", "resume by id", "loop"),
        DiagramEdge("new", "s3", "continues"),
    ),
    devui_name="CheckpointResume",
    build=build,
    demo=demo,
    new_this_year=True,
)
