import json
import time
from fastapi import WebSocket, WebSocketDisconnect
import session_manager as sm


# ---------------------------------------------------------------------------
# Allowed message types  (anything else is silently dropped)
# ---------------------------------------------------------------------------

RELAY_TYPES = {"offer", "answer", "ice", "ready", "done"}


# ---------------------------------------------------------------------------
# Logging helper — single format for all Render logs
# ---------------------------------------------------------------------------

def _log(session_id: str, event: str, detail: str = "") -> None:
    """
    Structured log line visible in Render's log dashboard.
    Format:  [qrlb] session=a3f9c1b2  event=...  detail=...
    """
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    line = f"[qrlb] {ts} UTC  session={session_id}  event={event}"
    if detail:
        line += f"  {detail}"
    print(line, flush=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _send(ws: WebSocket, payload: dict) -> None:
    if ws is None:
        return
    try:
        await ws.send_text(json.dumps(payload))
    except Exception:
        pass


async def _send_error(ws: WebSocket, code: str, message: str) -> None:
    await _send(ws, {"type": "error", "code": code, "message": message})


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

async def handle_websocket(ws: WebSocket, session_id: str, role: str) -> None:

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

    _log(session_id, f"{role}_connected")

    # -- 5. Message loop
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
                if desktop_ws and phone_ws:
                    _log(session_id, "both_connected", "starting WebRTC negotiation")
                    await _send(phone_ws, {"type": "peer_joined"})
                else:
                    await _send(ws, {"type": "ack_ready"})

            elif msg_type == "offer":
                _log(session_id, "webrtc_offer_relayed")
                await _send(desktop_ws, {"type": "offer", "sdp": msg.get("sdp")})

            elif msg_type == "answer":
                _log(session_id, "webrtc_answer_relayed")
                await _send(phone_ws, {"type": "answer", "sdp": msg.get("sdp")})

            elif msg_type == "ice":
                # ICE is high-frequency — don't log every candidate
                if role == "desktop":
                    await _send(phone_ws,   {"type": "ice", "candidate": msg.get("candidate")})
                else:
                    await _send(desktop_ws, {"type": "ice", "candidate": msg.get("candidate")})

            elif msg_type == "done":
                # ── THE IMPORTANT LOG ──────────────────────────────────────
                # Desktop sends "done" after successfully opening the link.
                # This is the confirmation that the full flow completed.
                _log(session_id, "LINK_DELIVERED",
                     "phone->desktop transfer complete. session closing.")
                # ──────────────────────────────────────────────────────────
                sm.set_state(session_id, sm.State.DONE)
                await _send(desktop_ws, {"type": "done"})
                await _send(phone_ws,   {"type": "done"})
                sm.destroy_session(session_id)
                break

    except WebSocketDisconnect:
        _log(session_id, f"{role}_disconnected", "WebSocket closed")

    finally:
        sm.detach_websocket(session_id, role)
        session = sm.get_session(session_id)
        if session:
            other_ws = session["phone_ws"] if role == "desktop" else session["desktop_ws"]
            await _send(other_ws, {"type": "peer_left", "role": role})