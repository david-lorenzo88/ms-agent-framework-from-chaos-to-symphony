"""Chat-client factory.

One switch, three providers. ``CHAOS_PROVIDER=offline`` is the default because
a session demo has to work when the venue wifi does not; ``azure`` and
``openai`` swap in a real model without any pattern code changing, which is the
point worth making from the stage - the orchestration layer does not know or
care which client it is driving.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from .scripted import ScriptedChatClient


def provider() -> str:
    """The configured provider name, lowercased."""
    return os.getenv("CHAOS_PROVIDER", "offline").strip().lower()


def is_offline() -> bool:
    return provider() == "offline"


@lru_cache(maxsize=1)
def _warn_once(message: str) -> None:
    import logging

    logging.getLogger(__name__).warning(message)


def chat_client(persona: str = "agent", *, tool_budget: int = 1) -> Any:
    """Return a chat client for one agent persona.

    Falls back to the offline client - loudly - if a real provider is selected
    but not configured. A half-configured provider should degrade to a working
    demo, not a stack trace in front of an audience.
    """
    name = provider()

    if name == "azure":
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        if endpoint and deployment:
            try:
                from agent_framework.azure import AzureOpenAIChatClient

                return AzureOpenAIChatClient(
                    endpoint=endpoint,
                    deployment_name=deployment,
                    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
                    api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
                )
            except Exception as exc:  # pragma: no cover - configuration dependent
                _warn_once(f"Azure client unavailable ({exc}); falling back to the offline scripted client.")
        else:
            _warn_once("CHAOS_PROVIDER=azure but endpoint/deployment are unset; using the offline scripted client.")

    elif name == "openai":
        if os.getenv("OPENAI_API_KEY"):
            try:
                from agent_framework.openai import OpenAIChatClient

                return OpenAIChatClient(model_id=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
            except Exception as exc:  # pragma: no cover - configuration dependent
                _warn_once(f"OpenAI client unavailable ({exc}); falling back to the offline scripted client.")
        else:
            _warn_once("CHAOS_PROVIDER=openai but OPENAI_API_KEY is unset; using the offline scripted client.")

    return ScriptedChatClient(persona, tool_budget=tool_budget)
