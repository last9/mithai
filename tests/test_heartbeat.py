"""Tests for HeartbeatScheduler."""

import threading
import time
from unittest.mock import MagicMock, patch

from mithai.core.heartbeat import HeartbeatScheduler, _HeartbeatAdapter
from mithai.adapters.base import IncomingMessage


# ---------------------------------------------------------------------------
# _HeartbeatAdapter
# ---------------------------------------------------------------------------

def test_heartbeat_adapter_approves_memory_tools():
    adapter = _HeartbeatAdapter()
    req = MagicMock()
    req.tool_name = "memory__write"
    assert adapter.request_human_approval(req, "C1") is True


def test_heartbeat_adapter_denies_non_memory_tools():
    adapter = _HeartbeatAdapter()
    req = MagicMock()
    req.tool_name = "shell__run"
    assert adapter.request_human_approval(req, "C1") is False


def test_heartbeat_adapter_denies_slack_tools():
    adapter = _HeartbeatAdapter()
    req = MagicMock()
    req.tool_name = "slack__get_history"
    assert adapter.request_human_approval(req, "C1") is False


# ---------------------------------------------------------------------------
# HeartbeatScheduler._tick()
# ---------------------------------------------------------------------------

def _make_scheduler(instructions=None):
    engine = MagicMock()
    memory = MagicMock()
    memory.read.return_value = instructions
    scheduler = HeartbeatScheduler(engine, memory, interval=9999)
    return scheduler, engine, memory


def test_tick_skips_when_heartbeat_md_absent():
    scheduler, engine, memory = _make_scheduler(instructions=None)
    scheduler._tick()
    engine.handle.assert_not_called()


def test_tick_skips_when_heartbeat_md_empty():
    scheduler, engine, memory = _make_scheduler(instructions="   \n  ")
    scheduler._tick()
    engine.handle.assert_not_called()


def test_tick_calls_engine_handle_with_instructions():
    instructions = "Check deploy status and update memory."
    scheduler, engine, memory = _make_scheduler(instructions=instructions)
    scheduler._tick()

    engine.handle.assert_called_once()
    call_args = engine.handle.call_args
    message: IncomingMessage = call_args[0][0]
    assert message.text == instructions.strip()
    assert message.platform == "system"
    assert message.channel_id == "heartbeat"
    assert message.thread_id == "heartbeat"
    assert message.user_id == "system"


def test_tick_passes_heartbeat_adapter():
    scheduler, engine, memory = _make_scheduler(instructions="do something")
    scheduler._tick()
    adapter = engine.handle.call_args[0][1]
    assert isinstance(adapter, _HeartbeatAdapter)


def test_tick_reads_heartbeat_file_each_call():
    scheduler, engine, memory = _make_scheduler(instructions="step one")
    scheduler._tick()
    scheduler._tick()
    assert memory.read.call_count == 2


def test_tick_engine_exception_does_not_propagate():
    scheduler, engine, memory = _make_scheduler(instructions="do it")
    engine.handle.side_effect = RuntimeError("boom")
    # Should not raise
    scheduler._tick()


# ---------------------------------------------------------------------------
# HeartbeatScheduler start / stop
# ---------------------------------------------------------------------------

def test_start_creates_daemon_thread():
    scheduler, _, _ = _make_scheduler()
    scheduler.start()
    assert scheduler._thread is not None
    assert scheduler._thread.daemon is True
    scheduler.stop()


def test_stop_signals_thread():
    scheduler, _, _ = _make_scheduler()
    scheduler.start()
    assert not scheduler._stop_event.is_set()
    scheduler.stop()
    assert scheduler._stop_event.is_set()


def test_start_idempotent():
    """Calling start() twice doesn't spawn a second thread."""
    scheduler, _, _ = _make_scheduler()
    scheduler.start()
    first_thread = scheduler._thread
    scheduler.start()
    assert scheduler._thread is first_thread
    scheduler.stop()


# ---------------------------------------------------------------------------
# _start_heartbeat() helper in run_cmd
# ---------------------------------------------------------------------------

def test_start_heartbeat_returns_none_when_disabled():
    from mithai.cli.run_cmd import _start_heartbeat
    engine = MagicMock()
    engine._memory = MagicMock()
    assert _start_heartbeat({"heartbeat": {"enabled": False}}, engine) is None


def test_start_heartbeat_returns_none_when_no_config():
    from mithai.cli.run_cmd import _start_heartbeat
    engine = MagicMock()
    engine._memory = MagicMock()
    assert _start_heartbeat({}, engine) is None


def test_start_heartbeat_returns_none_when_no_memory():
    from mithai.cli.run_cmd import _start_heartbeat
    engine = MagicMock()
    engine._memory = None
    assert _start_heartbeat({"heartbeat": {"enabled": True}}, engine) is None


def test_start_heartbeat_starts_and_returns_scheduler():
    from mithai.cli.run_cmd import _start_heartbeat
    engine = MagicMock()
    engine._memory = MagicMock()
    config = {"heartbeat": {"enabled": True, "interval": 60}}
    scheduler = _start_heartbeat(config, engine)
    try:
        assert scheduler is not None
        assert scheduler._interval == 60
        assert scheduler._thread is not None
        assert scheduler._thread.is_alive()
    finally:
        scheduler.stop()


def test_start_heartbeat_default_interval():
    from mithai.cli.run_cmd import _start_heartbeat
    engine = MagicMock()
    engine._memory = MagicMock()
    config = {"heartbeat": {"enabled": True}}
    scheduler = _start_heartbeat(config, engine)
    try:
        assert scheduler._interval == 3600
    finally:
        scheduler.stop()
