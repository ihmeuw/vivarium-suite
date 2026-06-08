"""Unit tests for Slack bot app notifications."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import click
import pytest

from vivarium_cluster_tools.core.cli_tools import validate_slack_options
from vivarium_cluster_tools.core.notifications import send_slack_notification

BOT_TOKEN = "xoxb-test-token"
MONITORING_URL = "https://jobmon.example.com/#/workflow/123"
RESULTS_DIR = "/tmp/results"
WORKFLOW_NAME = "my_pipeline"
COMMAND_LABEL = "dagger run"

SLACK_API = "https://slack.com/api"


def _mock_slack_responses() -> MagicMock:
    """Return a mock for ``requests.post`` that returns canned Slack API responses.

    Call 1 (users.lookupByEmail): returns user ID ``U12345``.
    Call 2 (conversations.open):  returns DM channel ``D67890``.
    Call 3 (chat.postMessage):    returns ok.
    """
    mock = MagicMock()

    lookup_resp = MagicMock()
    lookup_resp.json.return_value = {"ok": True, "user": {"id": "U12345"}}

    convo_resp = MagicMock()
    convo_resp.json.return_value = {"ok": True, "channel": {"id": "D67890"}}

    post_resp = MagicMock()
    post_resp.json.return_value = {"ok": True}

    mock.side_effect = [lookup_resp, convo_resp, post_resp]
    return mock


def test_no_token_skips_notification(monkeypatch: pytest.MonkeyPatch) -> None:
    """When PSIMULATE_SLACK_BOT_TOKEN is unset, no Slack API calls are made."""
    monkeypatch.delenv("PSIMULATE_SLACK_BOT_TOKEN", raising=False)
    with patch(
        "vivarium_cluster_tools.core.notifications.requests.post",
    ) as mock_post:
        send_slack_notification(
            workflow_name=WORKFLOW_NAME, status="D", command_label=COMMAND_LABEL
        )
        mock_post.assert_not_called()


def test_notification_on_workflow_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful workflow DMs the user via the Slack bot with a DONE message."""
    monkeypatch.setenv("PSIMULATE_SLACK_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("USER", "testuser")

    mock_post = _mock_slack_responses()
    with patch(
        "vivarium_cluster_tools.core.notifications.requests.post",
        mock_post,
    ):
        send_slack_notification(
            workflow_name=WORKFLOW_NAME,
            status="D",
            command_label=COMMAND_LABEL,
            monitoring_url=MONITORING_URL,
            results_dir=RESULTS_DIR,
        )

        assert mock_post.call_count == 3

        # Call 1: users.lookupByEmail
        lookup_call = mock_post.call_args_list[0]
        assert lookup_call[0][0] == f"{SLACK_API}/users.lookupByEmail"
        assert "testuser@uw.edu" in str(lookup_call)

        # Call 2: conversations.open
        convo_call = mock_post.call_args_list[1]
        assert convo_call[0][0] == f"{SLACK_API}/conversations.open"
        assert "U12345" in str(convo_call)

        # Call 3: chat.postMessage
        msg_call = mock_post.call_args_list[2]
        assert msg_call[0][0] == f"{SLACK_API}/chat.postMessage"
        msg_json = (
            msg_call[1].get("json") or msg_call[0][1]
            if len(msg_call[0]) > 1
            else msg_call[1].get("json")
        )
        assert msg_json["channel"] == "D67890"
        assert "DONE" in msg_json["text"]
        assert WORKFLOW_NAME in msg_json["text"]
        assert COMMAND_LABEL in msg_json["text"]
        assert MONITORING_URL in msg_json["text"]
        assert RESULTS_DIR in msg_json["text"]


def test_success_with_channel_posts_to_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful run with --slack-channel posts to the channel, no DM lookup."""
    monkeypatch.setenv("PSIMULATE_SLACK_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("USER", "testuser")

    post_resp = MagicMock()
    post_resp.json.return_value = {"ok": True}
    mock_post = MagicMock(side_effect=[post_resp])

    with patch("vivarium_cluster_tools.core.notifications.requests.post", mock_post):
        send_slack_notification(
            workflow_name=WORKFLOW_NAME,
            status="D",
            command_label=COMMAND_LABEL,
            slack_channel="#my-channel",
        )

        # Only the postMessage call is made: no email lookup, no DM open.
        assert mock_post.call_count == 1
        msg_call = mock_post.call_args_list[0]
        assert msg_call[0][0] == f"{SLACK_API}/chat.postMessage"
        msg_json = msg_call.kwargs["json"]
        assert msg_json["channel"] == "#my-channel"
        assert "DONE" in msg_json["text"]
        assert "<@" not in msg_json["text"]


def test_channel_without_hash_is_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    """A channel given without a leading '#' is normalized to include one."""
    monkeypatch.setenv("PSIMULATE_SLACK_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("USER", "testuser")

    post_resp = MagicMock()
    post_resp.json.return_value = {"ok": True}
    mock_post = MagicMock(side_effect=[post_resp])

    with patch("vivarium_cluster_tools.core.notifications.requests.post", mock_post):
        send_slack_notification(
            workflow_name=WORKFLOW_NAME,
            status="D",
            command_label=COMMAND_LABEL,
            slack_channel="my-channel",
        )

        assert mock_post.call_count == 1
        msg_json = mock_post.call_args_list[0].kwargs["json"]
        assert msg_json["channel"] == "#my-channel"


def test_success_with_channel_and_tag_mentions_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful run with --slack-channel and --slack-tag @-mentions the tagged user."""
    monkeypatch.setenv("PSIMULATE_SLACK_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("USER", "testuser")

    lookup_resp = MagicMock()
    lookup_resp.json.return_value = {"ok": True, "user": {"id": "U99999"}}
    post_resp = MagicMock()
    post_resp.json.return_value = {"ok": True}
    mock_post = MagicMock(side_effect=[lookup_resp, post_resp])

    with patch("vivarium_cluster_tools.core.notifications.requests.post", mock_post):
        send_slack_notification(
            workflow_name=WORKFLOW_NAME,
            status="D",
            command_label=COMMAND_LABEL,
            slack_channel="#my-channel",
            slack_tag="coworker",
        )

        assert mock_post.call_count == 2

        # Call 1: resolve the tagged user's Slack ID.
        lookup_call = mock_post.call_args_list[0]
        assert lookup_call[0][0] == f"{SLACK_API}/users.lookupByEmail"
        assert "coworker@uw.edu" in str(lookup_call)

        # Call 2: post to the channel with the mention prefixed.
        msg_call = mock_post.call_args_list[1]
        assert msg_call[0][0] == f"{SLACK_API}/chat.postMessage"
        msg_json = msg_call.kwargs["json"]
        assert msg_json["channel"] == "#my-channel"
        assert "<@U99999>" in msg_json["text"]


def test_failure_with_channel_and_tag_dms_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On failure, channel/tag are ignored and the launcher is DM'd without a mention."""
    monkeypatch.setenv("PSIMULATE_SLACK_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("USER", "testuser")

    mock_post = _mock_slack_responses()
    with patch("vivarium_cluster_tools.core.notifications.requests.post", mock_post):
        send_slack_notification(
            workflow_name=WORKFLOW_NAME,
            status="F",
            command_label=COMMAND_LABEL,
            slack_channel="#my-channel",
            slack_tag="coworker",
        )

        # Full DM flow: resolve launcher, open DM, post -- no channel post.
        assert mock_post.call_count == 3
        lookup_call = mock_post.call_args_list[0]
        assert "testuser@uw.edu" in str(lookup_call)

        msg_call = mock_post.call_args_list[2]
        msg_json = msg_call.kwargs["json"]
        assert msg_json["channel"] == "D67890"
        assert "ERROR" in msg_json["text"]
        assert "<@" not in msg_json["text"]


def test_validate_slack_options_rejects_tag_without_channel() -> None:
    """--slack-tag without --slack-channel is a usage error."""
    with pytest.raises(click.UsageError, match="--slack-tag requires --slack-channel"):
        validate_slack_options(slack_channel=None, slack_tag="coworker")


def test_validate_slack_options_allows_valid_combinations() -> None:
    """Channel-only, channel+tag, and neither are all accepted."""
    validate_slack_options(slack_channel=None, slack_tag=None)
    validate_slack_options(slack_channel="#my-channel", slack_tag=None)
    validate_slack_options(slack_channel="#my-channel", slack_tag="coworker")


def test_notification_on_workflow_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed workflow DMs the user via the Slack bot with an ERROR message."""
    monkeypatch.setenv("PSIMULATE_SLACK_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("USER", "testuser")

    mock_post = _mock_slack_responses()
    with patch(
        "vivarium_cluster_tools.core.notifications.requests.post",
        mock_post,
    ):
        send_slack_notification(
            workflow_name=WORKFLOW_NAME,
            status="F",
            command_label=COMMAND_LABEL,
            monitoring_url=MONITORING_URL,
            results_dir=RESULTS_DIR,
        )

        assert mock_post.call_count == 3

        # Call 1: users.lookupByEmail
        lookup_call = mock_post.call_args_list[0]
        assert lookup_call[0][0] == f"{SLACK_API}/users.lookupByEmail"
        assert "testuser@uw.edu" in str(lookup_call)

        # Call 2: conversations.open
        convo_call = mock_post.call_args_list[1]
        assert convo_call[0][0] == f"{SLACK_API}/conversations.open"
        assert "U12345" in str(convo_call)

        # Call 3: chat.postMessage
        msg_call = mock_post.call_args_list[2]
        assert msg_call[0][0] == f"{SLACK_API}/chat.postMessage"
        msg_json = (
            msg_call[1].get("json") or msg_call[0][1]
            if len(msg_call[0]) > 1
            else msg_call[1].get("json")
        )
        assert msg_json["channel"] == "D67890"
        assert "ERROR" in msg_json["text"]
        assert WORKFLOW_NAME in msg_json["text"]
        assert COMMAND_LABEL in msg_json["text"]
        assert MONITORING_URL in msg_json["text"]
        assert RESULTS_DIR in msg_json["text"]
