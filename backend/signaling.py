import json
from fastapi import WebSocket, WebSocketDisconnect
from backend import session_manager as sm


# ---------------------------------------------------------------------------
# Allowed message types  (anything else is silently dropped)
# ---------------------------------------------------------------------------

RELAY_TYPES = {"offer", "answer", "ice", "ready", "done"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _send(ws: WebSocket, payload: dict) -> None:
    """Send JSON to a single WebSocket. Silently ignores if ws is None."""
    if ws is None:
        return
    try:
        await ws.send_text(json.dumps(payload))
    except Exception:
        pass  # client already disconnected


async def _send_error(ws: WebSocket, code: str, message: str) -> None:
    await _send(ws, {"type": "error", "code": code, "message": message})


# ---------------------------------------------------------------------------
# Main handler  — one coroutine per WebSocket connection
# ---------------------------------------------------------------------------

async def handle_websocket(ws: WebSocket, session_id: str, role: str) -> None:
    """
    Entry point called by FastAPI route:
        WS /ws/{session_id}/{role}
    role must be "desktop" or "phone".
    """

    # -- 1. Validate role
    if role not in ("desktop", "phone"):
        await ws.accept()
        await _send_error(ws, "BAD_ROLE", "role must be desktop or phone")
        await ws.close()
        return

    # -- 2. Validate session
    session = sm.get_session(session_id)
    if session is None:
        await ws.accept()
        await _send_error(ws, "BAD_SESSION", "session not found or expired")
        await ws.close()
        return

    # -- 3. Prevent duplicate connections for same role
    if session[f"{role}_ws"] is not None:
        await ws.accept()
        await _send_error(ws, "ALREADY_CONNECTED", f"{role} already connected")
        await ws.close()
        return

    # -- 4. Accept + register
    await ws.accept()
    sm.attach_websocket(session_id, role, ws)
    await _send(ws, {"type": "connected", "role": role, "session_id": session_id})

    # -- 5. Message loop
    #
    # We do NOT push peer_joined on connect.
    # Both sides send {"type":"ready"} once their JS ws.onmessage is live.
    # Only then do we fire peer_joined at phone — no race condition.
    #
    try:
        while True:
            raw = await ws.receive_text()

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await _send_error(ws, "BAD_JSON", "message must be valid JSON")
                continue

            msg_type = msg.get("type")

            if msg_type not in RELAY_TYPES:
                await _send_error(ws, "UNKNOWN_TYPE", f"unknown type: {msg_type}")
                continue

            session = sm.get_session(session_id)
            if session is None:
                await _send_error(ws, "SESSION_EXPIRED", "session expired")
                break

            desktop_ws = session["desktop_ws"]
            phone_ws   = session["phone_ws"]

            if msg_type == "ready":
                # If both sides present, trigger phone to start WebRTC offer.
                # This can fire from either side sending ready — whoever is last.
                if desktop_ws and phone_ws:
                    await _send(phone_ws, {"type": "peer_joined"})
                else:
                    await _send(ws, {"type": "ack_ready"})

            elif msg_type == "offer":
                # Phone -> Desktop
                await _send(desktop_ws, {"type": "offer", "sdp": msg.get("sdp")})

            elif msg_type == "answer":
                # Desktop -> Phone
                await _send(phone_ws, {"type": "answer", "sdp": msg.get("sdp")})

            elif msg_type == "ice":
                # Relay to the OTHER side
                if role == "desktop":
                    await _send(phone_ws,   {"type": "ice", "candidate": msg.get("candidate")})
                else:
                    await _send(desktop_ws, {"type": "ice", "candidate": msg.get("candidate")})

            elif msg_type == "done":
                sm.set_state(session_id, sm.State.DONE)
                await _send(desktop_ws, {"type": "done"})
                await _send(phone_ws,   {"type": "done"})
                sm.destroy_session(session_id)
                break

    except WebSocketDisconnect:
        pass

    finally:
        sm.detach_websocket(session_id, role)
        session = sm.get_session(session_id)
        if session:
            other_ws = session["phone_ws"] if role == "desktop" else session["desktop_ws"]
            await _send(other_ws, {"type": "peer_left", "role": role})