"""
api/main.py
───────────
FastAPI application — the FNOL ingestion API.

ENDPOINTS:
  POST /claims/submit       → submit a new FNOL claim (returns claim_id)
  GET  /claims/{claim_id}   → check claim status
  GET  /claims              → list all claims (for the dashboard)
  POST /webhooks/slack      → Slack HITL callback (adjuster decision)
  GET  /health              → liveness probe for Container Apps

DESIGN:
  The API is intentionally thin — it validates, persists, queues, and returns.
  All AI processing happens in the workers asynchronously.
  This keeps the API fast (<100ms) and independently scalable.
"""

from __future__ import annotations
import json
import uuid
import hashlib
import hmac
from typing import Optional
from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel
from loguru import logger

from src.config import settings
from src.messaging.redis_queue import redis_queue
from src.models import ClaimState, ClaimStage, FNOLPayload, ChannelType
from src.repositories.cosmos_repository import CosmosRepository, ClaimNotFoundError

app = FastAPI(
    title="Claims Orchestrator — Option B API",
    description="AI-powered insurance claims processing (Balanced Architecture)",
    version="1.0.0",
)

repo = CosmosRepository()


# ══════════════════════════════════════════════════════════════
# REQUEST / RESPONSE SCHEMAS
# ══════════════════════════════════════════════════════════════

class FNOLRequest(BaseModel):
    """What the claimant (or their portal) sends to POST /claims/submit."""
    policy_number: str
    claimant_id: str
    channel: ChannelType = ChannelType.WEB
    incident_summary: str
    photo_urls: list[str] = []
    document_urls: list[str] = []
    voice_transcript_url: Optional[str] = None


class ClaimStatusResponse(BaseModel):
    claim_id: str
    stage: str
    decision: Optional[str] = None
    assigned_adjuster_id: Optional[str] = None
    message: str


# ══════════════════════════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════════════════════════

@app.post(
    "/claims/submit",
    response_model=ClaimStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a new FNOL claim",
)
async def submit_claim(request: FNOLRequest) -> ClaimStatusResponse:
    """
    Accept a First Notice of Loss submission.

    Returns 202 Accepted immediately — AI processing is async.
    Use GET /claims/{claim_id} to poll for the decision.
    """
    claim_id = str(uuid.uuid4())[:8].upper()
    fnol = FNOLPayload(**request.model_dump())

    state = ClaimState(id=claim_id, claim_id=claim_id, fnol=fnol)
    state.log("api", "fnol_received", {"channel": fnol.channel.value})
    repo.save(state)

    redis_queue.enqueue_intake(
        claim_id=claim_id,
        fnol_data=json.loads(fnol.model_dump_json()),
    )

    logger.info(f"api.submit | claim={claim_id} policy={fnol.policy_number}")

    return ClaimStatusResponse(
        claim_id=claim_id,
        stage=ClaimStage.RECEIVED.value,
        message="Claim received and queued for processing.",
    )


@app.get(
    "/claims/{claim_id}",
    response_model=ClaimStatusResponse,
    summary="Get claim status",
)
async def get_claim_status(claim_id: str) -> ClaimStatusResponse:
    """Check the current processing stage and decision for a claim."""
    try:
        state = repo.get(claim_id)
    except ClaimNotFoundError:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")

    return ClaimStatusResponse(
        claim_id=state.claim_id,
        stage=state.stage.value,
        decision=state.decision.decision.value if state.decision else None,
        assigned_adjuster_id=state.decision.assigned_adjuster_id if state.decision else None,
        message=f"Current stage: {state.stage.value}",
    )


@app.get("/claims", summary="List all claims")
async def list_claims(limit: int = 50) -> list[dict]:
    """Return recent claims — for the dashboard or monitoring."""
    claims = repo.get_all(limit=limit)
    return [
        {
            "claim_id": c.claim_id,
            "stage": c.stage.value,
            "decision": c.decision.decision.value if c.decision else None,
            "policy_number": c.fnol.policy_number,
            "channel": c.fnol.channel.value,
            "updated_at": c.updated_at.isoformat(),
        }
        for c in claims
    ]


@app.post("/webhooks/slack", summary="Slack HITL adjuster callback")
async def slack_webhook(request: Request) -> dict:
    """
    Called by Slack when an adjuster clicks Approve / Modify / Deny.

    This is the HITL resume point:
      1. Validate Slack HMAC signature
      2. Extract claim_id and adjuster decision
      3. Update Cosmos DB
      4. Push to settlement-queue to resume processing
    """
    body = await request.body()
    timestamp = request.headers.get("x-slack-request-timestamp", "")
    signature = request.headers.get("x-slack-signature", "")

    if settings.slack.signing_secret:
        base = f"v0:{timestamp}:{body.decode()}"
        computed = "v0=" + hmac.new(
            settings.slack.signing_secret.encode(),
            base.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(computed, signature):
            raise HTTPException(status_code=401, detail="Invalid Slack signature")

    data = json.loads(body)
    payload = json.loads(data.get("payload", "{}"))

    claim_id = payload.get("claim_id") or payload["actions"][0]["value"].split("|")[0]
    action = payload["actions"][0]["action_id"]
    adjuster_id = payload["user"]["id"]

    try:
        state = repo.get(claim_id)
    except ClaimNotFoundError:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")

    approved = action == "approve"
    state.log("adjuster", f"adjuster_{action}", {"adjuster_id": adjuster_id})
    repo.save(state)

    redis_queue.enqueue_settlement(
        claim_id=claim_id,
        approved=approved,
        adjuster_id=adjuster_id,
    )

    logger.info(f"slack.webhook | claim={claim_id} action={action} adjuster={adjuster_id}")
    return {"response_action": "clear"}


@app.get("/health")
async def health() -> dict:
    """Liveness probe for Azure Container Apps."""
    return {
        "status": "ok",
        "redis": redis_queue.ping(),
    }
