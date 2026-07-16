"""FastAPI service for the stock research assistant.

Run: uvicorn stock_screening.api:app --reload
"""

import asyncio
import json
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from stock_screening.agents.base import AgentDeps
from stock_screening.agents.main_agent import route_query_stream
from stock_screening.logging_config import get_logger, setup_logging
from stock_screening.models.constants import CONVERSATION_CONTEXT_MESSAGES
from stock_screening.models.types import AgentType

setup_logging(level="INFO")
logger = get_logger(__name__)

app = FastAPI(title="Stock Research API")

_INDEX_HTML = Path(__file__).parent / "static" / "index.html"
_PROJECT_ROOT = Path(__file__).parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
_INGEST_SCRIPT = _SCRIPTS_DIR / "ingest.py"

# session_id -> (deps, history). In-memory: sessions are lost on restart.
_sessions: dict[str, tuple[AgentDeps, list[dict]]] = {}


def _get_or_create_session(session_id: str | None) -> tuple[str, AgentDeps, list[dict]]:
    if session_id and session_id in _sessions:
        deps, history = _sessions[session_id]
        return session_id, deps, history
    sid = session_id or str(uuid.uuid4())
    _sessions[sid] = (AgentDeps(), [])
    return (sid, *_sessions[sid])


def _to_json(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "value"):
        return obj.value
    return str(obj)


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=_to_json)}\n\n"


@app.get("/")
async def index() -> HTMLResponse:
    return HTMLResponse(_INDEX_HTML.read_text())


@app.post("/chat/stream")
async def chat(request: Request) -> StreamingResponse:
    body = await request.json()
    message = (body.get("message") or "").strip()

    if not message:
        async def _empty():
            yield _sse({"type": "error", "message": "Empty message"})
        return StreamingResponse(_empty(), media_type="text/event-stream")

    force_agent = body.get("force_agent")
    if force_agent not in (AgentType.SCREENING, AgentType.WEB_SEARCH):
        force_agent = None

    sid, deps, history = _get_or_create_session(body.get("session_id"))
    context = history[-CONVERSATION_CONTEXT_MESSAGES:]

    async def generate():
        yield _sse({"type": "session", "session_id": sid})
        try:
            reply = ""
            async for event in route_query_stream(
                deps, message, conversation_context=context, force_agent=force_agent
            ):
                yield _sse(event)
                if event["type"] == "final":
                    reply = getattr(event.get("response"), "message", "") or ""
            history.append({"role": "user", "content": message})
            if reply:
                history.append({"role": "assistant", "content": reply})
        except Exception as e:
            logger.exception("Stream error")
            yield _sse({"type": "error", "message": str(e)})
        finally:
            yield _sse({"type": "done"})

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/session/clear")
async def clear_session(request: Request) -> dict:
    body = await request.json()
    _sessions.pop(body.get("session_id"), None)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Data refresh — runs scripts/ingest.py as a subprocess, streams its log lines.
# Single global job: one refresh at a time, shared by all clients.
# ---------------------------------------------------------------------------

_MODE_FLAGS: dict[str, list[str]] = {
    "incremental": [],
    "quick": ["--quick"],
    "full": ["--full"],
    "sync": ["--sync-universe"],
    "rebuild": ["--rebuild"],
}


@dataclass
class _RefreshJob:
    running: bool = False
    done: bool = False
    ok: bool = False
    lines: list[str] = field(default_factory=list)


_job = _RefreshJob()


async def _run_refresh(flags: list[str], mode: str) -> None:
    _job.lines.append(f"Starting {mode} refresh…")
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(_INGEST_SCRIPT), *flags,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(_SCRIPTS_DIR),
        )
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            if line:
                _job.lines.append(line)
        await proc.wait()
        _job.ok = proc.returncode == 0
        _job.lines.append("--- done ---" if _job.ok else f"--- exited with code {proc.returncode} ---")
    except Exception as e:
        logger.exception("Refresh error")
        _job.lines.append(f"Error: {e}")
        _job.ok = False
    finally:
        _job.running = False
        _job.done = True


@app.post("/data/refresh")
async def data_refresh(request: Request) -> JSONResponse:
    body = await request.json()
    if _job.running:
        return JSONResponse({"started": False, "reason": "already_running"})

    mode = body.get("mode", "sync")
    _job.running, _job.done, _job.ok, _job.lines = True, False, False, []
    asyncio.create_task(_run_refresh(_MODE_FLAGS.get(mode, _MODE_FLAGS["sync"]), mode))
    return JSONResponse({"started": True})


@app.get("/data/refresh/status")
async def refresh_status(from_line: int = 0) -> JSONResponse:
    return JSONResponse({
        "running": _job.running,
        "done": _job.done,
        "ok": _job.ok,
        "lines": _job.lines[from_line:],
        "total_lines": len(_job.lines),
    })
