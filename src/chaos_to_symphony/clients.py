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


@lru_cache(maxsize=1)
def effective() -> dict[str, Any]:
    """What the app will *actually* use, not what was asked for.

    Reporting the configured provider is not the same thing: select ``azure``
    in an image built without the provider extra and every agent quietly runs
    scripted while the badge claims a live model. Constructing a client costs
    nothing here - no provider is contacted until a request is made - so the
    honest answer is simply to build one and look at what came back.
    """
    requested = provider()
    try:
        client = chat_client("probe")
        name = type(client).__name__
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "requested": requested, "active": "offline", "live": False,
            "note": f"Could not construct a client ({type(exc).__name__}); running offline.",
        }

    live = name != "ScriptedChatClient"
    note = ""
    if requested != "offline" and not live:
        note = (
            f"'{requested}' was requested but is not available, so the offline client is in use. "
            "Install the provider extra and set its endpoint/key."
        )
    return {
        "requested": requested,
        "active": requested if live else "offline",
        "live": live,
        "client": name,
        "note": note,
    }


def chat_client(persona: str = "agent", *, tool_budget: int = 1) -> Any:
    """Return a chat client for one agent persona.

    Falls back to the offline client - loudly - if a real provider is selected
    but not configured or not installed. A half-configured provider should
    degrade to a working demo, not a stack trace in front of an audience.

    There is no ``AzureOpenAIChatClient`` in Agent Framework: Azure OpenAI is
    reached by giving ``OpenAIChatClient`` an ``azure_endpoint``, and the
    Foundry Agent Service has its own client in a separate package.
    """
    name = provider()

    if name == "azure":
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        if endpoint and deployment:
            try:
                from agent_framework.openai import OpenAIChatClient

                return OpenAIChatClient(
                    model=deployment,
                    azure_endpoint=endpoint,
                    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
                    api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
                )
            except ImportError:
                _warn_once("agent-framework-openai is not installed; using the offline client. pip install '.[openai]'")
            except Exception as exc:  # pragma: no cover - configuration dependent
                _warn_once(f"Azure client unavailable ({exc}); falling back to the offline scripted client.")
        else:
            _warn_once("CHAOS_PROVIDER=azure but endpoint/deployment are unset; using the offline scripted client.")

    elif name == "foundry":
        endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT")
        model = os.getenv("FOUNDRY_MODEL")
        if endpoint and model:
            try:
                from agent_framework.foundry import FoundryChatClient
                from azure.identity import DefaultAzureCredential

                return FoundryChatClient(
                    project_endpoint=endpoint, model=model, credential=DefaultAzureCredential()
                )
            except ImportError:
                _warn_once(
                    "agent-framework-foundry is not installed; using the offline client. "
                    "pip install '.[foundry]'"
                )
            except Exception as exc:  # pragma: no cover - configuration dependent
                _warn_once(f"Foundry client unavailable ({exc}); falling back to the offline scripted client.")
        else:
            _warn_once("CHAOS_PROVIDER=foundry but endpoint/model are unset; using the offline scripted client.")

    elif name == "openai":
        if os.getenv("OPENAI_API_KEY"):
            try:
                from agent_framework.openai import OpenAIChatClient

                return OpenAIChatClient(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
            except ImportError:
                _warn_once("agent-framework-openai is not installed; using the offline client. pip install '.[openai]'")
            except Exception as exc:  # pragma: no cover - configuration dependent
                _warn_once(f"OpenAI client unavailable ({exc}); falling back to the offline scripted client.")
        else:
            _warn_once("CHAOS_PROVIDER=openai but OPENAI_API_KEY is unset; using the offline scripted client.")

    return ScriptedChatClient(persona, tool_budget=tool_budget)
