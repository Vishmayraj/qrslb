import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import session_manager as sm
from signaling import handle_websocket


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
    # Start cleanup loop when app boots
    task = asyncio.create_task(_cleanup_loop())
    yield
    # Cancel cleanup loop when app shuts down
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

BASE_DIR = Path(__file__).parent.parent  # project root

# Serve desktop page at  /
# Serve phone page at    /phone/

DESKTOP_HTML = BASE_DIR / "frontend" / "desktop" / "index.html"
PHONE_HTML   = BASE_DIR / "frontend" / "phone"   / "index.html"


@app.get("/", response_class=HTMLResponse)
async def serve_desktop():
    """Desktop page — shows QR code."""
    if not DESKTOP_HTML.exists():
        raise HTTPException(status_code=404, detail="Desktop frontend not found")
    return HTMLResponse(content=DESKTOP_HTML.read_text())


@app.get("/phone", response_class=HTMLResponse)
async def serve_phone():
    """
    Phone page — paste and send link.
    This URL is what the QR code points to (with session_id as query param).
    e.g. https://yourapp.onrender.com/phone?session=abc12345
    """
    if not PHONE_HTML.exists():
        raise HTTPException(status_code=404, detail="Phone frontend not found")
    return HTMLResponse(content=PHONE_HTML.read_text())


# ---------------------------------------------------------------------------
# REST  —  Session management
# ---------------------------------------------------------------------------

@app.post("/session")
async def create_session():
    """
    Desktop calls this on page load to get a fresh session.

    Response:
        {
            "session_id": "a3f9c1b2",
            "expires_in": 600,
            "connect_url": "https://yourapp.onrender.com/phone?session=a3f9c1b2"
        }
    """
    session = sm.create_session()
    return JSONResponse({
        "session_id": session["id"],
        "expires_in": sm.SESSION_TTL_SECONDS,
        # Frontend will embed this URL into the QR code
        "qr_payload": f"/phone?session={session['id']}",
    })


@app.get("/session/{session_id}")
async def check_session(session_id: str):
    """
    Phone calls this immediately after QR scan to verify session is still valid.

    Response (valid):
        { "valid": true, "state": "waiting" }

    Response (invalid / expired):
        { "valid": false }
    """
    session = sm.get_session(session_id)
    if session is None:
        return JSONResponse({"valid": False})
    return JSONResponse({"valid": True, "state": session["state"]})


# ---------------------------------------------------------------------------
# WebSocket  —  Signaling
# ---------------------------------------------------------------------------

@app.websocket("/ws/{session_id}/{role}")
async def websocket_endpoint(ws: WebSocket, session_id: str, role: str):
    """
    Both desktop and phone connect here.

    Desktop: /ws/{session_id}/desktop
    Phone:   /ws/{session_id}/phone

    All signaling logic lives in signaling.py — this just hands off.
    """
    await handle_websocket(ws, session_id, role)


# ---------------------------------------------------------------------------
# Health check  —  Render uses this to confirm app is alive
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "sessions_active": len(sm._sessions)}