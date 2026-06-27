"""Tests for Mattermost user authorisation in conductor bridge.

Mirrors internal/session/test_slack_authorization.py style but targets the
module-level ``mattermost_is_authorized`` helper introduced by the hardening
work.  The conftest loads conductor_bridge.py as the ``bridge`` module so the
import below resolves without any path manipulation.
"""
import logging
import pytest

from bridge import mattermost_is_authorized


class TestMattermostAuthorization:
    """Tests for mattermost_is_authorized(user_id, allowed_users, allow_all_dev)."""

    def setup_method(self):
        logging.basicConfig(level=logging.WARNING)

    # ------------------------------------------------------------------
    # Non-empty allow-list
    # ------------------------------------------------------------------

    def test_allowed_user_returns_true(self):
        """A user present in the allow-list is accepted."""
        assert mattermost_is_authorized("U_ALICE", ["U_ALICE", "U_BOB"]) is True

    def test_second_allowed_user_returns_true(self):
        """Any user in the list is accepted, not just the first."""
        assert mattermost_is_authorized("U_BOB", ["U_ALICE", "U_BOB"]) is True

    def test_denied_user_non_empty_list_returns_false(self, caplog):
        """A user NOT in a non-empty allow-list is rejected."""
        with caplog.at_level(logging.WARNING, logger="bridge"):
            result = mattermost_is_authorized("U_EVE", ["U_ALICE", "U_BOB"])
        assert result is False

    def test_single_allowed_user_blocks_others(self):
        """Only the specified user is accepted when the list has one entry."""
        allowed = ["U_ONLY"]
        assert mattermost_is_authorized("U_ONLY", allowed) is True
        assert mattermost_is_authorized("U_OTHER", allowed) is False

    # ------------------------------------------------------------------
    # Empty allow-list -- fail-closed (hardened default)
    # ------------------------------------------------------------------

    def test_empty_list_allow_all_dev_false_returns_false(self, caplog):
        """Empty allow-list + allow_all_dev=False (default) -> fail closed."""
        with caplog.at_level(logging.WARNING, logger="bridge"):
            result = mattermost_is_authorized("U_ANYONE", [], allow_all_dev=False)
        assert result is False

    def test_empty_list_default_allow_all_dev_returns_false(self):
        """allow_all_dev defaults to False, so empty list -> reject."""
        assert mattermost_is_authorized("U_ANYONE", []) is False

    # ------------------------------------------------------------------
    # Empty allow-list -- dev escape hatch
    # ------------------------------------------------------------------

    def test_empty_list_allow_all_dev_true_returns_true(self, caplog):
        """Empty allow-list + allow_all_dev=True -> accept (dev mode) with warning."""
        with caplog.at_level(logging.WARNING, logger="bridge"):
            result = mattermost_is_authorized("U_DEVUSER", [], allow_all_dev=True)
        assert result is True

    def test_dev_mode_emits_warning(self, caplog):
        """Dev mode acceptance must emit a WARNING log so the operator notices."""
        with caplog.at_level(logging.WARNING, logger="bridge"):
            mattermost_is_authorized("U_DEVUSER", [], allow_all_dev=True)
        assert any("DEV" in r.message.upper() or "allow_all_users_for_dev" in r.message
                   for r in caplog.records), "Expected a dev-mode warning log"

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_empty_user_id_in_list_returns_true(self):
        """An empty string user_id that is explicitly listed is accepted."""
        assert mattermost_is_authorized("", [""]) is True

    def test_empty_user_id_not_in_list_returns_false(self):
        """An empty string user_id not in the list is rejected."""
        assert mattermost_is_authorized("", ["U_ALICE"]) is False
