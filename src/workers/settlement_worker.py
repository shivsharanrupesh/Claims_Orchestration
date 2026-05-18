"""
workers/settlement_worker.py
─────────────────────────────
Settlement Worker — finalises approved or denied claims.
"""

import signal
import sys
from loguru import logger

from src.messaging.redis_queue import redis_queue
from src.models import ClaimStage
from src.repositories.cosmos_repository import CosmosRepository
from src.tools.mcp_adapter import load_mcp_tools
from src.config import settings

_running = True


def _handle_sigterm(*_) -> None:
    global _running
    _running = False


def process_message(message: dict, repo: CosmosRepository) -> None:
    claim_id = message["claim_id"]
    approved = message.get("approved", False)
    adjuster_id = message.get("adjuster_id")

    state = repo.get(claim_id)
    claims_tools = load_mcp_tools(settings.mcp.claims_url)

    if approved and state.decision:
        tool = next((t for t in claims_tools if t.name == "settle_claim"), None)
        if tool:
            result = tool._run(
                claim_id=claim_id,
                settlement_amount_cad=state.decision.settlement_amount_cad or 0,
                adjuster_id=adjuster_id or state.decision.assigned_adjuster_id,
            )
            state.stage = ClaimStage.SETTLED
            state.log("settlement-worker", "settled", {"result": str(result)})
    else:
        reason = state.decision.rationale if state.decision else "Coverage denied"
        tool = next((t for t in claims_tools if t.name == "deny_claim"), None)
        if tool:
            tool._run(claim_id=claim_id, reason=reason)
        state.stage = ClaimStage.DENIED
        state.log("settlement-worker", "denied", {"reason": reason})

    repo.save(state)
    logger.info(f"settlement.complete | claim={claim_id} approved={approved}")


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_sigterm)
    repo = CosmosRepository()
    logger.info("settlement_worker.started")

    while _running:
        try:
            message = redis_queue.pop(redis_queue.SETTLEMENT_QUEUE, timeout=5)
            if message:
                process_message(message, repo)
        except Exception as e:
            logger.exception(f"settlement.message.failed | error={e}")

    sys.exit(0)


if __name__ == "__main__":
    main()
