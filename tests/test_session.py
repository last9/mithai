"""Tests for SessionManager."""

from mithai.core.session import SessionManager
from mithai.state.memory import MemoryStateBackend


def make_manager(max_turns=50):
    return SessionManager(MemoryStateBackend(), max_turns=max_turns)


def test_load_creates_empty_session():
    mgr = make_manager()
    session = mgr.load("slack:C123")

    assert session["session_id"] == "slack:C123"
    assert session["platform"] == "slack"
    assert session["channel_id"] == "C123"
    assert session["turns"] == []


def test_append_turn_persists():
    mgr = make_manager()
    key = "slack:C123"

    turn = SessionManager.build_turn(
        user_id="U1",
        user_message="check pods",
        tool_calls=[{"tool": "k8s__get_pods", "input": {}, "result_summary": "3 pods"}],
        assistant_response="Found 3 pods running.",
    )
    mgr.append_turn(key, turn)

    session = mgr.get_session(key)
    assert session is not None
    assert len(session["turns"]) == 1
    assert session["turns"][0]["user_message"] == "check pods"


def test_append_multiple_turns():
    mgr = make_manager()
    key = "cli:cli"

    for i in range(3):
        turn = SessionManager.build_turn(
            user_id="local",
            user_message=f"message {i}",
            tool_calls=[],
            assistant_response=f"response {i}",
        )
        mgr.append_turn(key, turn)

    session = mgr.get_session(key)
    assert len(session["turns"]) == 3
    assert session["turns"][2]["user_message"] == "message 2"


def test_trim_oldest_turns():
    mgr = make_manager(max_turns=3)
    key = "slack:C1"

    for i in range(5):
        turn = SessionManager.build_turn("U1", f"msg {i}", [], f"resp {i}")
        mgr.append_turn(key, turn)

    session = mgr.get_session(key)
    assert len(session["turns"]) == 3
    # Oldest two trimmed
    assert session["turns"][0]["user_message"] == "msg 2"


def test_list_sessions():
    mgr = make_manager()

    for ch in ["C1", "C2", "C3"]:
        key = f"slack:{ch}"
        turn = SessionManager.build_turn("U1", f"hello from {ch}", [], "hi")
        mgr.append_turn(key, turn)

    sessions = mgr.list_sessions()
    assert len(sessions) == 3
    assert all(s["turn_count"] == 1 for s in sessions)


def test_list_sessions_limit():
    mgr = make_manager()

    for i in range(5):
        key = f"slack:C{i}"
        turn = SessionManager.build_turn("U1", f"msg {i}", [], f"resp {i}")
        mgr.append_turn(key, turn)

    sessions = mgr.list_sessions(limit=2)
    assert len(sessions) == 2


def test_get_session_not_found():
    mgr = make_manager()
    assert mgr.get_session("nonexistent") is None


def test_search():
    mgr = make_manager()

    turn1 = SessionManager.build_turn("U1", "restart nginx", [], "restarted")
    mgr.append_turn("slack:C1", turn1)

    turn2 = SessionManager.build_turn("U1", "check disk", [], "disk is fine")
    mgr.append_turn("slack:C2", turn2)

    results = mgr.search("nginx")
    assert len(results) == 1
    assert results[0]["user_message"] == "restart nginx"


def test_search_case_insensitive():
    mgr = make_manager()

    turn = SessionManager.build_turn("U1", "Check NGINX status", [], "OK")
    mgr.append_turn("slack:C1", turn)

    results = mgr.search("nginx")
    assert len(results) == 1


def test_search_in_response():
    mgr = make_manager()

    turn = SessionManager.build_turn("U1", "what happened", [], "Pod nginx-abc crashed with OOMKilled")
    mgr.append_turn("slack:C1", turn)

    results = mgr.search("OOMKilled")
    assert len(results) == 1


def test_session_key():
    assert SessionManager.session_key("slack", "C123") == "slack:C123"
    assert SessionManager.session_key("cli", "cli") == "cli:cli"
