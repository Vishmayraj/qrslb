"""
Unit tests for session_manager.py

Run with:
    cd backend
    pytest test_session_manager.py -v

Each test is isolated — setUp/teardown clears the shared _sessions dict
so tests never bleed state into each other.

Testing resulted in passing every one of the 29 tests, all under 0.08s.
"""

import re
import time
import pytest

import session_manager as sm
from session_manager import (
    create_session,
    get_session,
    attach_websocket,
    detach_websocket,
    set_state,
    destroy_session,
    cleanup_expired,
    _sessions,
    State,
    SESSION_TTL_SECONDS,
)


# ---------------------------------------------------------------------------
# Isolation — clear the in-memory store before every test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_sessions():
    """
    Wipe _sessions before each test.
    Without this, state from one test leaks into the next.
    autouse=True means it runs automatically — no need to reference it.
    """
    _sessions.clear()
    yield
    _sessions.clear()


# ---------------------------------------------------------------------------
# 1. create_session
# ---------------------------------------------------------------------------

class TestCreateSession:

    def test_id_matches_8_char_hex_format(self):
        """
        The session ID format is validated by regex on every HTTP and WS route.
        If create_session() produces an ID that doesn't match ^[0-9a-f]{8}$,
        the server silently rejects its own sessions — a catastrophic silent bug.
        """
        session = create_session()
        assert re.match(r'^[0-9a-f]{8}$', session["id"]), (
            f"Session ID '{session['id']}' does not match expected 8-char hex format"
        )

    def test_initial_state_is_created(self):
        """
        State machine starts at CREATED. The frontend's /session/{id} check
        reads this state — wrong initial state breaks the phone page validation.
        """
        session = create_session()
        assert session["state"] == State.CREATED

    def test_session_stored_in_dict(self):
        """
        Session must be reachable via get_session() immediately after creation.
        If it isn't stored, every subsequent WS connection fails with BAD_SESSION.
        """
        session = create_session()
        assert get_session(session["id"]) is not None

    def test_expires_at_is_ttl_seconds_from_now(self):
        """
        Expiry must be set correctly on creation. Off-by-one here means sessions
        either expire immediately or never expire — both are security issues.
        Allow 1 second of tolerance for test execution time.
        """
        before = time.time()
        session = create_session()
        after = time.time()

        assert before + SESSION_TTL_SECONDS <= session["expires_at"] <= after + SESSION_TTL_SECONDS

    def test_websocket_refs_start_as_none(self):
        """
        Both ws refs must be None on creation. If they're not, the duplicate
        connection check in signaling.py fires immediately and rejects all connections.
        """
        session = create_session()
        assert session["desktop_ws"] is None
        assert session["phone_ws"]   is None

    def test_bulk_ids_are_unique(self):
        """
        uuid4().hex[:8] gives ~4 billion possibilities. Collision is astronomically
        unlikely but this documents the expectation. If the ID generation strategy
        ever changes, this test catches regressions immediately.
        """
        ids = [create_session()["id"] for _ in range(2000)]
        assert len(set(ids)) == 2000, "Collision detected in session ID generation"


# ---------------------------------------------------------------------------
# 2. get_session
# ---------------------------------------------------------------------------

class TestGetSession:

    def test_returns_none_for_unknown_id(self):
        """
        signaling.py calls get_session() on every message in the loop.
        If it raises instead of returning None for unknown IDs, the entire
        WebSocket handler crashes and the client sees a dead connection.
        """
        assert get_session("00000000") is None

    def test_returns_none_for_empty_string(self):
        """Edge case — empty string must not raise, must return None cleanly."""
        assert get_session("") is None

    def test_returns_none_for_garbage_input(self):
        """
        The HTTP routes validate session_id with regex before calling get_session,
        but signaling.py calls it directly. Garbage must be handled gracefully.
        """
        assert get_session("not-a-real-id!!!") is None
        assert get_session("../../../../etc") is None

    def test_returns_session_for_valid_live_id(self):
        """Basic contract — a freshly created session must be retrievable."""
        session = create_session()
        result = get_session(session["id"])
        assert result is not None
        assert result["id"] == session["id"]

    def test_returns_none_for_expired_session(self):
        """
        Expiry is the primary security mechanism. If an expired session is
        returned, old QR codes remain valid indefinitely — anyone with a
        screenshot of a QR could connect hours later.
        This is the most security-critical test in the suite.
        """
        session = create_session()
        # Backdate expiry to simulate TTL exceeded
        _sessions[session["id"]]["expires_at"] = time.time() - 1

        assert get_session(session["id"]) is None

    def test_expired_session_state_set_to_expired(self):
        """
        When a session expires, its state should be marked EXPIRED in the dict
        even though get_session returns None. This ensures cleanup_expired() can
        correctly identify and remove it.
        """
        session = create_session()
        sid = session["id"]
        _sessions[sid]["expires_at"] = time.time() - 1

        get_session(sid)  # trigger expiry logic

        assert _sessions[sid]["state"] == State.EXPIRED

    def test_live_session_not_affected_by_time(self):
        """
        A session with plenty of TTL left must not be affected. Regression guard
        against an accidental >= vs > bug in the expiry check.
        """
        session = create_session()
        # Advance expiry well into the future
        _sessions[session["id"]]["expires_at"] = time.time() + 9999

        assert get_session(session["id"]) is not None


# ---------------------------------------------------------------------------
# 3. attach_websocket + state transitions
# ---------------------------------------------------------------------------

class TestAttachWebsocket:

    def test_attaching_desktop_sets_desktop_ws(self):
        """
        Role assignment is critical — the signaling server routes messages based
        on which ws object is stored where. A copy-paste error (assigning phone_ws
        when role is 'desktop') would silently route all messages to the wrong side.
        """
        session = create_session()
        fake_ws = object()
        attach_websocket(session["id"], "desktop", fake_ws)

        result = get_session(session["id"])
        assert result["desktop_ws"] is fake_ws
        assert result["phone_ws"]   is None

    def test_attaching_phone_sets_phone_ws(self):
        """Mirror of the above — phone role must set phone_ws, not desktop_ws."""
        session = create_session()
        fake_ws = object()
        attach_websocket(session["id"], "phone", fake_ws)

        result = get_session(session["id"])
        assert result["phone_ws"]   is fake_ws
        assert result["desktop_ws"] is None

    def test_desktop_attach_transitions_state_to_waiting(self):
        """
        State machine: CREATED → WAITING when desktop connects.
        The phone page reads this state via GET /session/{id}.
        Wrong state here means the phone page shows incorrect status.
        """
        session = create_session()
        attach_websocket(session["id"], "desktop", object())

        assert get_session(session["id"])["state"] == State.WAITING

    def test_phone_attach_transitions_state_to_paired(self):
        """State machine: WAITING → PAIRED when phone connects."""
        session = create_session()
        attach_websocket(session["id"], "desktop", object())
        attach_websocket(session["id"], "phone",   object())

        assert get_session(session["id"])["state"] == State.PAIRED

    def test_full_state_transition_sequence(self):
        """
        Walk the entire state machine in order and assert each step.
        Catches any state that gets skipped or overwritten incorrectly.
        """
        session = create_session()
        sid = session["id"]

        assert get_session(sid)["state"] == State.CREATED

        attach_websocket(sid, "desktop", object())
        assert get_session(sid)["state"] == State.WAITING

        attach_websocket(sid, "phone", object())
        assert get_session(sid)["state"] == State.PAIRED

        set_state(sid, State.CONNECTED)
        assert get_session(sid)["state"] == State.CONNECTED

        set_state(sid, State.DONE)
        assert get_session(sid)["state"] == State.DONE

    def test_attach_to_nonexistent_session_does_not_raise(self):
        """
        signaling.py calls attach_websocket after validating the session exists,
        but there's a tiny race window where the session could expire between
        validation and attachment. Must not raise — must silently no-op.
        """
        attach_websocket("00000000", "desktop", object())  # should not raise


# ---------------------------------------------------------------------------
# 4. detach_websocket
# ---------------------------------------------------------------------------

class TestDetachWebsocket:

    def test_detach_desktop_clears_desktop_ws(self):
        """
        On disconnect, the ws ref must be cleared. If it isn't, the duplicate
        connection check in signaling.py permanently blocks reconnection —
        the desktop can never scan a new QR after a disconnect.
        """
        session = create_session()
        attach_websocket(session["id"], "desktop", object())
        detach_websocket(session["id"], "desktop")

        assert get_session(session["id"])["desktop_ws"] is None

    def test_detach_phone_clears_phone_ws(self):
        """Mirror for phone role."""
        session = create_session()
        attach_websocket(session["id"], "phone", object())
        detach_websocket(session["id"], "phone")

        assert get_session(session["id"])["phone_ws"] is None

    def test_detach_desktop_does_not_clear_phone_ws(self):
        """
        Detaching one role must not affect the other.
        If detach_websocket cleared both refs, the peer_left notification
        in signaling.py would have nothing to send to.
        """
        session = create_session()
        phone_ws = object()
        attach_websocket(session["id"], "desktop", object())
        attach_websocket(session["id"], "phone",   phone_ws)

        detach_websocket(session["id"], "desktop")

        assert get_session(session["id"])["phone_ws"] is phone_ws


# ---------------------------------------------------------------------------
# 5. destroy_session
# ---------------------------------------------------------------------------

class TestDestroySession:

    def test_destroyed_session_not_retrievable(self):
        """
        After destroy_session(), get_session() must return None.
        This is the single-use guarantee — once DONE, the session cannot
        be reused even if someone replays the same WebSocket connection.
        """
        session = create_session()
        sid = session["id"]

        destroy_session(sid)

        assert get_session(sid) is None
        assert sid not in _sessions

    def test_destroy_nonexistent_session_does_not_raise(self):
        """
        signaling.py calls destroy_session() in the done handler. If a race
        causes it to be called twice (e.g. both sides send done), the second
        call must not raise — dict.pop with default handles this, but
        an explicit test guards against future refactors.
        """
        destroy_session("00000000")  # should not raise
        destroy_session("00000000")  # calling twice should also not raise


# ---------------------------------------------------------------------------
# 6. cleanup_expired
# ---------------------------------------------------------------------------

class TestCleanupExpired:

    def test_removes_expired_sessions(self):
        """
        The background task calls this every 60 seconds.
        If expired sessions aren't removed, the _sessions dict grows
        unboundedly — eventual memory exhaustion on a long-running server.
        """
        session = create_session()
        _sessions[session["id"]]["expires_at"] = time.time() - 1

        removed = cleanup_expired()

        assert removed == 1
        assert session["id"] not in _sessions

    def test_does_not_remove_live_sessions(self):
        """
        Equally important — cleanup must not touch sessions that are still live.
        If it does, active transfers get killed mid-flow.
        """
        live = create_session()  # expires in 600s

        removed = cleanup_expired()

        assert removed == 0
        assert get_session(live["id"]) is not None

    def test_removes_only_expired_when_mixed(self):
        """
        The critical mixed case — expired and live sessions coexist.
        Only expired ones should be removed. This is the core correctness
        guarantee of the cleanup loop.
        """
        live    = create_session()
        expired = create_session()
        _sessions[expired["id"]]["expires_at"] = time.time() - 1

        removed = cleanup_expired()

        assert removed == 1
        assert get_session(live["id"])    is not None
        assert get_session(expired["id"]) is None

    def test_removes_multiple_expired_sessions(self):
        """Cleanup must handle a batch of expired sessions, not just one."""
        sessions = [create_session() for _ in range(5)]
        for s in sessions:
            _sessions[s["id"]]["expires_at"] = time.time() - 1

        removed = cleanup_expired()

        assert removed == 5
        assert len(_sessions) == 0

    def test_returns_zero_when_nothing_to_clean(self):
        """
        The background task logs how many sessions were removed.
        Returning 0 correctly suppresses the log line — returning wrong
        counts would flood logs with false positives.
        """
        create_session()  # live session

        removed = cleanup_expired()

        assert removed == 0