"""Tests for issue #979 - escalation flow: question surfacing, stall detection,
state.json writes, and session reply routing.

Run with: pytest conductor/tests/test_escalation_979_bridge.py -v
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import types
from pathlib import Path
from unittest import mock

# ---------------------------------------------------------------------------
# Bootstrap: make conductor_bridge importable without a real config/env
# ---------------------------------------------------------------------------

CONDUCTOR_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(CONDUCTOR_DIR))

# Stub heavyweight optional deps so import never fails in CI
for _mod in ["toml", "aiogram", "aiogram.client.session.aiohttp",
             "slack_bolt", "slack_bolt.async_app",
             "slack_bolt.adapter.socket_mode.async_handler",
             "slack_bolt.authorization",
             "slack_sdk.web.async_client",
             "discord", "mattermostautodriver"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.SimpleNamespace(
            load=lambda *a, **k: {},
            Bot=None, Dispatcher=None, AsyncTypedDriver=None,
        )

# Patch toml.load before bridge imports config
sys.modules["toml"].load = lambda *a, **k: {}  # type: ignore[union-attr]

import bridge as _bridge_mod  # noqa: E402  (after sys.path / stub setup)
from bridge import (  # noqa: E402
    _output_hash,
    filter_need_lines,
    mark_auto_response,
    mark_session_escalated,
    parse_session_reply_prefix,
    read_conductor_state,
    write_conductor_state,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# _output_hash
# ---------------------------------------------------------------------------

class TestOutputHash:
    def test_consistent(self):
        assert _output_hash("hello") == _output_hash("hello")

    def test_different_on_different_input(self):
        assert _output_hash("hello") != _output_hash("world")

    def test_returns_16_hex_chars(self):
        h = _output_hash("anything")
        assert len(h) == 16
        int(h, 16)  # raises if not valid hex


# ---------------------------------------------------------------------------
# read/write_conductor_state and mark helpers
# ---------------------------------------------------------------------------

class TestConductorStateIO:
    def test_read_returns_empty_dict_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_bridge_mod, "CONDUCTOR_DIR", tmp_path)
        result = read_conductor_state("no-such-conductor")
        assert result == {}

    def test_write_then_read_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_bridge_mod, "CONDUCTOR_DIR", tmp_path)
        state = {"sessions": {}, "escalations_today": 0}
        write_conductor_state("myconductor", state)
        assert (tmp_path / "myconductor" / "state.json").exists()
        result = read_conductor_state("myconductor")
        assert result == state

    def test_write_is_atomic_via_tmp(self, tmp_path, monkeypatch):
        """Atomic write: tmp file should be gone after successful write."""
        monkeypatch.setattr(_bridge_mod, "CONDUCTOR_DIR", tmp_path)
        write_conductor_state("c", {"x": 1})
        tmp_candidate = tmp_path / "c" / "state.json.tmp"
        assert not tmp_candidate.exists()


class TestMarkSessionEscalated:
    def _make_state(self, tmp_path, conductor, sessions_dict):
        state = {
            "sessions": sessions_dict,
            "escalations_today": 0,
            "auto_responses_today": 0,
        }
        write_conductor_state(conductor, state)

    def test_sets_escalated_flag(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_bridge_mod, "CONDUCTOR_DIR", tmp_path)
        self._make_state(tmp_path, "main", {
            "abc123": {"title": "my-session", "escalated": False}
        })
        mark_session_escalated("main", "my-session")
        state = read_conductor_state("main")
        assert state["sessions"]["abc123"]["escalated"] is True

    def test_increments_escalations_today(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_bridge_mod, "CONDUCTOR_DIR", tmp_path)
        self._make_state(tmp_path, "main", {})
        mark_session_escalated("main", "anything")
        assert read_conductor_state("main")["escalations_today"] == 1
        mark_session_escalated("main", "anything")
        assert read_conductor_state("main")["escalations_today"] == 2

    def test_noop_when_no_state_file(self, tmp_path, monkeypatch):
        """Should not raise when state.json does not exist."""
        monkeypatch.setattr(_bridge_mod, "CONDUCTOR_DIR", tmp_path)
        mark_session_escalated("missing-conductor", "any-session")  # must not raise


class TestMarkAutoResponse:
    def test_increments_auto_responses_today(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_bridge_mod, "CONDUCTOR_DIR", tmp_path)
        write_conductor_state("main", {"auto_responses_today": 3})
        mark_auto_response("main")
        assert read_conductor_state("main")["auto_responses_today"] == 4

    def test_starts_at_1_when_field_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_bridge_mod, "CONDUCTOR_DIR", tmp_path)
        write_conductor_state("main", {})
        mark_auto_response("main")
        assert read_conductor_state("main")["auto_responses_today"] == 1


# ---------------------------------------------------------------------------
# parse_session_reply_prefix
# ---------------------------------------------------------------------------

class TestParseSessionReplyPrefix:
    _sessions = [
        {"title": "945", "status": "waiting"},
        {"title": "apply-gov-revisions", "status": "running"},
        {"title": "conductor-main", "status": "running"},  # excluded
    ]

    def test_matches_exact_title_prefix(self):
        title, body = parse_session_reply_prefix("945: yes merge now", self._sessions)
        assert title == "945"
        assert body == "yes merge now"

    def test_matches_longer_title(self):
        title, body = parse_session_reply_prefix(
            "apply-gov-revisions: please continue", self._sessions
        )
        assert title == "apply-gov-revisions"
        assert body == "please continue"

    def test_excludes_conductor_sessions(self):
        title, body = parse_session_reply_prefix(
            "conductor-main: do something", self._sessions
        )
        # conductor-main is excluded; no match
        assert title is None
        assert body == "conductor-main: do something"

    def test_no_match_returns_none_and_original(self):
        title, body = parse_session_reply_prefix("status", self._sessions)
        assert title is None
        assert body == "status"

    def test_first_match_wins(self):
        sessions = [
            {"title": "ab", "status": "waiting"},
            {"title": "abc", "status": "waiting"},
        ]
        title, body = parse_session_reply_prefix("abc: hello", sessions)
        # "ab" prefix does NOT match "abc:" (requires exactly "ab:"), so "abc" wins
        assert title == "abc"
        assert body == "hello"

    def test_empty_sessions_list(self):
        title, body = parse_session_reply_prefix("anything: here", [])
        assert title is None

    def test_body_stripped(self):
        title, body = parse_session_reply_prefix("945:   lots of spaces   ", self._sessions)
        assert title == "945"
        assert body == "lots of spaces"

    def test_no_match_when_title_only_no_colon(self):
        title, body = parse_session_reply_prefix("945 merge now", self._sessions)
        assert title is None


# ---------------------------------------------------------------------------
# question de-dup (A1 hash logic)
# ---------------------------------------------------------------------------

class TestQuestionDedup:
    """Unit-test the hash-based de-dup logic used in A1 question surfacing.

    The bridge code uses question_hash_by_session[title] to track the last
    posted hash. We test the invariant: same question text => same hash =>
    skip; different text => different hash => post.
    """

    def test_same_question_produces_same_hash(self):
        q = "Should I merge PR #957 into main?"
        assert _output_hash(q) == _output_hash(q)

    def test_different_question_produces_different_hash(self):
        q1 = "Should I merge PR #957 into main?"
        q2 = "Should I merge PR #958 into main?"
        assert _output_hash(q1) != _output_hash(q2)

    def test_dedup_dict_prevents_repost(self):
        """Simulate the heartbeat loop de-dup dict: second call with same hash skips."""
        posted_questions = []
        question_hash_by_session: dict[str, str] = {}
        question_text = "Are you sure you want to proceed?"
        session_title = "my-session"

        def _try_post(title, text):
            new_hash = _output_hash(text)
            if question_hash_by_session.get(title) == new_hash:
                return False  # skip
            question_hash_by_session[title] = new_hash
            posted_questions.append(text)
            return True

        assert _try_post(session_title, question_text) is True
        assert len(posted_questions) == 1

        # Second call with same text: should skip
        assert _try_post(session_title, question_text) is False
        assert len(posted_questions) == 1

        # Different text: should post
        assert _try_post(session_title, "A new question?") is True
        assert len(posted_questions) == 2


# ---------------------------------------------------------------------------
# Stall detection logic
# ---------------------------------------------------------------------------

class TestStallDetection:
    """Unit-test the stall-detection state machine logic.

    We replicate the key branching from heartbeat_loop: if hash unchanged
    for >= STALL_MINUTES_DEFAULT minutes and not yet alerted, post once.
    """

    def _run_stall_check(
        self,
        stall_state: dict,
        session_title: str,
        output_text: str,
        now_mono: float,
        stall_minutes: float,
    ) -> tuple[bool, dict]:
        """Return (should_alert, updated_stall_state_entry)."""
        out_hash = _output_hash(output_text)
        prev = stall_state.get(session_title, {})
        if prev.get("hash") != out_hash:
            stall_state[session_title] = {"hash": out_hash, "since": now_mono, "alerted": False}
            return False, stall_state
        elapsed_min = (now_mono - prev.get("since", now_mono)) / 60.0
        if elapsed_min >= stall_minutes and not prev.get("alerted"):
            stall_state[session_title]["alerted"] = True
            return True, stall_state
        return False, stall_state

    def test_no_alert_when_output_changes(self):
        state: dict = {}
        t0 = 0.0
        should_alert, state = self._run_stall_check(state, "sess", "output-v1", t0, 30)
        assert not should_alert
        t1 = t0 + 2000.0  # 33+ minutes later
        should_alert, state = self._run_stall_check(state, "sess", "output-v2", t1, 30)
        assert not should_alert, "Output changed, should NOT alert"

    def test_alert_when_output_frozen_for_threshold(self):
        state: dict = {}
        t0 = 0.0
        # First observation: sets the clock
        should_alert, state = self._run_stall_check(state, "sess", "frozen", t0, 30)
        assert not should_alert
        # 31 minutes later, same output
        t1 = t0 + 31 * 60
        should_alert, state = self._run_stall_check(state, "sess", "frozen", t1, 30)
        assert should_alert, "Frozen for > stall_minutes, should alert"

    def test_alert_fires_only_once(self):
        state: dict = {}
        t0 = 0.0
        self._run_stall_check(state, "sess", "frozen", t0, 30)
        t1 = t0 + 31 * 60
        should_alert1, state = self._run_stall_check(state, "sess", "frozen", t1, 30)
        assert should_alert1
        # Third call: alerted is now True, should NOT fire again
        t2 = t1 + 10 * 60
        should_alert2, state = self._run_stall_check(state, "sess", "frozen", t2, 30)
        assert not should_alert2, "Already alerted, must not fire again"

    def test_no_alert_below_threshold(self):
        state: dict = {}
        t0 = 0.0
        self._run_stall_check(state, "sess", "frozen", t0, 30)
        t1 = t0 + 10 * 60  # only 10 min
        should_alert, _ = self._run_stall_check(state, "sess", "frozen", t1, 30)
        assert not should_alert, "Under threshold, should not alert"


# ---------------------------------------------------------------------------
# filter_need_lines (regression guard - existing behaviour unchanged)
# ---------------------------------------------------------------------------

class TestFilterNeedLinesRegression:
    def test_fresh_need_line_forwarded(self):
        result = filter_need_lines("NEED: approval for merge", {})
        assert result["alerts"] == ["NEED: approval for merge"]
        assert result["retired"] == []

    def test_repeated_need_line_retired_at_threshold(self):
        line = "NEED: approval for merge"
        counts: dict = {}
        for _ in range(2):
            r = filter_need_lines(line, counts)
            counts = r["counts"]
        r = filter_need_lines(line, counts)
        assert any("STILL BLOCKED" in s for s in r["retired"])

    def test_non_need_lines_ignored(self):
        result = filter_need_lines("Just a normal response.\nNEED: something", {})
        assert len(result["alerts"]) == 1
        assert result["alerts"][0] == "NEED: something"
