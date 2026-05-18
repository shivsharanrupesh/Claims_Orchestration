"""
mcp_servers/claims_mcp/server.py
─────────────────────────────────
Claims MCP Server — tools for the Claims Management System.

Tools exposed:
  - get_claim_history  → past claims for a claimant (used by Enrichment Specialist)
  - settle_claim       → trigger payment (used by Settlement Worker)
  - deny_claim         → record denial (used by Settlement Worker)
  - attach_document    → link a Blob document to a claim record

DEPLOYMENT: uvicorn src.mcp_servers.claims_mcp.server:app --port 8001
REPLACE THE STUBS with real calls to your Claims Management System
(Guidewire, Duck Creek, or in-house).
"""

from fastmcp import FastMCP
from loguru import logger

mcp = FastMCP("claims-mcp")


@mcp.tool()
def get_claim_history(claimant_id: str, months: int = 24) -> dict:
    """Get a claimant's claim history for the past N months.
    Args:
        claimant_id: Unique claimant identifier
        months: Look-back window (default 24)
    Returns:
        claim_count, total_paid_cad, past_claims list
    """
    logger.info(f"claims_mcp.get_claim_history | claimant={claimant_id}")
    # ── STUB: replace with real CMS API call ──
    return {
        "claimant_id": claimant_id,
        "claim_count": 1,
        "total_paid_cad": 4200.00,
        "past_claims": [
            {"claim_id": "CLM-2023-001", "type": "auto_collision",
             "amount_cad": 4200.00, "date": "2023-08-15", "settled": True},
        ],
    }


@mcp.tool()
def settle_claim(claim_id: str, settlement_amount_cad: float,
                  adjuster_id: str = None) -> dict:
    """Trigger settlement payment for an approved claim.
    Args:
        claim_id: Claim to settle
        settlement_amount_cad: Payment amount in CAD
        adjuster_id: Authorising adjuster ID (optional)
    Returns:
        payment_reference, status
    """
    logger.info(f"claims_mcp.settle_claim | claim={claim_id} amount={settlement_amount_cad}")
    # ── STUB: replace with real payment API call ──
    return {
        "claim_id": claim_id,
        "payment_reference": f"PAY-{claim_id}-001",
        "amount_cad": settlement_amount_cad,
        "status": "initiated",
    }


@mcp.tool()
def deny_claim(claim_id: str, reason: str) -> dict:
    """Record a claim denial with reason.
    Args:
        claim_id: Claim to deny
        reason: Plain language denial reason (stored for OSFI audit)
    Returns:
        status confirmation
    """
    logger.info(f"claims_mcp.deny_claim | claim={claim_id}")
    return {"claim_id": claim_id, "status": "denied", "reason": reason}


@mcp.tool()
def attach_document(claim_id: str, blob_url: str, doc_type: str) -> dict:
    """Attach an Azure Blob document to a claim record.
    Args:
        claim_id: Target claim
        blob_url: Azure Blob Storage URL
        doc_type: photo | police_report | repair_estimate | voice_transcript
    Returns:
        attachment_id confirmation
    """
    return {"claim_id": claim_id, "attachment_id": f"ATT-{claim_id}-001",
            "blob_url": blob_url, "doc_type": doc_type, "status": "attached"}


app = mcp.http_app()
