"""
mcp_servers/slack_mcp/server.py
────────────────────────────────
Slack MCP Server — human-in-the-loop adjuster notifications.
DEPLOYMENT: uvicorn src.mcp_servers.slack_mcp.server:app --port 8005
"""

import os
import httpx
from fastmcp import FastMCP

mcp = FastMCP("slack-mcp")


@mcp.tool()
def post_approval_request(channel: str, claim_id: str, summary: str,
                            adjuster_id: str = None,
                            actions: list = None) -> dict:
    """
    Post an interactive Slack message asking an adjuster to review a claim.

    This is the HITL trigger. When the Decision Crew chooses HUMAN_REVIEW
    or SENIOR_ESCALATION, this tool is called to notify the adjuster.

    The adjuster clicks Approve / Modify / Deny → Slack sends a callback
    to POST /webhooks/slack in the FastAPI app → settlement continues.

    Args:
        channel: Slack channel name or ID
        claim_id: Claim requiring review
        summary: 3-sentence AI-generated summary for the adjuster
        adjuster_id: Optional Slack user ID to mention
        actions: Button labels (default: approve, modify, deny)

    Returns:
        Slack API response
    """
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        return {"ok": False, "error": "SLACK_BOT_TOKEN not configured"}

    if actions is None:
        actions = ["approve", "modify", "deny"]

    mention = f"<@{adjuster_id}> " if adjuster_id else ""
    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"{mention}*Claim {claim_id} requires your review.*\n\n{summary}"},
        },
        {
            "type": "actions",
            "elements": [
                {"type": "button",
                 "text": {"type": "plain_text", "text": a.title()},
                 "action_id": a,
                 "value": f"{claim_id}|{a}",
                 "style": "primary" if a == "approve" else "danger" if a == "deny" else None}
                for a in actions
            ],
        },
    ]

    try:
        with httpx.Client() as client:
            resp = client.post(
                "https://slack.com/api/chat.postMessage",
                json={"channel": channel, "blocks": blocks,
                      "text": f"Claim {claim_id} review required"},
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            return resp.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
def send_dm(user_id: str, message: str) -> dict:
    """Send a direct message to a Slack user."""
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        return {"ok": False, "error": "SLACK_BOT_TOKEN not configured"}
    try:
        with httpx.Client() as client:
            open_resp = client.post(
                "https://slack.com/api/conversations.open",
                json={"users": user_id},
                headers={"Authorization": f"Bearer {token}"}, timeout=10.0)
            channel_id = open_resp.json()["channel"]["id"]
            resp = client.post(
                "https://slack.com/api/chat.postMessage",
                json={"channel": channel_id, "text": message},
                headers={"Authorization": f"Bearer {token}"}, timeout=10.0)
            return resp.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


app = mcp.http_app()
