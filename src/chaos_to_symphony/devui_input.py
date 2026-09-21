"""Make every workflow in DevUI ask for a prompt, not a form.

DevUI builds a workflow's input control from whatever type its *start* executor
declares. The four hand-built graph patterns declare plain ``str``, so DevUI
shows a text box and you type the case into it. The five orchestration builders
do not: ``SequentialBuilder`` and friends put an input adapter in front that
accepts ``Message``, ``list[str | Message]`` *and* ``str``, and DevUI's
``select_primary_input_type`` returns the first Message-ish type it finds. So
eight of the twelve patterns open "Configure Workflow Inputs" and demand
``role``, ``contents``, ``author_name`` and ``message_id`` before they will run.

It is not even DevUI's chat box. Its frontend recognises a chat message by
looking for a ``text`` property, and the framework's ``Message`` carries
``contents`` instead - so the detection misses and the raw struct is rendered
as a generic form. Twelve identical patterns, two different front doors, and
the more interesting eight get the worse one.

None of that information is needed. Every one of those adapters already accepts
a bare ``str`` and wraps it into a conversation itself, which is exactly what
the showcase site's "Run it" box relies on. So the fix is to tell DevUI to pick
``str`` whenever the start executor accepts one: same workflows, same dispatch,
one text box everywhere.

DevUI resolves this type twice - once to build the schema behind
``/v1/entities/{id}/info``, once to parse what you typed before the run - and
both call sites import the function inside the calling function. Rebinding the
module attribute therefore reaches both, and the schema can never disagree with
the parser.
"""

from __future__ import annotations

import logging
import types
import typing
from typing import Any, get_args, get_origin

logger = logging.getLogger(__name__)

#: ``typing.Union[int, str]`` and ``int | str`` report different origins.
_UNION_ORIGINS = (typing.Union, types.UnionType)


def accepts_plain_text(message_types: list[Any]) -> bool:
    """True when one of the declared input types is a bare ``str``.

    An executor may declare ``str`` on its own or inside a union - the
    declarative ``JoinExecutor`` form is a single ``dict | str | ...`` element -
    so both shapes count.
    """
    for declared in message_types:
        if declared is str:
            return True
        if get_origin(declared) in _UNION_ORIGINS and any(arg is str for arg in get_args(declared)):
            return True
    return False


def install() -> bool:
    """Prefer ``str`` over ``Message`` when DevUI picks a workflow's input type.

    Returns True if the patch went in. It is a private function of a beta
    package, so treat a failure as cosmetic: log it and let DevUI start with its
    own behaviour rather than taking the demo down over an input widget.
    """
    try:
        from agent_framework_devui import _utils
    except Exception:
        logger.warning("Could not import agent_framework_devui._utils; DevUI keeps its own input forms")
        return False

    original = getattr(_utils, "select_primary_input_type", None)
    if original is None:
        logger.warning("agent_framework_devui._utils.select_primary_input_type is gone; input forms left alone")
        return False
    if getattr(original, "_chaos_prefers_text", False):
        return True  # Already installed - entities() and main() may both call this.

    def select_primary_input_type(message_types: list[Any]) -> Any | None:
        if accepts_plain_text(message_types):
            return str
        return original(message_types)

    select_primary_input_type._chaos_prefers_text = True  # type: ignore[attr-defined]
    _utils.select_primary_input_type = select_primary_input_type
    return True
