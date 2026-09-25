"""Chat-client factory.

One switch, three providers. ``CHAOS_PROVIDER=offline`` is the default because
a session demo has to work when the venue wifi does not; ``azure`` and
``openai`` swap in a real model without any pattern code changing, which is the
point worth making from the stage - the orchestration layer does not know or
care which client it is driving.
"""

from __future__ import annotations

import base64
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

from .scripted import ScriptedChatClient


@dataclass(frozen=True)
class VisitorFoundry:
    """A Foundry project a visitor brought with them, for their own runs only.

    The public site must not hand its own identity to strangers, so a visitor
    who wants a live model supplies all three: their project, their deployment,
    and an Entra access token for it. The token is what makes this safe to
    offer - it is theirs, it expires within the hour, and it only ever reaches
    their own project. None of it is stored on the server or echoed back.
    """

    endpoint: str
    model: str
    token: str


#: Set for the duration of one visitor's run. A context variable rather than a
#: global so two visitors never see each other's settings: asyncio copies the
#: context into the task a run starts, and every agent is built inside it.
_visitor: ContextVar[VisitorFoundry | None] = ContextVar("chaos_visitor_foundry", default=None)

#: Foundry project endpoints live here. Anything else is refused, so the form
#: cannot be used to make the server call an arbitrary URL.
FOUNDRY_HOST_SUFFIX = ".services.ai.azure.com"


def check_visitor_endpoint(endpoint: str) -> str:
    """Return an error to show the visitor, or "" if the endpoint looks right."""
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not (parsed.hostname or "").endswith(FOUNDRY_HOST_SUFFIX):
        return (
            "The project endpoint should look like "
            f"https://<resource>{FOUNDRY_HOST_SUFFIX}/api/projects/<project> - copy it from the Foundry portal."
        )
    if not parsed.path.startswith("/api/projects/"):
        return "The project endpoint should end in /api/projects/<project> - copy it from the Foundry portal."
    return ""


@contextmanager
def visitor_foundry(settings: VisitorFoundry | None) -> Iterator[None]:
    """Use a visitor's Foundry project for whatever is started inside the block."""
    token = _visitor.set(settings)
    try:
        yield
    finally:
        _visitor.reset(token)


class _PastedToken:
    """An access token the visitor pasted, presented as an azure-core credential.

    Deliberately cannot refresh: when it expires the run fails with a 401, and
    the visitor fetches another. Refreshing would mean holding something
    longer-lived than a one-hour token, which is exactly what this avoids.
    """

    def __init__(self, token: str) -> None:
        self._token = token
        self._expires_on = _jwt_expiry(token)

    def get_token(self, *scopes: str, **kwargs: Any) -> Any:
        from azure.core.credentials import AccessToken

        return AccessToken(self._token, self._expires_on)

    def get_token_info(self, *scopes: str, options: Any = None) -> Any:
        from azure.core.credentials import AccessTokenInfo

        return AccessTokenInfo(self._token, self._expires_on)


def _jwt_expiry(token: str) -> int:
    """The token's own expiry, read without verifying it - Foundry does that.

    Falls back to "now plus a few minutes" for anything unreadable, which only
    decides when azure-core would ask for a new token; Foundry still rejects an
    invalid one.
    """
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return int(claims["exp"])
    except Exception:
        return int(time.time()) + 300


def provider() -> str:
    """The provider name for the current run, lowercased."""
    if _visitor.get() is not None:
        return "foundry"
    return (os.getenv("CHAOS_PROVIDER", "offline") or "offline").strip().lower()


def redact(text: str) -> str:
    """Remove the host's own Foundry endpoint from a message bound for a browser.

    A failing run reports its exception on screen, and an SDK error can quote
    the URL it was calling. Visitors' own endpoints are left alone: those are
    theirs, and seeing them is how they debug a typo.
    """
    endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT", "").strip()
    host = urlparse(endpoint).hostname if endpoint else None
    for secret in filter(None, (endpoint, host)):
        text = text.replace(secret, "<host endpoint>")
    return text


def is_offline() -> bool:
    return provider() == "offline"


@lru_cache(maxsize=1)
def _warn_once(message: str) -> None:
    import logging

    logging.getLogger(__name__).warning(message)


def describe_endpoint(client: Any) -> dict[str, str]:
    """The API version and base URL a live client will actually use.

    Worth surfacing rather than assuming: left to itself the framework targets
    Azure OpenAI's v1 surface - base_url ending /openai/v1/ with api_version
    "preview" - not the classic dated ?api-version=YYYY-MM-DD path. Passing a
    dated version is what produces "API version not supported" against a
    current resource.
    """
    inner = getattr(client, "client", None) or getattr(client, "async_client", None)
    version = getattr(client, "api_version", None) or getattr(inner, "_api_version", None)
    base_url = getattr(inner, "base_url", None)
    return {
        "apiVersion": str(version) if version else "",
        "baseUrl": str(base_url) if base_url else "",
    }


def effective() -> dict[str, Any]:
    """What the current run will use: the visitor's project if they gave one."""
    if _visitor.get() is not None:
        return _effective()
    return _effective_for_host()


@lru_cache(maxsize=1)
def _effective_for_host() -> dict[str, Any]:
    """The site's own answer, cached: the environment cannot change under it."""
    return _effective()


def _effective() -> dict[str, Any]:
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
    endpoint = describe_endpoint(client) if live else {"apiVersion": "", "baseUrl": ""}
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
        "apiVersion": endpoint["apiVersion"],
        "baseUrl": endpoint["baseUrl"],
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

                # Pass only what was actually configured. Pinning an api_version
                # here overrides the framework's own default, and a hard-coded
                # one goes stale: "2024-10-21" is rejected outright by newer
                # resources with "API version not supported". Set
                # AZURE_OPENAI_API_VERSION to pin it deliberately.
                options: dict[str, Any] = {"model": deployment, "azure_endpoint": endpoint}
                if os.getenv("AZURE_OPENAI_API_KEY"):
                    options["api_key"] = os.getenv("AZURE_OPENAI_API_KEY")
                if os.getenv("AZURE_OPENAI_API_VERSION"):
                    options["api_version"] = os.getenv("AZURE_OPENAI_API_VERSION")
                return OpenAIChatClient(**options)
            except ImportError:
                _warn_once("agent-framework-openai is not installed; using the offline client. pip install '.[openai]'")
            except Exception as exc:  # pragma: no cover - configuration dependent
                _warn_once(f"Azure client unavailable ({exc}); falling back to the offline scripted client.")
        else:
            _warn_once("CHAOS_PROVIDER=azure but endpoint/deployment are unset; using the offline scripted client.")

    elif name == "foundry":
        visitor = _visitor.get()
        if visitor is not None:
            # Never falls back: a visitor who asked for their own model and got
            # the scripted one instead would be told nothing true by the run.
            from agent_framework.foundry import FoundryChatClient

            return FoundryChatClient(
                project_endpoint=visitor.endpoint, model=visitor.model, credential=_PastedToken(visitor.token)
            )

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
