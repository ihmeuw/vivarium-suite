"""Slack notification support for psimulate workflows."""

from __future__ import annotations

import os

import requests
from loguru import logger

SLACK_API_BASE = "https://slack.com/api"
SLACK_TIMEOUT = 10  # seconds


def send_slack_notification(
    workflow_name: str,
    status: str,
    monitoring_url: str | None = None,
    results_dir: str | None = None,
) -> None:
    """Send a Slack notification after a workflow completes.

    Resolves the SLURM ``$USER`` to a Slack user via ``{user}@uw.edu``
    email lookup using the Slack API. Reads ``PSIMULATE_SLACK_BOT_TOKEN``
    from the environment. If the token is unset or any API call fails,
    logs a warning and returns without raising.

    Parameters
    ----------
    workflow_name
        The name of the workflow to include in the message.
    status
        The workflow status, e.g. ``"D"`` for DONE or ``"E"`` for ERROR.
    monitoring_url
        Optional URL to the Jobmon monitoring page for this workflow.
    results_dir
        Optional path to the results directory for this workflow.
    """
    try:
        token = os.environ.get("PSIMULATE_SLACK_BOT_TOKEN")
        if not token:
            logger.debug("PSIMULATE_SLACK_BOT_TOKEN not set. Skipping Slack notification.")
            return

        username = os.environ.get("USER", "unknown")
        headers = {"Authorization": f"Bearer {token}"}

        # Resolve SLURM username to Slack user ID via email lookup
        email = f"{username}@uw.edu"
        lookup_resp = requests.post(
            f"{SLACK_API_BASE}/users.lookupByEmail",
            headers=headers,
            data={"email": email},
            timeout=SLACK_TIMEOUT,
        )
        lookup_data = lookup_resp.json()
        if not lookup_data.get("ok"):
            logger.warning(
                f"Slack user lookup failed for {email}: {lookup_data.get('error')}"
            )
            return
        user_id = lookup_data["user"]["id"]

        # Open a DM conversation with the user
        convo_resp = requests.post(
            f"{SLACK_API_BASE}/conversations.open",
            headers=headers,
            json={"users": user_id},
            timeout=SLACK_TIMEOUT,
        )
        convo_data = convo_resp.json()
        if not convo_data.get("ok"):
            logger.warning(f"Slack conversations.open failed: {convo_data.get('error')}")
            return
        slack_id = convo_data["channel"]["id"]

        # Build the message
        status_text = "DONE" if status == "D" else "ERROR"
        emoji = "\u2705" if status == "D" else "\u274c"
        lines = [f"{emoji} psimulate workflow {status_text}: {workflow_name}"]
        if monitoring_url:
            lines.append(f"Monitor: {monitoring_url}")
        if results_dir:
            lines.append(f"Results: {results_dir}")
        message = "\n".join(lines)

        # Post the message
        msg_resp = requests.post(
            f"{SLACK_API_BASE}/chat.postMessage",
            headers=headers,
            json={"channel": slack_id, "text": message},
            timeout=SLACK_TIMEOUT,
        )
        msg_data = msg_resp.json()
        if not msg_data.get("ok"):
            logger.warning(f"Slack chat.postMessage failed: {msg_data.get('error')}")

    except Exception as e:
        logger.warning(f"Slack notification failed: {e}")
