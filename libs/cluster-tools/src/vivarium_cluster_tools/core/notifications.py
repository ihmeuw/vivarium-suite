"""Slack notification support for parallel-workflow CLIs."""

from __future__ import annotations

import os

import requests
from loguru import logger

SLACK_API_BASE = "https://slack.com/api"
SLACK_TIMEOUT = 10  # seconds


def _resolve_user_id(token: str, username: str) -> str | None:
    """Resolve a SLURM/UW username to a Slack user ID via ``{username}@uw.edu`` lookup."""
    email = f"{username}@uw.edu"
    resp = requests.post(
        f"{SLACK_API_BASE}/users.lookupByEmail",
        headers={"Authorization": f"Bearer {token}"},
        data={"email": email},
        timeout=SLACK_TIMEOUT,
    )
    data = resp.json()
    if not data.get("ok"):
        logger.warning(f"Slack user lookup failed for {email}: {data.get('error')}")
        return None
    return str(data["user"]["id"])


def _open_dm(token: str, user_id: str) -> str | None:
    """Open a direct-message conversation with a user and return its channel ID."""
    resp = requests.post(
        f"{SLACK_API_BASE}/conversations.open",
        headers={"Authorization": f"Bearer {token}"},
        json={"users": user_id},
        timeout=SLACK_TIMEOUT,
    )
    data = resp.json()
    if not data.get("ok"):
        logger.warning(f"Slack conversations.open failed: {data.get('error')}")
        return None
    return str(data["channel"]["id"])


def send_slack_notification(
    workflow_name: str,
    status: str,
    command_label: str,
    monitoring_url: str | None = None,
    results_dir: str | None = None,
    slack_channel: str | None = None,
    slack_tag: str | None = None,
) -> None:
    """Send a Slack notification after a workflow completes.

    On success, the message is sent to ``slack_channel`` if one is given
    (optionally @-mentioning ``slack_tag``); otherwise it is direct-messaged
    to the launching user. On failure, the message is always direct-messaged
    to the launching user and ``slack_channel``/``slack_tag`` are ignored, so a
    tagged collaborator is never pinged for a run they are not responsible for.

    The launching user is the SLURM ``$USER``, resolved to a Slack user via a
    ``{user}@uw.edu`` email lookup. Reads ``PSIMULATE_SLACK_BOT_TOKEN`` from the
    environment. If the token is unset or any API call fails, logs a warning and
    returns without raising.

    Parameters
    ----------
    workflow_name
        The name of the workflow to include in the message.
    status
        The workflow status, e.g. ``"D"`` for DONE or ``"E"`` for ERROR.
    command_label
        Short string identifying which CLI invocation triggered the
        notification (e.g. ``"psimulate run"`` or ``"dagger run"``).
        Rendered into the message header.
    monitoring_url
        Optional URL to the Jobmon monitoring page for this workflow.
    results_dir
        Optional path to the results directory for this workflow.
    slack_channel
        Optional channel name (e.g. ``"my-channel"``) to post a
        successful-run notification to instead of DMing the launching user. The
        Slack bot must already be a member of the channel. Ignored on failure.
    slack_tag
        Optional username to @-mention in the channel notification on success.
        Only honored alongside ``slack_channel``; ignored on failure.
    """
    try:
        token = os.environ.get("PSIMULATE_SLACK_BOT_TOKEN")
        if not token:
            logger.debug("PSIMULATE_SLACK_BOT_TOKEN not set. Skipping Slack notification.")
            return

        launcher = os.environ.get("USER", "unknown")
        success = status == "D"

        mention = ""
        if success and slack_channel:
            # Post to the requested channel; the bot must already be a member.
            # Users pass the bare channel name; prepend the '#' Slack expects.
            channel = f"#{slack_channel.lstrip('#')}"
            if slack_tag:
                tag_id = _resolve_user_id(token, slack_tag)
                if tag_id is None:
                    return
                mention = f"<@{tag_id}> "
        else:
            # Default path, and every failure: DM the launching user.
            user_id = _resolve_user_id(token, launcher)
            if user_id is None:
                return
            dm_channel = _open_dm(token, user_id)
            if dm_channel is None:
                return
            channel = dm_channel

        # Build the message
        status_text = "DONE" if success else "ERROR"
        emoji = "✅" if success else "❌"
        lines = [f"{mention}{emoji} {command_label} {status_text}: {workflow_name}"]
        if monitoring_url:
            lines.append(f"Monitor: {monitoring_url}")
        if results_dir:
            lines.append(f"Results: {results_dir}")
        message = "\n".join(lines)

        # Post the message
        msg_resp = requests.post(
            f"{SLACK_API_BASE}/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json={"channel": channel, "text": message},
            timeout=SLACK_TIMEOUT,
        )
        msg_data = msg_resp.json()
        if not msg_data.get("ok"):
            logger.warning(f"Slack chat.postMessage failed: {msg_data.get('error')}")

    except Exception as e:
        logger.warning(f"Slack notification failed: {e}")
