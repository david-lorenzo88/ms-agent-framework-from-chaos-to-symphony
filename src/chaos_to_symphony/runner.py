"""Drives one pattern run and turns Agent Framework events into a live feed.

The showcase site needs three things from a run that the framework emits as one
event stream: log lines, which diagram node is busy, and any pause waiting on a
human. This module does that translation and nothing else, so the pattern
modules stay free of presentation concerns.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from . import telemetry
from .base import PatternSpec
from .memory import STORE
from .scripted import SEND_BACK_INSTRUCTION, reset_context

#: How long a human-in-the-loop pause waits for a click before it approves
#: itself. A demo that hangs forever because nobody pressed the button is worse
#: than one that visibly auto-approves and says so.
APPROVAL_TIMEOUT_SECONDS = 90


@dataclass
class RunSession:
    """One execution of one pattern, streamed to one or more browsers."""

    run_id: str
    spec: PatternSpec
    prompt: str
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    approvals: dict[str, asyncio.Future[str]] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    done: bool = False
    task: asyncio.Task[None] | None = None
    traces: list[dict[str, Any]] = field(default_factory=list)
    """OpenTelemetry spans for this run, kept so switching to the Traces tab
    afterwards shows the run you just did rather than nothing."""

    # -- emit helpers ------------------------------------------------------

    def emit(self, kind: str, **payload: Any) -> None:
        """Push one frame to the browser."""
        self.queue.put_nowait({"kind": kind, "t": round(time.time() - self.started_at, 2), **payload})

    def log(self, level: str, source: str, message: str) -> None:
        self.emit("log", level=level, source=source, message=message)

    def activate(self, node_id: str, state: str = "active") -> None:
        self.emit("node", node=node_id, state=state)

    # -- diagram mapping ---------------------------------------------------

    def node_for(self, executor_id: str) -> str | None:
        """Best-effort map from a framework executor id to a diagram node.

        Diagram nodes are authored for the audience, so their ids are short
        ('customs') while the framework's are full agent names
        ('customs-specialist'). Match on label first, then on containment.

        The third rule exists because the orchestration builders name their
        coordinator after the *pattern*, not after the agent you handed them:
        Magentic reports 'magentic_orchestrator' and GroupChat reports
        'group_chat_orchestrator', neither of which resembles the manager or
        chair drawn on the diagram. Those are the most important boxes in both
        pictures, and without this they were the only two that never lit up.
        """
        if not executor_id:
            return None
        lowered = executor_id.lower()

        for node in self.spec.nodes:
            if node.label.lower() == lowered:
                return node.id
        for node in self.spec.nodes:
            if node.id.lower() in lowered or lowered in node.label.lower():
                return node.id

        if "orchestrator" in lowered or "manager" in lowered:
            coordinators = [n for n in self.spec.nodes if n.kind == "orchestrator"]
            # Only when it is unambiguous: Concurrent draws two (dispatcher and
            # aggregator) and guessing between them would light the wrong box.
            if len(coordinators) == 1:
                return coordinators[0].id
        return None

    # -- approval ----------------------------------------------------------

    def answer(self, request_id: str, decision: str) -> bool:
        """Resolve a pending approval from the browser. Returns False if unknown."""
        future = self.approvals.get(request_id)
        if future is None or future.done():
            return False
        future.set_result(decision)
        return True


async def _drive_workflow(session: RunSession) -> None:
    """Run a workflow with streaming, translating events as they arrive."""
    from agent_framework.orchestrations import AgentRequestInfoResponse, HandoffAgentUserRequest

    workflow = session.spec.build()
    stream = workflow.run(session.prompt, stream=True)
    responses: dict[str, Any] | None = None

    #: Executors that were actually asked to do something, as opposed to ones
    #: handed a copy of the conversation to keep their history in step. Only
    #: the first kind should light up in the diagram - see _is_sync_broadcast.
    working: set[str] = set()

    while True:
        pending: dict[str, Any] = {}
        async for event in stream:
            kind = getattr(event, "type", "")
            executor_id = getattr(event, "executor_id", "") or ""
            node = session.node_for(executor_id)

            if kind == "executor_invoked":
                if _is_sync_broadcast(event):
                    # Real, but not work. Say so instead of lighting the box.
                    # "muted": still on the record, because it explains why a
                    # fully connected graph invokes everyone, but dimmed - on a
                    # four-seat committee these outnumber the actual turns.
                    session.log("muted", executor_id or "workflow",
                                "sent the conversation to stay in step - no response requested")
                    continue
                working.add(executor_id)
                if node:
                    session.activate(node, "active")
                session.log("info", executor_id or "workflow", "invoked")

            elif kind == "executor_completed":
                # The broadcast recipients complete too, and completing is what
                # turns a box green. Only close the ones that opened.
                if executor_id and executor_id not in working:
                    continue
                if node:
                    session.activate(node, "done")
                session.log("info", executor_id or "workflow", "completed")

            elif kind == "agent_run_update":
                text = _text_of(getattr(event, "data", None))
                if text:
                    session.emit("token", source=executor_id or "agent", text=text)

            elif kind == "output":
                text = _text_of(getattr(event, "data", None))
                if text.strip():
                    session.emit("output", source=executor_id or "workflow", text=text)

            elif kind == "request_info":
                request_id = getattr(event, "request_id", "") or str(uuid.uuid4())
                request = getattr(event, "data", None)

                if isinstance(request, HandoffAgentUserRequest):
                    # Not an approval gate. Handoff is conversational: after a
                    # participant speaks it asks what the *user* says next, and
                    # it wants list[Message] back. Answering it with an approval
                    # object raises "Response type mismatch" and strands the run.
                    # A showcase run is single-shot, so close the conversation.
                    session.log("info", "request_info",
                                "handoff asked for the user's next turn - closing the conversation")
                    pending[request_id] = HandoffAgentUserRequest.create_response(
                        "That resolves it, thank you."
                    )
                else:
                    decision = await _ask_human(session, request_id, event)
                    if decision == "approve":
                        pending[request_id] = AgentRequestInfoResponse.approve()
                    else:
                        # An empty response approves, so a send-back has to carry
                        # a message: that is what sends the proposal back round
                        # the gated agent instead of past it.
                        session.log("warn", "request_info",
                                    "sent back - the settlement agent re-prices and the gate asks again")
                        pending[request_id] = AgentRequestInfoResponse.from_strings([SEND_BACK_INSTRUCTION])

            elif kind == "error":
                session.log("error", executor_id or "workflow", str(getattr(event, "data", "")))

        if not pending:
            break
        session.log("info", "workflow", f"resuming with {len(pending)} human response(s)")
        responses = pending
        stream = workflow.run(stream=True, responses=responses)


def _is_sync_broadcast(event: Any) -> bool:
    """True when an executor was only handed the conversation to stay in step.

    Handoff and group chat build a fully connected graph: after each turn the
    active agent broadcasts the conversation to every other participant so
    their histories match, as an ``AgentExecutorRequest`` with
    ``should_respond=False``. Each recipient really is invoked - it files the
    messages and returns without calling its model.

    Those are honest framework events, but they answer "who received a
    message", not "who worked the case". Lighting the diagram off them turns
    every specialist green on a pattern whose entire point is that exactly one
    of them was chosen, which is the opposite of the lesson.
    """
    return getattr(getattr(event, "data", None), "should_respond", None) is False


async def _ask_human(session: RunSession, request_id: str, event: Any) -> str:
    """Suspend the run and wait for a click, or auto-approve after a timeout."""
    from .patterns.p10_human_in_the_loop import approval_context

    future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    session.approvals[request_id] = future
    session.emit(
        "approval",
        requestId=request_id,
        proposal=_text_of(getattr(event, "data", None))[:800],
        context=approval_context(session.prompt),
        timeoutSeconds=APPROVAL_TIMEOUT_SECONDS,
    )
    session.log("warn", "request_info", "workflow SUSPENDED - waiting on a human decision")
    try:
        decision = await asyncio.wait_for(future, timeout=APPROVAL_TIMEOUT_SECONDS)
        session.log("info", "request_info", f"human decided: {decision}")
    except asyncio.TimeoutError:
        decision = "approve"
        session.log("warn", "request_info", "no answer in time - auto-approved so the demo can continue")
    session.emit("approvalResolved", requestId=request_id, decision=decision)
    return decision


def _text_of(data: Any) -> str:
    """Pull displayable text out of whatever an event carried."""
    if data is None:
        return ""
    for attribute in ("text", "message"):
        value = getattr(data, attribute, None)
        if isinstance(value, str) and value:
            return value
    response = getattr(data, "agent_response", None)
    if response is not None:
        value = getattr(response, "text", None)
        if isinstance(value, str):
            return value
    return str(data)


async def execute(session: RunSession) -> None:
    """Run one pattern to completion, however that pattern needs to be run."""
    reset_context()
    STORE.reset()
    session.log("info", "runner", f"provider={_provider()}  pattern={session.spec.slug}")
    session.emit("start", pattern=session.spec.slug, prompt=session.prompt)
    try:
        # Everything the workflow emits inside this block is captured, including
        # from the sub-tasks the concurrent pattern spawns.
        with telemetry.collect() as spans:
            if session.spec.demo is not None:
                # Checkpoint-resume and guardrails drive the workflow more than
                # once, so they narrate themselves.
                session.log("info", "runner", "pattern supplies its own runner")
                for line in await session.spec.demo(session.prompt):
                    session.emit("output", source=session.spec.slug, text=line)
            else:
                await _drive_workflow(session)
        session.traces = telemetry.summarise(spans)
        session.log("info", "runner", f"run complete - {len(session.traces)} spans captured")
    except Exception as exc:
        session.log("error", "runner", f"{type(exc).__name__}: {exc}")
    finally:
        session.emit("traces", rows=session.traces)
        session.emit("audit", rows=STORE.audit_dicts())
        session.emit("end")
        session.done = True


def _provider() -> str:
    from .clients import provider

    return provider()


async def cancel(session: RunSession) -> None:
    """Stop a run that is still going."""
    if session.task and not session.task.done():
        session.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await session.task
