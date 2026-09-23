"""What each agent in a pattern is actually configured with.

The session's whole argument is that the orchestration is real, so the answer
to "what is this agent?" has to come from the built workflow rather than from a
list somebody maintained alongside it. Everything here is read back out of the
``Agent`` objects the pattern module constructed: its instructions, the tools
bound to it, the chat client it will really drive. Change a prompt in
``patterns/p04_handoff.py`` and this changes with it, because there is nowhere
else for it to be read from.

Two things are worth the walk rather than a flat scan of ``workflow.executors``:

* Some agents live one level down. Human-in-the-loop wraps its gated
  participant in an ``AgentApprovalExecutor`` and sub-workflow composition
  embeds a whole ``Workflow`` behind a ``WorkflowExecutor``; both carry a
  nested ``workflow``, and a flat scan simply loses those agents.
* The handoff tools do not exist in the source at all. ``HandoffBuilder``
  generates a ``handoff_to_<target>`` tool per permitted edge at build time, so
  reading them off the built agent is the only way to show that routing is a
  tool call - and that triage can reach four specialists while a specialist can
  only hand back.
"""

from __future__ import annotations

from typing import Any

#: Prefix HandoffBuilder gives the routing tools it generates.
_HANDOFF_PREFIX = "handoff_to_"


def _tool_of(tool: Any) -> dict[str, Any]:
    """One tool, as the site shows it."""
    name = str(getattr(tool, "name", "") or getattr(tool, "__name__", "") or "tool")
    return {
        "name": name,
        "description": str(getattr(tool, "description", "") or "").strip(),
        "kind": "handoff" if name.startswith(_HANDOFF_PREFIX) else "function",
    }


def _agent_of(executor_id: str, agent: Any, *, nested: bool) -> dict[str, Any]:
    """One agent's configuration, flattened for the API."""
    options: dict[str, Any] = getattr(agent, "default_options", None) or {}
    response_format = options.get("response_format")
    return {
        "executorId": executor_id,
        "name": str(getattr(agent, "name", "") or executor_id),
        "description": str(getattr(agent, "description", "") or "").strip(),
        "instructions": str(options.get("instructions") or "").strip(),
        "tools": [_tool_of(t) for t in (options.get("tools") or [])],
        # Which client this agent will really drive. Worth showing next to the
        # prompt: it is the difference between the scripted demo and a live
        # model, and it is per agent rather than a global setting.
        "client": type(getattr(agent, "client", None)).__name__,
        # Set when the agent is pinned to a schema instead of prose - the
        # switch-case classifier is the one the routing depends on.
        "structuredOutput": getattr(response_format, "__name__", None),
        "nested": nested,
    }


def agents_in(workflow: Any) -> list[dict[str, Any]]:
    """Every agent wired into a workflow, in graph order, nested ones included."""
    found: list[dict[str, Any]] = []
    seen_workflows: set[int] = {id(workflow)}

    def walk(current: Any, nested: bool) -> None:
        executors = getattr(current, "executors", None) or {}
        for executor in executors.values() if isinstance(executors, dict) else executors:
            agent = getattr(executor, "agent", None)
            if agent is not None and hasattr(agent, "default_options"):
                found.append(_agent_of(str(getattr(executor, "id", "?")), agent, nested=nested))
            inner = getattr(executor, "workflow", None)
            # Guard against a cycle: a workflow that somehow reaches itself
            # would otherwise recurse until the stack runs out.
            if inner is not None and id(inner) not in seen_workflows:
                seen_workflows.add(id(inner))
                walk(inner, True)

    walk(workflow, False)
    return found
