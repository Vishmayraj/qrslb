# QRSLB — QR (Secure Link Bridge) WebRTC Link Launcher

**Scan a QR on your phone → link is automatically pasted and opened on the desktop.**  
A tiny, lightweight web app designed to solve one specific problem: sending links to foreign or temporary computers **securely, instantly, and without juggling apps**.

---

## 🚀 Overview

We’ve all been there:  

> “I just need to open Google Drive on a college PC, but I have to log into WhatsApp → send myself a link → copy it → open it → log out. Ugh.”  

QRSLB automates this workflow: **scan a QR code with your phone → paste the URL → the link opens automatically on the desktop**.  
No WhatsApp. No juggling. No security risks.

---

## 📝 Features

- **One-step link transfer:** scan QR → URL is automatically pasted/opened on the desktop.  
- **WebRTC P2P:** direct peer-to-peer connection whenever possible.  
- **TURN fallback:** automatically relays via TURN if devices are on different networks.  
- **Ephemeral sessions:** each QR/session expires after a configurable timeout.  
- **Lightweight & fast:** minimal overhead, low TURN usage (~1 KB per session).  
- **Cross-platform:** works on any browser with WebRTC support.  

---

## 🖥️ How It Works

1. Desktop opens QRSLB → generates a **unique session QR code**.  
2. Phone scans the QR → connects via **WebSocket to the server**.  
3. Phone sends the URL → the **link is automatically pasted and opened on the desktop**.  
4. Session ends automatically or after expiration.  

**WebRTC + WebSocket Signaling:**  

- WebSockets handle **signaling & session management**.  
- WebRTC handles **secure P2P channel**, falling back to TURN if needed.  

---

## 💡 Lessons Learned / Considerations

- **TURN servers are tiny:** 500 MB monthly is more than enough for tiny URL transfers.  
- **Security first:** always validate URLs to prevent XSS.  
- **Keepalive & WebSocket stability:** ping timeouts are normal; handled gracefully.  
- **Phone-initiated offer:** ensures timing issues don’t break the flow.  

---

## 🌐 Live Demo

Try it live: [https://qrslb.onrender.com](https://qrslb.onrender.com)  

---

## 💻 Contributing

- Issues & feature requests welcome.  
- Pull requests for improvements or experiments are appreciated.  

---

## 📜 License

MIT License — free to use, modify, and experiment.  
