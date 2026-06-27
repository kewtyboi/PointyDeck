"""Tests for Mattermost WebSocket event parsing logic.

These tests replicate the core parsing and gate logic from
``_mm_event_handler`` (the closure inside ``run_mattermost_bridge``) as pure
inline functions -- no live server, no WebSocket connection, no tmux required.

The conftest loads conductor_bridge.py as ``bridge`` so we can import helpers
(mattermost_is_authorized, MATTERMOST_MAX_LENGTH) directly.

Coverage:
    - Valid ``posted`` event that passes all gates
    - Invalid JSON input (must not raise)
    - Event from a non-target channel (filtered out)
    - Empty message body (filtered out)
    - Bot self-post (filtered out)
    - Non-``posted`` event type (ignored)
    - Authorisation gate integration
"""
from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from bridge import mattermost_is_authorized, MATTERMOST_MAX_LENGTH


# ---------------------------------------------------------------------------
# Minimal inline event-parser (mirrors _mm_event_handler gate logic)
# ---------------------------------------------------------------------------

class _ParseResult:
    """Return value from _parse_posted_event."""
    def __init__(self, *, message: str | None, skip_reason: str | None):
        self.message = message          # non-None only when event should be dispatched
        self.skip_reason = skip_reason  # why the event was skipped (for assertions)

    @property
    def dispatched(self) -> bool:
        return self.message is not None


def _parse_posted_event(
    raw_event: Any,
    *,
    channel_id: str,
    own_user_id: str = "",
    allowed_users: list[str] | None = None,
    allow_all_dev: bool = False,
) -> _ParseResult:
    """Pure replication of _mm_event_handler's parse + gate logic.

    Returns a _ParseResult indicating whether the event would be dispatched
    to a conductor, and why not if it would be skipped.

    This intentionally omits conductor dispatch, queuing, and I/O -- those
    are integration concerns not suitable for pure unit tests.
    """
    if allowed_users is None:
        allowed_users = []

    # 1. Parse raw event
    try:
        if isinstance(raw_event, str):
            event = json.loads(raw_event)
        else:
            event = raw_event
    except Exception:
        return _ParseResult(message=None, skip_reason="invalid_json")

    # 2. Only handle "posted" events
    if event.get("event", "") != "posted":
        return _ParseResult(message=None, skip_reason="non_posted_event")

    # 3. Parse post payload
    try:
        data = event.get("data", {})
        post_raw = data.get("post", "{}")
        post = json.loads(post_raw) if isinstance(post_raw, str) else post_raw
    except Exception:
        return _ParseResult(message=None, skip_reason="invalid_post_json")

    # 4. Filter by channel
    if post.get("channel_id") != channel_id:
        return _ParseResult(message=None, skip_reason="wrong_channel")

    # 5. Filter bot self-posts
    sender_id = post.get("user_id", "")
    if own_user_id and sender_id == own_user_id:
        return _ParseResult(message=None, skip_reason="self_post")

    # 6. Authorisation gate
    if not mattermost_is_authorized(sender_id, allowed_users, allow_all_dev):
        return _ParseResult(message=None, skip_reason="unauthorised")

    # 7. Empty message filter
    text = post.get("message", "").strip()
    if not text:
        return _ParseResult(message=None, skip_reason="empty_message")

    return _ParseResult(message=text, skip_reason=None)


# ---------------------------------------------------------------------------
# Helpers to build realistic event payloads
# ---------------------------------------------------------------------------

def _make_posted_event(
    channel_id: str = "ch_target",
    user_id: str = "U_HUMAN",
    message: str = "Hello conductor",
) -> str:
    """Build a JSON string mimicking what mattermostautodriver passes to the handler."""
    post = {
        "channel_id": channel_id,
        "user_id": user_id,
        "message": message,
    }
    return json.dumps({
        "event": "posted",
        "data": {"post": json.dumps(post)},
    })


TARGET_CHANNEL = "ch_target"
BOT_USER_ID = "U_BOT"
HUMAN_USER_ID = "U_HUMAN"


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestValidPostedEvent:
    def test_valid_event_is_dispatched(self):
        """A well-formed posted event passes all gates and returns the message."""
        raw = _make_posted_event(channel_id=TARGET_CHANNEL, user_id=HUMAN_USER_ID, message="Run tests")
        result = _parse_posted_event(
            raw,
            channel_id=TARGET_CHANNEL,
            allowed_users=[HUMAN_USER_ID],
        )
        assert result.dispatched
        assert result.message == "Run tests"

    def test_valid_event_with_allow_all_dev(self):
        """Dev mode (empty list + allow_all_dev=True) dispatches any user."""
        raw = _make_posted_event(channel_id=TARGET_CHANNEL, user_id="U_RANDOM", message="test")
        result = _parse_posted_event(
            raw,
            channel_id=TARGET_CHANNEL,
            allowed_users=[],
            allow_all_dev=True,
        )
        assert result.dispatched
        assert result.message == "test"


class TestInvalidJSON:
    def test_invalid_json_string_skipped(self):
        """Malformed JSON must not raise; the event is silently skipped."""
        result = _parse_posted_event(
            "not json at all {{{",
            channel_id=TARGET_CHANNEL,
        )
        assert not result.dispatched
        assert result.skip_reason == "invalid_json"

    def test_empty_string_skipped(self):
        """An empty string is also silently skipped."""
        result = _parse_posted_event("", channel_id=TARGET_CHANNEL)
        assert not result.dispatched
        assert result.skip_reason == "invalid_json"

    def test_truncated_json_skipped(self):
        """Truncated JSON must not raise."""
        result = _parse_posted_event('{"event": "posted", "data": {', channel_id=TARGET_CHANNEL)
        assert not result.dispatched
        assert result.skip_reason == "invalid_json"


class TestNonTargetChannel:
    def test_different_channel_skipped(self):
        """Events from a channel that is not the configured one are ignored."""
        raw = _make_posted_event(channel_id="ch_other", user_id=HUMAN_USER_ID)
        result = _parse_posted_event(
            raw,
            channel_id=TARGET_CHANNEL,
            allowed_users=[HUMAN_USER_ID],
        )
        assert not result.dispatched
        assert result.skip_reason == "wrong_channel"

    def test_empty_channel_id_in_event_skipped(self):
        """A post with no channel_id does not match the target."""
        raw = json.dumps({
            "event": "posted",
            "data": {"post": json.dumps({"user_id": HUMAN_USER_ID, "message": "hi"})},
        })
        result = _parse_posted_event(
            raw,
            channel_id=TARGET_CHANNEL,
            allowed_users=[HUMAN_USER_ID],
        )
        assert not result.dispatched
        assert result.skip_reason == "wrong_channel"


class TestEmptyMessage:
    def test_empty_message_skipped(self):
        """A posted event with an empty message body is not dispatched."""
        raw = _make_posted_event(channel_id=TARGET_CHANNEL, user_id=HUMAN_USER_ID, message="")
        result = _parse_posted_event(
            raw,
            channel_id=TARGET_CHANNEL,
            allowed_users=[HUMAN_USER_ID],
        )
        assert not result.dispatched
        assert result.skip_reason == "empty_message"

    def test_whitespace_only_message_skipped(self):
        """Whitespace-only messages are also treated as empty."""
        raw = _make_posted_event(channel_id=TARGET_CHANNEL, user_id=HUMAN_USER_ID, message="   ")
        result = _parse_posted_event(
            raw,
            channel_id=TARGET_CHANNEL,
            allowed_users=[HUMAN_USER_ID],
        )
        assert not result.dispatched
        assert result.skip_reason == "empty_message"


class TestBotSelfPost:
    def test_bot_own_post_skipped(self):
        """The bot's own user_id must be filtered to prevent self-loops."""
        raw = _make_posted_event(channel_id=TARGET_CHANNEL, user_id=BOT_USER_ID, message="I replied")
        result = _parse_posted_event(
            raw,
            channel_id=TARGET_CHANNEL,
            own_user_id=BOT_USER_ID,
            allowed_users=[BOT_USER_ID],  # even if listed, self-post is filtered first
        )
        assert not result.dispatched
        assert result.skip_reason == "self_post"

    def test_other_user_not_filtered_as_self(self):
        """A different user is not filtered by the self-post guard."""
        raw = _make_posted_event(channel_id=TARGET_CHANNEL, user_id=HUMAN_USER_ID, message="hi")
        result = _parse_posted_event(
            raw,
            channel_id=TARGET_CHANNEL,
            own_user_id=BOT_USER_ID,
            allowed_users=[HUMAN_USER_ID],
        )
        assert result.dispatched

    def test_missing_own_user_id_does_not_filter(self):
        """When own_user_id is not set (empty string), no self-filter is applied."""
        raw = _make_posted_event(channel_id=TARGET_CHANNEL, user_id=HUMAN_USER_ID, message="go")
        result = _parse_posted_event(
            raw,
            channel_id=TARGET_CHANNEL,
            own_user_id="",
            allowed_users=[HUMAN_USER_ID],
        )
        assert result.dispatched


class TestNonPostedEventType:
    def test_reaction_added_skipped(self):
        raw = json.dumps({"event": "reaction_added", "data": {}})
        result = _parse_posted_event(raw, channel_id=TARGET_CHANNEL)
        assert not result.dispatched
        assert result.skip_reason == "non_posted_event"

    def test_hello_event_skipped(self):
        raw = json.dumps({"event": "hello", "data": {}})
        result = _parse_posted_event(raw, channel_id=TARGET_CHANNEL)
        assert not result.dispatched
        assert result.skip_reason == "non_posted_event"


class TestAuthorisationGate:
    def test_unlisted_user_fail_closed(self):
        """Non-empty allow-list rejects users not on it."""
        raw = _make_posted_event(channel_id=TARGET_CHANNEL, user_id="U_EVE", message="hack")
        result = _parse_posted_event(
            raw,
            channel_id=TARGET_CHANNEL,
            allowed_users=["U_ALICE"],
        )
        assert not result.dispatched
        assert result.skip_reason == "unauthorised"

    def test_empty_list_no_dev_fail_closed(self):
        """Empty allow-list with allow_all_dev=False rejects everyone (hardened default)."""
        raw = _make_posted_event(channel_id=TARGET_CHANNEL, user_id=HUMAN_USER_ID, message="hi")
        result = _parse_posted_event(
            raw,
            channel_id=TARGET_CHANNEL,
            allowed_users=[],
            allow_all_dev=False,
        )
        assert not result.dispatched
        assert result.skip_reason == "unauthorised"


class TestMattermostMaxLength:
    def test_max_length_constant_accessible(self):
        """MATTERMOST_MAX_LENGTH is importable from bridge and is a reasonable positive int."""
        assert isinstance(MATTERMOST_MAX_LENGTH, int)
        assert MATTERMOST_MAX_LENGTH > 0
