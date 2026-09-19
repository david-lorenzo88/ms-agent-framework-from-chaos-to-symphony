"""The single source of truth for what patterns exist.

DevUI, the showcase API and the website all read this. Adding a pattern means
adding a module and one import here - nothing else changes.
"""

from __future__ import annotations

from .base import PatternSpec
from .patterns import (
    p01_sequential,
    p02_concurrent,
    p03_group_chat,
    p04_handoff,
    p05_magentic,
    p06_switch_case,
    p07_map_reduce,
    p08_reflection_loop,
    p09_sub_workflow,
    p10_human_in_the_loop,
    p11_checkpoint_resume,
    p12_guardrails,
)

#: In the order the session presents them.
PATTERNS: tuple[PatternSpec, ...] = (
    p01_sequential.SPEC,
    p02_concurrent.SPEC,
    p03_group_chat.SPEC,
    p04_handoff.SPEC,
    p05_magentic.SPEC,
    p06_switch_case.SPEC,
    p07_map_reduce.SPEC,
    p08_reflection_loop.SPEC,
    p09_sub_workflow.SPEC,
    p10_human_in_the_loop.SPEC,
    p11_checkpoint_resume.SPEC,
    p12_guardrails.SPEC,
)

BY_SLUG: dict[str, PatternSpec] = {spec.slug: spec for spec in PATTERNS}

#: How the session groups them on the agenda and on the website.
TIERS: tuple[tuple[str, str, str], ...] = (
    (
        "core",
        "The five orchestrations",
        "Stable in Agent Framework 1.0, in Python and .NET. The same five as last year - now all five run.",
    ),
    (
        "graph",
        "The workflow graph",
        "What sits underneath the five. Reach for these when no named pattern fits.",
    ),
    (
        "production",
        "Production concerns",
        "The parts that only matter once real users arrive. Governance, durability, and failure.",
    ),
)


def get(slug: str) -> PatternSpec:
    """Look up one pattern, raising a clear error for an unknown slug."""
    try:
        return BY_SLUG[slug]
    except KeyError:
        raise KeyError(f"Unknown pattern '{slug}'. Known: {', '.join(BY_SLUG)}") from None
