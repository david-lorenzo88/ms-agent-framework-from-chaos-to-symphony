"""In-process OpenTelemetry capture.

Why this exists
---------------
DevUI renders the events of runs *it* started. Its frontend reads only
``entity_id`` from the URL, keeps its timeline in client-side state, exposes no
postMessage API, and this build ships no server-side trace-retrieval endpoint -
so a run started from the showcase can never appear in the embedded DevUI
timeline. Rather than re-run the pattern just to see it through the framework's
eyes, the showcase captures the same telemetry itself.

Agent Framework emits proper OpenTelemetry spans: ``invoke_agent <name>``,
``execute_tool <name>``, ``executor.process <id>``, plus the edge and message
plumbing underneath. That is the substance of what DevUI's trace panel shows,
and it is available in whichever process actually runs the workflow.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger(__name__)

#: The span sink for the run on this task. A ContextVar rather than a module
#: global so two runs started at once cannot pour spans into each other - the
#: concurrent pattern spawns sub-tasks, and they inherit this.
_SINK: ContextVar[list[dict[str, Any]] | None] = ContextVar("chaos_span_sink", default=None)

_installed = False


def _classify(name: str) -> str:
    """Bucket a span by what the audience would call it."""
    if name.startswith("invoke_agent"):
        return "agent"
    if name.startswith("execute_tool"):
        return "tool"
    if name.startswith("executor.process"):
        return "executor"
    if name.startswith("edge_group") or name.startswith("message."):
        return "plumbing"
    if name.startswith("workflow"):
        return "workflow"
    return "other"


def _describe(span: Any) -> dict[str, Any]:
    """Turn a finished span into a JSON-ready row."""
    attributes = dict(span.attributes or {})
    name = span.name or ""
    subject = attributes.get("executor.id") or ""
    if not subject and " " in name:
        # "invoke_agent intake-agent" / "execute_tool lookup_booking"
        subject = name.split(" ", 1)[1]
    return {
        "name": name,
        "kind": _classify(name),
        "subject": subject,
        "startNs": span.start_time,
        "durationMs": round((span.end_time - span.start_time) / 1e6, 3),
        "status": str(getattr(span.status, "status_code", "")),
    }


def install() -> bool:
    """Install the tracer provider and span sink. Idempotent.

    Order matters: a TracerProvider has to be set *before*
    ``enable_instrumentation`` runs, otherwise the global provider stays the
    no-op ProxyTracerProvider and every span is silently dropped.
    """
    global _installed
    if _installed:
        return True
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SpanProcessor

        class _SpanSink(SpanProcessor):
            def on_start(self, span: Any, parent_context: Any = None) -> None:  # noqa: D102
                pass

            def on_end(self, span: Any) -> None:  # noqa: D102
                sink = _SINK.get()
                if sink is not None:
                    sink.append(_describe(span))

            def shutdown(self) -> None:  # noqa: D102
                pass

            def force_flush(self, timeout_millis: int | None = None) -> bool:  # noqa: D102
                return True

        provider = TracerProvider()
        provider.add_span_processor(_SpanSink())
        trace.set_tracer_provider(provider)

        from agent_framework.observability import enable_instrumentation

        enable_instrumentation()
        _installed = True
        return True
    except Exception:
        # Telemetry is a nice-to-have; a demo must still run without it.
        logger.exception("Could not install OpenTelemetry capture; the Traces tab will stay empty")
        return False


@contextmanager
def collect():
    """Collect the spans emitted inside this block."""
    sink: list[dict[str, Any]] = []
    token = _SINK.set(sink)
    try:
        yield sink
    finally:
        _SINK.reset(token)


def summarise(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order spans by start time and add an offset, for a waterfall view."""
    if not spans:
        return []
    origin = min(s["startNs"] for s in spans)
    rows = []
    for span in sorted(spans, key=lambda s: s["startNs"]):
        rows.append({
            "name": span["name"],
            "kind": span["kind"],
            "subject": span["subject"],
            "offsetMs": round((span["startNs"] - origin) / 1e6, 3),
            "durationMs": span["durationMs"],
        })
    return rows
