"""
workers/intake_worker.py
─────────────────────────
Intake Worker — processes claims from the intake Redis queue.

HOW IT WORKS:
  1. Long-polls the Redis intake-queue
  2. On message: create ClaimState, save to Cosmos DB
  3. Run IntakeCrew (sequential: Document → Validation → Enrichment)
  4. Save updated ClaimState with all intake results to Cosmos DB
  5. Push claim_id to Redis decision-queue
  6. Worker loops back to wait for next message

DEPLOYMENT:
  Runs as an Azure Container App.
  Container Apps auto-scales replicas based on Redis queue depth.
  During a catastrophe event (mass claims), it scales 1 → N replicas.

RUN LOCALLY:
  python -m src.workers.intake_worker
"""

import signal
import sys
import uuid
from loguru import logger

from src.config import settings
from src.crews.intake_crew import IntakeCrew
from src.messaging.redis_queue import redis_queue
from src.models import ClaimState, ClaimStage, FNOLPayload
from src.observability.mlflow_tracker import ClaimRunTracker
from src.repositories.cosmos_repository import CosmosRepository

_running = True


def _handle_sigterm(*_) -> None:
    global _running
    logger.info("worker.shutdown.signal")
    _running = False


def process_message(message: dict, repo: CosmosRepository, crew: IntakeCrew) -> None:
    """Process a single intake queue message end-to-end."""
    claim_id = message.get("claim_id") or str(uuid.uuid4())[:8].upper()
    fnol_data = message.get("fnol", {})

    with ClaimRunTracker(claim_id, "intake") as tracker:

        # Step 1 — Create ClaimState
        fnol = FNOLPayload(**fnol_data)
        state = ClaimState(id=claim_id, claim_id=claim_id, fnol=fnol)
        state.log("intake-worker", "claim_received", {"channel": fnol.channel})
        repo.save(state)

        # Step 2 — Mark as running
        state.stage = ClaimStage.INTAKE_RUNNING
        state.log("intake-worker", "intake_started")
        repo.save(state)

        # Step 3 — Run the crew
        result = crew.run(state)

        # Step 4 — Update state with all results
        state.extracted_fields = result.extracted_fields
        state.policy_coverage  = result.policy_coverage
        state.enrichment       = result.enrichment
        state.stage = ClaimStage.INTAKE_COMPLETE
        state.log("intake-crew", "intake_complete", {
            "confidence": result.extracted_fields.extraction_confidence,
            "is_covered": result.policy_coverage.is_covered,
            "prior_claims": result.enrichment.prior_claims_count_24mo,
        })
        repo.save(state)

        # Step 5 — Enqueue for decision
        redis_queue.enqueue_decision(claim_id)

        tracker.log("extraction_confidence", result.extracted_fields.extraction_confidence)
        tracker.log("is_covered", int(result.policy_coverage.is_covered))
        tracker.log("prior_claims_24mo", result.enrichment.prior_claims_count_24mo)

        logger.info(f"intake.complete | claim={claim_id} stage={state.stage.value}")


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_sigterm)
    repo = CosmosRepository()
    crew = IntakeCrew()

    logger.info("intake_worker.started")

    while _running:
        try:
            message = redis_queue.pop(redis_queue.INTAKE_QUEUE, timeout=5)
            if message:
                process_message(message, repo, crew)
        except Exception as e:
            logger.exception(f"intake.message.failed | error={e}")

    logger.info("intake_worker.stopped")
    sys.exit(0)


if __name__ == "__main__":
    main()
