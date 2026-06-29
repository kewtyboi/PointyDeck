"""Tests for issue #975 performance quick-wins.

Covers:
  - ensure_conductor_running_with_status: returns (bool, status_str) so callers
    avoid a duplicate get_session_status subprocess on the happy path.
  - _get_drain_event / _drain_queue: event-driven wake replaces fixed 5s poll.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import types
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    import toml  # noqa: F401
except ModuleNotFoundError:
    sys.modules["toml"] = types.SimpleNamespace(load=lambda *_args, **_kwargs: {})

import bridge  # noqa: E402
from bridge import (  # noqa: E402
    _get_drain_event,
    ensure_conductor_running_with_status,
)


# ---------------------------------------------------------------------------
# Helpers (mirror test_issue1351_conductor_dedupe.py style)
# ---------------------------------------------------------------------------

async def _no_sleep(_seconds: float) -> None:
    return None


def _completed(returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["agent-deck"], returncode, "", stderr)


def _run(coro):
    with mock.patch("bridge.asyncio.sleep", new=_no_sleep):
        return asyncio.run(coro)


# ---------------------------------------------------------------------------
# ensure_conductor_running_with_status tests
# ---------------------------------------------------------------------------

class TestEnsureConductorRunningWithStatus:
    def test_running_happy_path_returns_status_no_cli(self):
        """On the happy path the function returns (True, status) without touching run_cli."""
        for status in ("waiting", "idle", "running", "active", "starting"):
            with mock.patch(
                "bridge.get_session_status",
                return_value=status,
            ), mock.patch("bridge.run_cli") as mock_cli:
                ok, returned_status = _run(
                    ensure_conductor_running_with_status("ops", "work")
                )

            assert ok is True, f"expected ok=True for status={status}"
            assert returned_status == status, (
                f"expected status={status!r}, got {returned_status!r}"
            )
            mock_cli.assert_not_called()

    def test_busy_status_reports_was_busy_correctly(self):
        """Caller can derive was_busy from returned status without a second subprocess."""
        busy_statuses = ("running", "active", "starting")
        for status in busy_statuses:
            with mock.patch("bridge.get_session_status", return_value=status):
                ok, returned_status = _run(
                    ensure_conductor_running_with_status("ops", "work")
                )
            was_busy = returned_status in ("running", "active", "starting")
            assert was_busy is True, f"expected was_busy=True for status={status}"

    def test_non_running_status_delegates_to_ensure_conductor_running(self):
        """When conductor is not running, falls back to ensure_conductor_running for start-up."""
        # Status sequence: unknown (initial check) -> unknown (ensure_conductor_running's
        # internal check) -> running (ensure_conductor_running's final check) -> running
        # (our re-fetch after startup).
        with mock.patch(
            "bridge.get_session_status",
            side_effect=["unknown", "unknown", "running", "running"],
        ), mock.patch(
            "bridge.get_sessions_list",
            return_value=[{"title": "conductor-ops", "profile": "work", "id": "x"}],
        ), mock.patch(
            "bridge.run_cli",
            side_effect=[_completed(1, "not found"), _completed(0)],
        ):
            ok, status = _run(ensure_conductor_running_with_status("ops", "work"))

        assert ok is True
        assert status == "running"

    def test_startup_failure_returns_false_and_error_status(self):
        """When start-up fails, returns (False, 'error')."""
        # Status sequence: unknown (initial) -> unknown (internal) -> unknown (final).
        with mock.patch(
            "bridge.get_session_status",
            side_effect=["unknown", "unknown", "unknown"],
        ), mock.patch(
            "bridge.get_sessions_list",
            return_value=[{"title": "conductor-ops", "profile": "work", "id": "x"}],
        ), mock.patch(
            "bridge.run_cli",
            side_effect=[_completed(1, "fail"), _completed(1, "still fail")],
        ):
            ok, status = _run(ensure_conductor_running_with_status("ops", "work"))

        assert ok is False
        assert status == "error"


# ---------------------------------------------------------------------------
# _get_drain_event / event-driven drain tests
# ---------------------------------------------------------------------------

class TestGetDrainEvent:
    def setup_method(self):
        # Reset the global drain event before each test.
        bridge._drain_event = None

    def test_returns_event_inside_running_loop(self):
        async def _inner():
            ev = _get_drain_event()
            assert ev is not None
            assert isinstance(ev, asyncio.Event)

        asyncio.run(_inner())

    def test_returns_same_instance_on_repeated_calls(self):
        async def _inner():
            ev1 = _get_drain_event()
            ev2 = _get_drain_event()
            assert ev1 is ev2

        asyncio.run(_inner())

    def test_returns_none_outside_event_loop(self):
        # Patch asyncio.get_running_loop to simulate no running loop.
        bridge._drain_event = None
        with mock.patch("bridge.asyncio.get_running_loop", side_effect=RuntimeError):
            ev = _get_drain_event()
        assert ev is None

    def test_enqueue_sets_drain_event(self):
        """_enqueue_message must call ev.set() so the drain task wakes immediately."""
        async def _inner():
            bridge._drain_event = None
            ev = _get_drain_event()  # lazily create inside the loop
            assert ev is not None
            assert not ev.is_set()

            with mock.patch("bridge.get_session_status", return_value="waiting"), \
                 mock.patch("bridge._ensure_drain_task"):
                bridge._enqueue_message("conductor-test", "hello", "work")

            assert ev.is_set(), "drain event must be set after _enqueue_message"

        asyncio.run(_inner())
