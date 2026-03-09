# QRSLB — QR Secure Link Bridge

**Scan a QR on your phone → link opens instantly on the desktop.**  
A browser-native P2P link transfer tool built for one sharp problem: sending links to foreign or temporary computers without logging into anything.

🌐 **Live:** [qrslb.onrender.com](https://qrslb.onrender.com) &nbsp;|&nbsp; No install. No login. No app.

---

## The Problem

> "I need to open a Google Drive link on a college PC. So I log into WhatsApp Web, send myself the link, copy it, open it, then log out. Every single time."

QRSLB eliminates this entirely.

```
Scan QR  →  Paste link on phone  →  Desktop opens it
```

---

## System Architecture

```
┌─────────────────────────────────────────────┐
│              RENDER SERVER                  │
│                                             │
│  FastAPI                                    │
│  ├── POST /session   → create session       │
│  ├── GET  /session/{id} → validate          │
│  ├── GET  /ice-config   → TURN credentials  │
│  └── WS   /ws/{id}/{role} → signaling       │
│                                             │
│  In-memory session store + cleanup loop     │
└──────────────┬──────────────────────────────┘
               │  WebSocket (signaling only)
      ┌────────┴────────┐
      │                 │
 ┌────▼─────┐     ┌─────▼────┐
 │ DESKTOP  │     │  PHONE   │
 │ browser  │◄────┤ browser  │
 │ shows QR │     │sends link│
 └──────────┘     └──────────┘
   WebRTC DataChannel (direct P2P — server never sees the link)
```

### Signaling Protocol

The server only relays these message types — it never inspects content:

```
desktop → server : { type: "ready" }
phone   → server : { type: "ready" }
         ↓ server fires peer_joined to phone once both sides are ready
phone   → desktop: { type: "offer",  sdp: ... }
desktop → phone  : { type: "answer", sdp: ... }
both    ↔ both   : { type: "ice",    candidate: ... }

── WebRTC DataChannel open ──

phone   → desktop: { type: "link", url: "https://..." }   ← direct, server blind
desktop → phone  : { type: "ack" }
desktop → server : { type: "done" }  → session destroyed
```

### Why phone initiates the WebRTC offer

The desktop sends `ready` when its WebSocket message loop is live. The phone does the same. The server fires `peer_joined` only when **both** sides have confirmed readiness — then phone creates the offer. This eliminates the race condition where `peer_joined` arrives before the recipient's `onmessage` handler is registered.

### ICE candidate buffering

ICE candidates can arrive before `setRemoteDescription()` completes. Both clients buffer early candidates and flush them immediately after the remote description is set — a standard WebRTC production pattern that prevents silent connection failures on high-latency networks.

---

## Session Lifecycle

```
CREATED → WAITING → PAIRED → CONNECTED → DONE
                                       ↘ EXPIRED (10 min TTL)
```

Sessions are single-use. Once `done` is received the session is destroyed server-side. Expired sessions are cleaned up by a background task every 60 seconds.

---

## Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Backend | FastAPI + uvicorn | Async-native, WebSocket support, minimal overhead |
| Signaling transport | WebSockets | Persistent, bidirectional, low-latency |
| P2P channel | WebRTC DataChannel | Direct browser-to-browser, server never sees payload |
| STUN | Google public STUN | Free, reliable, zero config |
| TURN | Metered.ca | Dynamic credential API, 500 MB free tier (more than enough for URL-only transfers — a single session uses ~1 KB) |
| Hosting | Render free tier | Zero cost, auto-deploy from GitHub |
| Analytics | Umami Cloud | Privacy-friendly, no cookies, GDPR compliant |
| Session store | In-memory Python dict | See tradeoffs below |

---

## Security

- **Session ID validation** — regex `^[0-9a-f]{8}$` on every route. Path traversal and injection rejected before touching state.
- **URL scheme validation** — only `http://` and `https://` accepted. Blocks `javascript:`, `data:`, `file:` XSS vectors.
- **Single-use sessions** — destroyed immediately after transfer completes.
- **CORS lockdown** — requests only accepted from the app's own origin (`ALLOWED_ORIGIN` env var).
- **API docs disabled** — `/docs`, `/redoc`, `/openapi.json` all return 404 in production.
- **Credentials in env vars** — `METERED_API_KEY` never appears in frontend HTML. Frontend fetches a `/ice-config` endpoint which proxies credentials server-side.
- **WebRTC payload opacity** — the signaling server relays `offer`, `answer`, and `ice` messages opaquely. The actual link travels over the DataChannel directly between browsers.

---

## Known Tradeoffs & Scaling Notes

### In-memory session store
Sessions live in a Python dict on the server process. This is intentional for the current scale — it keeps the deployment zero-dependency and free. The tradeoff: sessions don't survive a server restart, and horizontal scaling across multiple instances would require a shared store (Redis with pub/sub for WebSocket coordination). For a single Render instance serving personal/small-group usage this is a non-issue.

### Render free tier cold starts
The server sleeps after 15 minutes of inactivity. UptimeRobot pings `/health` every 5 minutes to keep it warm. First cold-start load takes ~30 seconds — acceptable for a zero-cost deployment.

### STUN-only fallback
If `METERED_API_KEY` is not set, the app falls back to Google STUN only. This works for same-network usage but will fail for devices on different NATs (e.g. phone on mobile data, desktop on campus WiFi). TURN is required for reliable cross-network operation.

### TURN bandwidth
URL-only transfers use negligible TURN bandwidth (~1 KB per session). The Metered free tier (500 MB/month) is effectively unlimited for this use case. Upgrading to the 20 GB free tier is available if usage scales.

---

## Running Locally

```bash
git clone https://github.com/Vishmayraj/qrslb
cd qrslb

pip install -r requirements.txt
cd backend
uvicorn main:app --reload --port 8000
```

Open `http://localhost:8000` on desktop. For phone testing use your local IP (`http://192.168.x.x:8000`) — WebRTC requires either localhost or HTTPS.

### Environment variables

| Variable | Required | Description |
|---|---|---|
| `METERED_API_KEY` | No | TURN credentials from metered.ca. Falls back to STUN-only if unset. |
| `METERED_DOMAIN` | No | Your Metered app domain. Default: `qrslb.metered.live` |
| `ALLOWED_ORIGIN` | No | CORS origin. Default: `*` (lock down in production) |

---

## Deploying to Render

The repo includes `render.yaml`. Connect the repo to Render, add `METERED_API_KEY` in the Environment tab, and deploy. Everything else is configured automatically.

---

## Future Enhancements

- [ ] Redis session store + horizontal scaling support
- [ ] Auto-retry with exponential backoff on WebSocket disconnect
- [ ] Multiple links per session (batch transfer)
- [ ] QR auto-refresh on session expiry without page reload
- [ ] Optional end-to-end encryption of the DataChannel payload

---

## License

MIT — free to use, modify, and experiment.