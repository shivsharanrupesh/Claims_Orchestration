"""
workers/decision_worker.py
───────────────────────────
Decision Worker — processes claims from the Redis decision-queue.

HOW IT WORKS:
  1. Loads enriched ClaimState from Cosmos DB
  2. Runs DecisionCrew (hierarchical: Manager → Triage + Fraud + Routing)
  3. Routes based on decision:
     AUTO_APPROVE  → enqueue settlement (approved=True)
     DENY          → enqueue settlement (approved=False)
     HUMAN_REVIEW  → post Slack message + enqueue HITL queue
     ESCALATION    → post Slack message (priority) + enqueue HITL queue
  4. Saves updated ClaimState to Cosmos DB
"""

import signal
import sys
from loguru import logger

from src.crews.decision_crew import DecisionCrew
from src.messaging.redis_queue import redis_queue
from src.models import ClaimDecision, ClaimStage, DecisionPath
from src.observability.mlflow_tracker import ClaimRunTracker
from src.repositories.cosmos_repository import CosmosRepository
from src.tools.mcp_adapter import load_mcp_tools
from src.config import settings

_running = True


def _handle_sigterm(*_) -> None:
    global _running
    _running = False


def _post_slack(decision: ClaimDecision, claim_id: str) -> None:
    """Send adjuster review message via Slack MCP."""
    try:
        tools = load_mcp_tools(settings.mcp.slack_url)
        tool = next((t for t in tools if t.name == "post_approval_request"), None)
        if tool:
            tool._run(
                channel=settings.slack.review_channel,
                claim_id=claim_id,
                summary=decision.review_summary or decision.rationale,
                adjuster_id=decision.assigned_adjuster_id,
                actions=["approve", "modify", "deny"],
            )
    except Exception as e:
        logger.error(f"slack.post.failed | claim={claim_id} error={e}")


def process_message(message: dict, repo: CosmosRepository, crew: DecisionCrew) -> None:
    """Process a single decision queue message end-to-end."""
    claim_id = message["claim_id"]

    with ClaimRunTracker(claim_id, "decision") as tracker:
        state = repo.get(claim_id)
        state.stage = ClaimStage.DECISION_RUNNING
        state.log("decision-worker", "decision_started")
        repo.save(state)

        decision = crew.run(state)
        state.decision = decision
        state.stage = ClaimStage.DECISION_RUNNING

        state.log("decision-crew", "decision_complete", {
            "decision": decision.decision.value,
            "confidence": decision.confidence,
            "adjuster": decision.assigned_adjuster_id,
        })

        if decision.decision == DecisionPath.AUTO_APPROVE:
            state.stage = ClaimStage.APPROVED
            redis_queue.enqueue_settlement(claim_id, approved=True)

        elif decision.decision == DecisionPath.DENY:
            state.stage = ClaimStage.DENIED
            redis_queue.enqueue_settlement(claim_id, approved=False)

        elif decision.decision in (DecisionPath.HUMAN_REVIEW, DecisionPath.SENIOR_ESCALATION):
            state.stage = ClaimStage.AWAITING_REVIEW
            _post_slack(decision, claim_id)
            redis_queue.enqueue_hitl(claim_id, decision.review_summary or "")

        repo.save(state)
        tracker.log("decision", decision.decision.value)
        tracker.log("confidence", decision.confidence)
        logger.info(f"decision.complete | claim={claim_id} path={decision.decision.value}")


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_sigterm)
    repo = CosmosRepository()
    crew = DecisionCrew()
    logger.info("decision_worker.started")

    while _running:
        try:
            message = redis_queue.pop(redis_queue.DECISION_QUEUE, timeout=5)
            if message:
                process_message(message, repo, crew)
        except Exception as e:
            logger.exception(f"decision.message.failed | error={e}")

    sys.exit(0)


if __name__ == "__main__":
    main()
