"""
mcp_servers/workforce_mcp/server.py — Port 8004
mcp_servers/slack_mcp/server.py    — Port 8005
"""

# ── Workforce MCP ─────────────────────────────────────────────────────────────
from fastmcp import FastMCP as FastMCPW
mcp_w = FastMCPW("workforce-mcp")


@mcp_w.tool()
def list_available_adjusters(claim_type: str, province: str = "ON",
                              language: str = "en",
                              specialist_required: bool = False) -> dict:
    """List available adjusters for this claim type and location.
    Args:
        claim_type: e.g. personal_auto, commercial_property
        province: Two-letter province code
        language: en or fr
        specialist_required: Whether a specialist adjuster is needed
    Returns:
        Ranked list of available adjusters
    """
    return {
        "adjusters": [
            {"adjuster_id": "ADJ-001", "name": "Sarah Chen",
             "specialty": "personal_auto", "province": "ON",
             "language": "en", "current_load": 12, "score": 0.92},
            {"adjuster_id": "ADJ-042", "name": "Marc Tremblay",
             "specialty": "personal_auto", "province": "ON",
             "language": "fr", "current_load": 8, "score": 0.88},
        ]
    }


@mcp_w.tool()
def assign_claim(claim_id: str, adjuster_id: str,
                  priority: str = "standard") -> dict:
    """Formally assign a claim to an adjuster in the WFM system.
    Args:
        claim_id: Target claim
        adjuster_id: Adjuster to assign
        priority: standard | urgent | critical
    Returns:
        Assignment confirmation
    """
    return {"claim_id": claim_id, "adjuster_id": adjuster_id,
            "priority": priority, "status": "assigned",
            "estimated_first_contact": "within 4 hours"}


workforce_app = mcp_w.http_app()
