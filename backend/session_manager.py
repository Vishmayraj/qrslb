import uuid
import time
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SESSION_TTL_SECONDS = 600  # 10 minutes


# ---------------------------------------------------------------------------
# Session states
# ---------------------------------------------------------------------------

class State:
    CREATED   = "created"    # POST /session called, QR not yet shown
    WAITING   = "waiting"    # Desktop WebSocket connected, showing QR
    PAIRED    = "paired"     # Phone scanned and joined WebSocket
    CONNECTED = "connected"  # WebRTC DataChannel open
    DONE      = "done"       # Link received, session complete
    EXPIRED   = "expired"    # TTL exceeded


# ---------------------------------------------------------------------------
# In-memory store  { session_id: session_dict }
# ---------------------------------------------------------------------------

_sessions: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_session() -> dict:
    """
    Create a new session and return it.
    Called by POST /session.
    """
    session_id = uuid.uuid4().hex[:8]          # e.g. "a3f9c1b2"
    session = {
        "id":         session_id,
        "state":      State.CREATED,
        "created_at": time.time(),
        "expires_at": time.time() + SESSION_TTL_SECONDS,
        "desktop_ws": None,                    # WebSocket object (set later)
        "phone_ws":   None,                    # WebSocket object (set later)
    }
    _sessions[session_id] = session
    return session


def get_session(session_id: str) -> Optional[dict]:
    """
    Return session if it exists and has not expired.
    Returns None if missing or expired.
    """
    session = _sessions.get(session_id)
    if session is None:
        return None

    if time.time() > session["expires_at"]:
        session["state"] = State.EXPIRED
        return None

    return session


def set_state(session_id: str, state: str) -> None:
    """
    Advance the session to a new state.
    Uses get_session() so expired sessions are never mutated.
    """
    session = get_session(session_id)
    if session:
        session["state"] = state


def attach_websocket(session_id: str, role: str, ws) -> None:
    """
    Attach a WebSocket connection to a session.
    role must be "desktop" or "phone".
    """
    session = _sessions.get(session_id)
    if session is None:
        return

    if role == "desktop":
        session["desktop_ws"] = ws
        session["state"] = State.WAITING
    elif role == "phone":
        session["phone_ws"] = ws
        session["state"] = State.PAIRED


def detach_websocket(session_id: str, role: str) -> None:
    """Remove WebSocket reference when a client disconnects."""
    session = _sessions.get(session_id)
    if session is None:
        return

    if role == "desktop":
        session["desktop_ws"] = None
    elif role == "phone":
        session["phone_ws"] = None


def destroy_session(session_id: str) -> None:
    """Permanently delete a session (called after DONE or on expiry cleanup)."""
    _sessions.pop(session_id, None)


def cleanup_expired() -> int:
    """
    Remove all expired sessions from memory.
    Call this periodically (e.g. every 60s via a background task).
    Returns the number of sessions removed.
    """
    now = time.time()
    expired = [sid for sid, s in _sessions.items() if now > s["expires_at"]]
    for sid in expired:
        del _sessions[sid]
    return len(expired)