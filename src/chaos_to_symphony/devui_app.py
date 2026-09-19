"""Launch DevUI with all twelve pattern workflows registered.

DevUI is the Agent Framework's own inspector: it renders each workflow's graph,
streams the agent-by-agent interaction, and shows the raw events underneath.
That is the whole reason it is on stage - the audience sees the framework's
view of the run, not a view this repo invented.

    python -m chaos_to_symphony.devui_app        # http://localhost:8080

Auth is disabled by default here so the showcase site can embed DevUI in an
iframe. That is a local-demo decision; DevUI ships auth-enabled and is a sample
app, not a production surface. Set CHAOS_DEVUI_AUTH=1 to put the token back.
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

from .clients import is_offline, provider
from .registry import PATTERNS

load_dotenv()

logger = logging.getLogger(__name__)


def entities() -> list[object]:
    """Build every pattern's workflow once, for in-memory registration."""
    built: list[object] = []
    for spec in PATTERNS:
        try:
            built.append(spec.build())
        except Exception:
            # One broken pattern must not stop the other eleven appearing on
            # stage. Log it and carry on.
            logger.exception("Could not build pattern %s; skipping it in DevUI", spec.slug)
    return built


def main() -> None:
    """Entry point for ``chaos-devui``."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s")

    from agent_framework.devui import serve

    port = int(os.getenv("DEVUI_PORT", "8080"))
    showcase_port = os.getenv("SHOWCASE_PORT", "8000")
    auth = os.getenv("CHAOS_DEVUI_AUTH", "0") == "1"

    workflows = entities()
    print()
    print("  From Chaos to Symphony - Baltic Summit 2026")
    print(f"  {len(workflows)} pattern workflows registered with DevUI")
    print(f"  Provider: {provider()}" + ("  (offline - no keys, no network)" if is_offline() else ""))
    print(f"  DevUI:    http://localhost:{port}")
    print(f"  Showcase: http://localhost:{showcase_port}")
    print()

    serve(
        entities=workflows,
        port=port,
        host="127.0.0.1",
        auto_open=False,
        # The showcase site is a different origin, and it both embeds DevUI and
        # reads /v1/entities to resolve each pattern's entity id.
        cors_origins=[
            f"http://localhost:{showcase_port}",
            f"http://127.0.0.1:{showcase_port}",
        ],
        ui_enabled=True,
        instrumentation_enabled=os.getenv("CHAOS_TELEMETRY", "1") == "1",
        auth_enabled=auth,
    )


if __name__ == "__main__":
    main()
