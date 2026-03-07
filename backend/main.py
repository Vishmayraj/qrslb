import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, WebSocket, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from backend import session_manager as sm
from backend.signaling import handle_websocket


# ---------------------------------------------------------------------------
# Background task — cleans expired sessions every 60 seconds
# ---------------------------------------------------------------------------

async def _cleanup_loop():
    while True:
        await asyncio.sleep(60)
        removed = sm.cleanup_expired()
        if removed:
            print(f"[cleanup] removed {removed} expired session(s)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_cleanup_loop())
    yield
    task.cancel()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="QR Link Bridge",
    description="Send links from phone to desktop via QR + WebRTC",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Serve frontend files
# ---------------------------------------------------------------------------

BASE_DIR     = Path(__file__).parent.parent
DESKTOP_HTML = BASE_DIR / "frontend" / "desktop" / "index.html"
PHONE_HTML   = BASE_DIR / "frontend" / "phone"   / "index.html"


@app.get("/", response_class=HTMLResponse)
async def serve_desktop():
    if not DESKTOP_HTML.exists():
        raise HTTPException(status_code=404, detail="Desktop frontend not found")
    return HTMLResponse(content=DESKTOP_HTML.read_text())


@app.get("/phone", response_class=HTMLResponse)
async def serve_phone():
    if not PHONE_HTML.exists():
        raise HTTPException(status_code=404, detail="Phone frontend not found")
    return HTMLResponse(content=PHONE_HTML.read_text())


# ---------------------------------------------------------------------------
# ICE config — fetches live TURN credentials from Metered API
# API key stays in env var, never in HTML
# ---------------------------------------------------------------------------

FALLBACK_ICE = {
    "iceServers": [
        {"urls": "stun:stun.l.google.com:19302"},
        {"urls": "stun:stun1.l.google.com:19302"},
    ]
}

@app.get("/ice-config")
async def ice_config():
    """
    Fetches fresh TURN credentials from Metered.
    Falls back to STUN-only if API key is not configured or request fails.
    """
    api_key = os.getenv("METERED_API_KEY")

    if not api_key:
        return JSONResponse(FALLBACK_ICE)

    metered_domain = os.getenv("METERED_DOMAIN", "qrslb.metered.live")

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"https://{metered_domain}/api/v1/turn/credentials",
                params={"apiKey": api_key},
            )
            resp.raise_for_status()
            ice_servers = resp.json()
            return JSONResponse({"iceServers": ice_servers})

    except Exception as e:
        print(f"[ice-config] Metered fetch failed: {e}, falling back to STUN")
        return JSONResponse(FALLBACK_ICE)


# ---------------------------------------------------------------------------
# REST — Session management
# ---------------------------------------------------------------------------

@app.post("/session")
async def create_session():
    session = sm.create_session()
    return JSONResponse({
        "session_id": session["id"],
        "expires_in": sm.SESSION_TTL_SECONDS,
        "qr_payload": f"/phone?session={session['id']}",
    })


@app.get("/session/{session_id}")
async def check_session(session_id: str):
    session = sm.get_session(session_id)
    if session is None:
        return JSONResponse({"valid": False})
    return JSONResponse({"valid": True, "state": session["state"]})


# ---------------------------------------------------------------------------
# WebSocket — Signaling
# ---------------------------------------------------------------------------

@app.websocket("/ws/{session_id}/{role}")
async def websocket_endpoint(ws: WebSocket, session_id: str, role: str):
    await handle_websocket(ws, session_id, role)


# ---------------------------------------------------------------------------
# Health check — Render pings this
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "sessions_active": len(sm._sessions)}