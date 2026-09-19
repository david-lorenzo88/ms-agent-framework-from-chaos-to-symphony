"""Make one real call with the configured provider and say what happened.

Twelve patterns is a slow and expensive way to discover that an endpoint is
wrong. This makes a single minimal request and, when the failure is the one
everybody hits - "API version not supported" - walks the known Azure API
versions and reports which of them your resource accepts.

    python scripts/check_provider.py

Reads the same environment as the app, so a .env file works.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from chaos_to_symphony.clients import effective, provider  # noqa: E402

#: "preview" first: left unpinned the framework targets Azure OpenAI's v1
#: surface (base_url .../openai/v1/, api_version "preview") rather than the
#: classic dated path, and on a current resource the dated ones are exactly
#: what gets rejected. The dated versions follow for older resources.
CANDIDATES = (
    "preview",
    "2025-04-01-preview",
    "2025-03-01-preview",
    "2025-01-01-preview",
    "2024-12-01-preview",
    "2024-10-21",
    "2024-08-01-preview",
    "2024-06-01",
)


async def try_call(client) -> tuple[bool, str]:
    """One tiny request. Returns (ok, detail)."""
    from agent_framework import Agent

    agent = Agent(client=client, name="probe", instructions="Reply with the single word: ok")
    try:
        response = await asyncio.wait_for(agent.run("ping"), timeout=45)
        return True, (response.text or "").strip()[:60]
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"[:400]


async def sweep_versions(endpoint: str, deployment: str, key: str | None) -> None:
    """Find which api_version this resource accepts."""
    from agent_framework.openai import OpenAIChatClient

    print("\nTrying api_version candidates (newest first):\n")
    working = []
    for version in CANDIDATES:
        options = {"model": deployment, "azure_endpoint": endpoint, "api_version": version}
        if key:
            options["api_key"] = key
        try:
            ok, detail = await try_call(OpenAIChatClient(**options))
        except Exception as exc:
            ok, detail = False, f"{type(exc).__name__}: {exc}"[:120]
        if ok:
            working.append(version)
            print(f"  WORKS   {version}   -> {detail!r}")
        else:
            short = "API version not supported" if "API version not supported" in detail else detail[:90]
            print(f"  no      {version}   {short}")

    print()
    if working:
        print(f"Set this and re-run:\n\n  AZURE_OPENAI_API_VERSION={working[0]}\n")
    else:
        print("None of them worked, so the api_version is not the problem.")
        print("Check the endpoint and deployment name:")
        print("  - endpoint looks like https://<resource>.openai.azure.com/")
        print("  - deployment is the DEPLOYMENT name, not the model name")


async def main() -> int:
    status = effective()
    print(f"CHAOS_PROVIDER : {provider()}")
    print(f"Active client  : {status['client']}  (live={status['live']})")
    if status["note"]:
        print(f"Note           : {status['note']}")

    if not status["live"]:
        print("\nRunning offline, so there is nothing to check against a real service.")
        return 0

    from chaos_to_symphony.clients import chat_client

    print("\nMaking one request...")
    ok, detail = await try_call(chat_client("probe"))
    if ok:
        print(f"  OK -> {detail!r}")
        print()
        print("The provider works. It is using:")
        print(f"  api_version : {status['apiVersion'] or '(framework default)'}")
        print(f"  endpoint    : {status['baseUrl'] or '(unknown)'}")
        if os.getenv("AZURE_OPENAI_API_VERSION"):
            print("\n  (pinned by AZURE_OPENAI_API_VERSION)")
        else:
            print("\n  Nothing is pinned - this is the framework's own default, which is")
            print("  what you want unless a resource specifically rejects it. To freeze it:")
            print(f"    AZURE_OPENAI_API_VERSION={status['apiVersion']}")
        print("\nRun: make demo")
        return 0

    print(f"  FAILED: {detail}")

    if "API version not supported" in detail and provider() == "azure":
        await sweep_versions(
            os.environ["AZURE_OPENAI_ENDPOINT"],
            os.environ["AZURE_OPENAI_DEPLOYMENT"],
            os.getenv("AZURE_OPENAI_API_KEY"),
        )
    elif "DeploymentNotFound" in detail or "404" in detail:
        print("\nThat usually means AZURE_OPENAI_DEPLOYMENT is the model name rather")
        print("than the deployment name. Use the left-hand column in the portal.")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
