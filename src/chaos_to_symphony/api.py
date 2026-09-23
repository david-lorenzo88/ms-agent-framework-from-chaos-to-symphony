"""The showcase site's backend.

Serves the static site, the pattern catalogue, a live run feed over
server-sent events, and the approval endpoint the human-in-the-loop pattern
needs. It also resolves DevUI entity ids, so the site can point its embedded
DevUI at whichever pattern is selected.

    python -m chaos_to_symphony.api        # http://localhost:8000
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, runtime_config, telemetry
from .clients import effective, provider
from .introspect import agents_in
from .memory import STORE
from .registry import PATTERNS, TIERS, get
from .runner import RunSession, execute

load_dotenv()

logger = logging.getLogger(__name__)
def _find_web_dir() -> Path | None:
    """Locate the static site.

    The obvious ``__file__ / .. / .. / .. / web`` only holds for an editable
    install run from the repository root. Pip-installed - which is how the
    container runs - that resolves inside site-packages, the directory does not
    exist, and every static route silently fails to register: the app boots
    happily and answers "Not Found" at /.
    """
    candidates: list[Path] = []
    override = os.getenv("CHAOS_WEB_DIR")
    if override:
        candidates.append(Path(override))
    candidates += [
        Path(__file__).resolve().parent.parent.parent / "web",  # editable, from the repo
        Path("/app/web"),                                       # the container image
        Path.cwd() / "web",                                     # run from the repo root
    ]
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return None


WEB_DIR = _find_web_dir()
DEVUI_URL = os.getenv("DEVUI_URL", f"http://localhost:{os.getenv('DEVUI_PORT', '8080')}")

#: When both processes share one container - which is how this deploys - the
#: browser can only reach the public ingress. DevUI is then proxied through
#: this app instead of being exposed on its own port: the SPA at /devui/ and
#: its API at /v1/, which works because DevUI's frontend asks for both with
#: relative URLs and this app owns nothing under either path.
PROXY_DEVUI = os.getenv("CHAOS_PROXY_DEVUI", "0") == "1"
#: What the browser should use as the DevUI origin. Behind the proxy that is
#: this app's own origin plus /devui; locally it is DevUI's own port.
PUBLIC_DEVUI_URL = "/devui" if PROXY_DEVUI else DEVUI_URL

#: Live runs, keyed by run id. In memory, like everything else here.
SESSIONS: dict[str, RunSession] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
    traced = telemetry.install()
    print()
    print("  From Chaos to Symphony - Baltic Summit 2026")
    print(f"  Showcase: http://localhost:{os.getenv('SHOWCASE_PORT', '8000')}")
    print(f"  DevUI:    {DEVUI_URL}  (start it with: python -m chaos_to_symphony.devui_app)")
    status = effective()
    print(f"  Provider: {status['active']} via {status['client']}"
          + ("  (no keys, no network)" if not status["live"] else ""))
    if status.get("baseUrl"):
        print(f"  Endpoint: {status['baseUrl']}  (api_version={status['apiVersion'] or 'default'})")
    if status["note"]:
        print(f"  WARNING:  {status['note']}")
    print(f"  Traces:   {'OpenTelemetry capture on' if traced else 'unavailable'}")
    print(f"  Site:     {WEB_DIR if WEB_DIR else 'NOT FOUND - set CHAOS_WEB_DIR'}")
    print()
    yield
    for session in list(SESSIONS.values()):
        if session.task and not session.task.done():
            session.task.cancel()


app = FastAPI(title="From Chaos to Symphony", version=__version__, lifespan=lifespan)


class RunRequest(BaseModel):
    prompt: str | None = None


class ApprovalRequest(BaseModel):
    requestId: str
    decision: str = "approve"


class ConfigRequest(BaseModel):
    """What the settings panel sends. No keys: Foundry does not take one."""

    provider: str = "offline"
    foundryProjectEndpoint: str = ""
    foundryModel: str = ""


#: Set CHAOS_CONFIG_API=0 to make the settings panel read-only. Worth doing on
#: a public deploy: the site has no authentication, so anyone who finds the URL
#: can otherwise point the demo at a different Foundry project.
CONFIG_WRITABLE = os.getenv("CHAOS_CONFIG_API", "1") == "1"


# --------------------------------------------------------------------------
# Catalogue
# --------------------------------------------------------------------------


@app.get("/api/patterns")
async def patterns() -> dict[str, Any]:
    """Everything the site needs to render the catalogue."""
    status = effective()
    return {
        "version": __version__,
        # What is actually in use, so the UI cannot advertise a live model that
        # silently fell back to the scripted client.
        "provider": status["active"],
        "requestedProvider": status["requested"],
        "offline": not status["live"],
        "providerNote": status["note"],
        "devuiUrl": PUBLIC_DEVUI_URL,
        "tiers": [{"id": t[0], "title": t[1], "blurb": t[2]} for t in TIERS],
        "patterns": [spec.to_dict() for spec in PATTERNS],
    }


@app.get("/api/devui/entities")
async def devui_entities() -> dict[str, Any]:
    """Map each pattern's workflow name to the entity id DevUI minted for it.

    DevUI generates ids with a fresh uuid at start-up, so this cannot be
    hard-coded. If DevUI is not running the site degrades to showing its own
    panels - the showcase must not need DevUI to be useful.
    """
    mapping: dict[str, str] = {}
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{DEVUI_URL}/v1/entities")
            response.raise_for_status()
            payload = response.json()
        entities = payload.get("entities", payload) if isinstance(payload, dict) else payload
        by_name = {str(e.get("name", "")).lower(): e.get("id") for e in entities}
        for spec in PATTERNS:
            entity_id = by_name.get(spec.devui_name.lower())
            if entity_id:
                mapping[spec.slug] = entity_id
        return {"available": True, "devuiUrl": PUBLIC_DEVUI_URL, "entities": mapping}
    except Exception as exc:
        logger.info("DevUI not reachable at %s (%s)", DEVUI_URL, exc)
        return {"available": False, "devuiUrl": PUBLIC_DEVUI_URL, "entities": {}, "reason": str(exc)[:200]}


@app.get("/api/audit")
async def audit() -> dict[str, Any]:
    """The in-memory audit trail from the most recent run."""
    return {"rows": STORE.audit_dicts()}


def _provider_status() -> dict[str, Any]:
    """What the app would actually use right now, for the panel to show back."""
    status = effective()
    return {
        "provider": status["active"],
        "requestedProvider": status["requested"],
        "offline": not status["live"],
        "client": status["client"],
        "baseUrl": status.get("baseUrl", ""),
        "note": status["note"],
    }


@app.get("/api/config")
async def read_config() -> dict[str, Any]:
    """The provider settings, and whether they can be changed from here."""
    return {**runtime_config.snapshot(), "writable": CONFIG_WRITABLE, "status": _provider_status()}


@app.put("/api/config")
async def write_config(body: ConfigRequest) -> dict[str, Any]:
    """Point the demo at a provider without restarting it.

    Validates before saving, because the failure this replaces is precisely the
    silent one: set a provider with no endpoint and every agent quietly runs
    scripted while the badge says otherwise. The reply carries the *effective*
    status rather than an acknowledgement, so the panel can say whether the
    change actually took.
    """
    if not CONFIG_WRITABLE:
        raise HTTPException(status_code=403, detail="Settings are read-only here (CHAOS_CONFIG_API=0).")

    name = body.provider.strip().lower()
    if name not in runtime_config.SELECTABLE_PROVIDERS:
        raise HTTPException(
            status_code=422,
            detail=f"Provider must be one of: {', '.join(runtime_config.SELECTABLE_PROVIDERS)}.",
        )

    values = {"CHAOS_PROVIDER": name}
    if name == "foundry":
        endpoint = body.foundryProjectEndpoint.strip()
        model = body.foundryModel.strip()
        if not endpoint or not model:
            raise HTTPException(
                status_code=422,
                detail="Foundry needs both a project endpoint and a model deployment name.",
            )
        if not endpoint.startswith("https://"):
            raise HTTPException(
                status_code=422,
                detail="The project endpoint should start with https:// - copy it from the Foundry portal.",
            )
        values["FOUNDRY_PROJECT_ENDPOINT"] = endpoint
        values["FOUNDRY_MODEL"] = model

    persisted = runtime_config.save(values)
    return {
        **runtime_config.snapshot(),
        "writable": True,
        "persisted": persisted,
        "status": _provider_status(),
    }


@app.delete("/api/config")
async def reset_config() -> dict[str, Any]:
    """Forget the saved settings and use whatever the process was started with."""
    if not CONFIG_WRITABLE:
        raise HTTPException(status_code=403, detail="Settings are read-only here (CHAOS_CONFIG_API=0).")
    runtime_config.clear()
    return {**runtime_config.snapshot(), "writable": True, "status": _provider_status()}


#: Built agent configs, per slug. Reading them means building the workflow, and
#: a pattern's prompts and tools cannot change while the process is running -
#: so build once and answer every later visit from here.
_AGENTS: dict[str, list[dict[str, Any]]] = {}


@runtime_config.on_change
def _forget_cached_agents() -> None:
    """Agent cards name the client each agent drives, so they go stale too."""
    _AGENTS.clear()


@app.get("/api/agents/{slug}")
async def agents(slug: str) -> dict[str, Any]:
    """Every agent in one pattern: its prompt, its tools, the client it drives.

    Read back out of the built workflow rather than from anything maintained by
    hand, so what the audience sees on screen is what the agent was actually
    given - including the handoff tools, which only exist after the builder has
    run.
    """
    try:
        spec = get(slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None

    if slug not in _AGENTS:
        try:
            _AGENTS[slug] = agents_in(spec.build())
        except Exception as exc:
            # A pattern that cannot be built is a problem for its own run, not
            # a reason for this panel to 500.
            logger.exception("Could not introspect the agents in %s", slug)
            raise HTTPException(status_code=503, detail=f"Could not build {slug}: {exc}") from None

    return {"slug": slug, "pattern": spec.name, "agents": _AGENTS[slug]}


@app.get("/api/store")
async def store_summary() -> dict[str, Any]:
    """A read-only peek at the in-memory 'database', for the data panel."""
    return {
        "customers": len(STORE.customers),
        "shipments": len(STORE.shipments),
        "openExceptions": len(STORE.open_exceptions()),
        "tariffLines": len(STORE.tariffs),
        "rows": [
            {
                "id": s.id,
                "lane": s.lane,
                "goods": s.goods,
                "exception": s.exception.value if s.exception else "-",
                "severity": s.severity.value,
                "valueEur": s.declared_value_eur,
            }
            for s in sorted(STORE.shipments.values(), key=lambda s: s.id)
        ],
    }


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------


@app.post("/api/run/{slug}")
async def start_run(slug: str, body: RunRequest) -> dict[str, str]:
    """Kick off one pattern. Returns a run id to stream from."""
    try:
        spec = get(slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None

    run_id = uuid.uuid4().hex[:12]
    session = RunSession(run_id=run_id, spec=spec, prompt=(body.prompt or spec.default_prompt).strip())
    SESSIONS[run_id] = session
    session.task = asyncio.create_task(execute(session))

    # Keep only the last handful of runs; this is a demo, not a log server.
    for old in list(SESSIONS)[:-8]:
        finished = SESSIONS[old]
        if finished.done:
            SESSIONS.pop(old, None)
    return {"runId": run_id, "pattern": slug, "prompt": session.prompt}


@app.get("/api/stream/{run_id}")
async def stream(run_id: str) -> StreamingResponse:
    """Server-sent events for one run: logs, node activations, output, pauses."""
    session = SESSIONS.get(run_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown run")

    async def events():
        yield f"data: {json.dumps({'kind': 'connected', 'runId': run_id})}\n\n"
        while True:
            try:
                frame = await asyncio.wait_for(session.queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                if session.done and session.queue.empty():
                    break
                yield ": keep-alive\n\n"
                continue
            yield f"data: {json.dumps(frame)}\n\n"
            if frame.get("kind") == "end":
                # Closing here shows up in devtools as an aborted request. That
                # is simply how EventSource ends - there is no graceful close
                # handshake, so whichever side hangs up first looks like the
                # failure. Delaying it only moves the abort to the client and
                # adds latency, so hang up promptly.
                break

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.post("/api/approve/{run_id}")
async def approve(run_id: str, body: ApprovalRequest) -> dict[str, Any]:
    """Answer a suspended human-in-the-loop gate."""
    session = SESSIONS.get(run_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown run")
    accepted = session.answer(body.requestId, body.decision)
    if not accepted:
        raise HTTPException(status_code=409, detail="No pending approval with that id")
    return {"ok": True, "decision": body.decision}


@app.get("/api/health")
async def health() -> dict[str, Any]:
    """Liveness for the platform. Deliberately not /health - that path is
    proxied to DevUI - and deliberately independent of DevUI, so a DevUI
    problem cannot make the platform restart a working showcase."""
    return {
        "status": "ok",
        "version": __version__,
        "provider": effective()["active"],
        "requestedProvider": provider(),
        "patterns": len(PATTERNS),
        "proxyDevui": PROXY_DEVUI,
    }


@app.get("/api/traces/{run_id}")
async def traces(run_id: str) -> dict[str, Any]:
    """The OpenTelemetry spans for one run, for the Traces tab."""
    session = SESSIONS.get(run_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown run")
    return {"runId": run_id, "pattern": session.spec.slug, "spans": session.traces}


@app.post("/api/reset")
async def reset() -> dict[str, str]:
    """Reset the in-memory store between demos."""
    STORE.reset()
    return {"status": "reset"}


# --------------------------------------------------------------------------
# DevUI reverse proxy (single-origin deployments)
# --------------------------------------------------------------------------

_PROXY_HOP_BY_HOP = {
    "connection", "keep-alive", "transfer-encoding", "upgrade",
    "proxy-authenticate", "proxy-authorization", "te", "trailers",
    "content-encoding", "content-length",
}


async def _proxy(request: Request, target: str) -> StreamingResponse:
    """Stream a request through to DevUI and stream the answer back.

    Streaming rather than buffering is not optional here: DevUI's /v1/responses
    is server-sent events, and a buffering proxy would hold the whole run and
    deliver it in one lump after it finished.
    """
    client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0))
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _PROXY_HOP_BY_HOP | {"host"}}
    upstream = client.build_request(
        request.method, target, headers=headers, content=request.stream(),
        params=dict(request.query_params),
    )
    response = await client.send(upstream, stream=True)

    async def body():
        try:
            async for chunk in response.aiter_raw():
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    return StreamingResponse(
        body(),
        status_code=response.status_code,
        headers={k: v for k, v in response.headers.items() if k.lower() not in _PROXY_HOP_BY_HOP},
        media_type=response.headers.get("content-type"),
    )


if PROXY_DEVUI:
    _METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]

    @app.api_route("/devui", methods=_METHODS, include_in_schema=False)
    @app.api_route("/devui/{path:path}", methods=_METHODS, include_in_schema=False)
    async def proxy_devui_app(request: Request, path: str = "") -> StreamingResponse:
        """The DevUI single-page app. Its assets are requested relatively, so
        serving it from this subpath works without rebuilding the frontend."""
        return await _proxy(request, f"{DEVUI_URL}/{path}")

    @app.api_route("/v1/{path:path}", methods=_METHODS, include_in_schema=False)
    async def proxy_devui_api(request: Request, path: str) -> StreamingResponse:
        """DevUI's API. The SPA asks for this at the origin root, so it has to
        live here rather than under /devui."""
        return await _proxy(request, f"{DEVUI_URL}/v1/{path}")

    # The SPA also probes these two at the root. Without them it renders, then
    # reports "Can't Connect to Backend" - which looks like DevUI is down when
    # in fact only the proxy was incomplete.
    @app.api_route("/health", methods=_METHODS, include_in_schema=False)
    async def proxy_devui_health(request: Request) -> StreamingResponse:
        return await _proxy(request, f"{DEVUI_URL}/health")

    @app.api_route("/meta", methods=_METHODS, include_in_schema=False)
    async def proxy_devui_meta(request: Request) -> StreamingResponse:
        return await _proxy(request, f"{DEVUI_URL}/meta")


# --------------------------------------------------------------------------
# Static site
# --------------------------------------------------------------------------

if WEB_DIR is not None:
    app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/{filename}")
    async def static_file(filename: str) -> FileResponse:
        candidate = (WEB_DIR / filename).resolve()
        if candidate.is_file() and WEB_DIR.resolve() in candidate.parents:
            return FileResponse(candidate)
        raise HTTPException(status_code=404, detail="Not found")

else:
    # Fail loudly. A bare 404 at / sent the last deploy on a detour; say what
    # is actually wrong and where it looked.
    logger.error("Static site not found. Set CHAOS_WEB_DIR to the directory holding index.html.")

    @app.get("/")
    async def missing_site() -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={
                "error": "The static site was not found, so the UI is not being served.",
                "fix": "Set CHAOS_WEB_DIR to the directory containing index.html.",
                "looked_in": [
                    os.getenv("CHAOS_WEB_DIR") or "(CHAOS_WEB_DIR unset)",
                    str(Path(__file__).resolve().parent.parent.parent / "web"),
                    "/app/web",
                    str(Path.cwd() / "web"),
                ],
                "api": "The JSON API under /api is unaffected and still works.",
            },
        )


def main() -> None:
    """Entry point for ``chaos-web``."""
    import uvicorn

    uvicorn.run(
        "chaos_to_symphony.api:app",
        # Loopback locally; a container has to bind every interface for the
        # platform's ingress to reach it.
        host=os.getenv("CHAOS_HOST", "127.0.0.1"),
        port=int(os.getenv("SHOWCASE_PORT", "8000")),
        log_level="info",
    )


if __name__ == "__main__":
    main()
