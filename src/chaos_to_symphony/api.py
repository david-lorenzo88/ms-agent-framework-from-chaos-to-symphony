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
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .clients import is_offline, provider
from .memory import STORE
from .registry import PATTERNS, TIERS, get
from .runner import RunSession, execute

load_dotenv()

logger = logging.getLogger(__name__)
WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web"
DEVUI_URL = os.getenv("DEVUI_URL", f"http://localhost:{os.getenv('DEVUI_PORT', '8080')}")

#: Live runs, keyed by run id. In memory, like everything else here.
SESSIONS: dict[str, RunSession] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
    print()
    print("  From Chaos to Symphony - Baltic Summit 2026")
    print(f"  Showcase: http://localhost:{os.getenv('SHOWCASE_PORT', '8000')}")
    print(f"  DevUI:    {DEVUI_URL}  (start it with: python -m chaos_to_symphony.devui_app)")
    print(f"  Provider: {provider()}" + ("  (offline - no keys, no network)" if is_offline() else ""))
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


# --------------------------------------------------------------------------
# Catalogue
# --------------------------------------------------------------------------


@app.get("/api/patterns")
async def patterns() -> dict[str, Any]:
    """Everything the site needs to render the catalogue."""
    return {
        "version": __version__,
        "provider": provider(),
        "offline": is_offline(),
        "devuiUrl": DEVUI_URL,
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
        return {"available": True, "devuiUrl": DEVUI_URL, "entities": mapping}
    except Exception as exc:
        logger.info("DevUI not reachable at %s (%s)", DEVUI_URL, exc)
        return {"available": False, "devuiUrl": DEVUI_URL, "entities": {}, "reason": str(exc)[:200]}


@app.get("/api/audit")
async def audit() -> dict[str, Any]:
    """The in-memory audit trail from the most recent run."""
    return {"rows": STORE.audit_dicts()}


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


@app.post("/api/reset")
async def reset() -> dict[str, str]:
    """Reset the in-memory store between demos."""
    STORE.reset()
    return {"status": "reset"}


# --------------------------------------------------------------------------
# Static site
# --------------------------------------------------------------------------

if WEB_DIR.is_dir():
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


def main() -> None:
    """Entry point for ``chaos-web``."""
    import uvicorn

    uvicorn.run(
        "chaos_to_symphony.api:app",
        host="127.0.0.1",
        port=int(os.getenv("SHOWCASE_PORT", "8000")),
        log_level="info",
    )


if __name__ == "__main__":
    main()
