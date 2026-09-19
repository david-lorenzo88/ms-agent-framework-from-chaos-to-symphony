"""Shared shape for a pattern demo.

Every pattern module exposes one :class:`PatternSpec`. The spec carries both
the *teaching* material (what it is, when to use it, when not to) and the
*runnable* material (a ``build`` callable returning a Workflow). The DevUI app,
the showcase API and the website all read the same registry, so a pattern is
described in exactly one place.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def parse_structured(model: type[ModelT], text: str) -> ModelT:
    """Validate a model's reply against a schema, tolerating how models write.

    ``response_format`` asks for clean JSON and the offline client obliges
    exactly. A real model mostly does too - but not always: it may wrap the
    object in a markdown fence, or put a sentence in front of it. Failing to
    parse kills the run, and "the model added a code fence" is a poor reason
    for a demo to die on stage, so try the strict form first and fall back to
    finding the JSON object inside the text.
    """
    candidates = [text]
    fenced = _FENCE.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    # Outermost braces: handles a leading "Here is the result:".
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    last: Exception | None = None
    for candidate in candidates:
        try:
            return model.model_validate_json(candidate)
        except Exception as exc:  # pragma: no cover - depends on the model
            last = exc
    raise ValueError(
        f"Could not parse a {model.__name__} from the model's reply: {text[:200]!r}"
    ) from last


@dataclass(frozen=True, slots=True)
class DiagramNode:
    """One box in the interaction diagram."""

    id: str
    label: str
    kind: str = "agent"  # agent | orchestrator | executor | gate | store | human


@dataclass(frozen=True, slots=True)
class DiagramEdge:
    """One arrow in the interaction diagram."""

    source: str
    target: str
    label: str = ""
    style: str = "solid"  # solid | dashed | loop


@dataclass(frozen=True, slots=True)
class PatternSpec:
    """Everything the session needs to know about one orchestration pattern."""

    slug: str
    number: int
    name: str
    tier: str  # core | graph | production
    tagline: str
    summary: str
    use_when: tuple[str, ...]
    avoid_when: tuple[str, ...]
    maf_api: tuple[str, ...]
    failure_mode: str
    """The way this pattern breaks in production, and the knob that stops it."""
    scenario: str
    """The Baltic Freight case this demo runs."""
    default_prompt: str
    nodes: tuple[DiagramNode, ...]
    edges: tuple[DiagramEdge, ...]
    build: Callable[[], Any] = field(repr=False, default=None)  # type: ignore[assignment]
    demo: Callable[[str], Any] | None = field(repr=False, default=None)
    """Optional custom runner, for patterns whose story is not a single ``run()``.

    Checkpoint-resume and graceful degradation both need to drive the workflow
    more than once - stop it, inspect it, restart it - so they supply an async
    callable returning narration lines instead of relying on the generic runner.
    """
    devui_name: str = ""
    """The Workflow name DevUI registers this pattern under.

    DevUI mints ``{type}_{source}_{name}_{uuid}`` entity ids at start-up, so the
    uuid cannot be known ahead of time. The website resolves an id by matching
    this name against DevUI's /v1/entities listing.
    """
    new_this_year: bool = False
    """True for material that did not exist in the 2025 edition of this talk."""

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form, for the showcase API."""
        return {
            "slug": self.slug,
            "number": self.number,
            "name": self.name,
            "tier": self.tier,
            "tagline": self.tagline,
            "summary": self.summary,
            "useWhen": list(self.use_when),
            "avoidWhen": list(self.avoid_when),
            "mafApi": list(self.maf_api),
            "failureMode": self.failure_mode,
            "scenario": self.scenario,
            "defaultPrompt": self.default_prompt,
            "newThisYear": self.new_this_year,
            "devuiName": self.devui_name,
            "hasCustomRunner": self.demo is not None,
            "diagram": {
                "nodes": [{"id": n.id, "label": n.label, "kind": n.kind} for n in self.nodes],
                "edges": [
                    {"source": e.source, "target": e.target, "label": e.label, "style": e.style} for e in self.edges
                ],
            },
        }
