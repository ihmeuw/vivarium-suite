"""Unit tests for Slack bot app notifications."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from vivarium_cluster_tools.psimulate.notifications import send_slack_notification

BOT_TOKEN = "xoxb-test-token"
MONITORING_URL = "https://jobmon.example.com/#/workflow/123"
RESULTS_DIR = "/tmp/results"
WORKFLOW_NAME = "my_pipeline"

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
        "vivarium_cluster_tools.psimulate.notifications.requests.post",
    ) as mock_post:
        send_slack_notification(workflow_name=WORKFLOW_NAME, status="D")
        mock_post.assert_not_called()


def test_notification_on_workflow_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful workflow DMs the user via the Slack bot with a DONE message."""
    monkeypatch.setenv("PSIMULATE_SLACK_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("USER", "testuser")

    mock_post = _mock_slack_responses()
    with patch(
        "vivarium_cluster_tools.psimulate.notifications.requests.post",
        mock_post,
    ):
        send_slack_notification(
            workflow_name=WORKFLOW_NAME,
            status="D",
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
        assert MONITORING_URL in msg_json["text"]
        assert RESULTS_DIR in msg_json["text"]


def test_notification_on_workflow_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed workflow DMs the user via the Slack bot with an ERROR message."""
    monkeypatch.setenv("PSIMULATE_SLACK_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("USER", "testuser")

    mock_post = _mock_slack_responses()
    with patch(
        "vivarium_cluster_tools.psimulate.notifications.requests.post",
        mock_post,
    ):
        send_slack_notification(
            workflow_name=WORKFLOW_NAME,
            status="F",
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
        assert MONITORING_URL in msg_json["text"]
        assert RESULTS_DIR in msg_json["text"]
