"""
messaging/redis_queue.py
────────────────────────
Message queue using Azure Cache for Redis.

WHY REDIS (NOT AZURE SERVICE BUS) IN OPTION B:
  ✓ Much simpler API — push/pop vs sessions/dead-letter/correlation-ids
  ✓ Azure Cache for Redis is fully managed (like Service Bus) — just simpler
  ✓ Redis lists give you FIFO queuing natively
  ✓ Atomic operations — no double-processing of messages
  ✓ Same managed Redis if you want to scale to multiple servers

HOW IT WORKS:
  - Workers use LPUSH to add a message to the LEFT of a list
  - Workers use BRPOP (blocking right-pop) to wait for messages
  - This gives FIFO (first in, first out) ordering
  - If a worker crashes, the message is gone — for OPTION B that's fine
    (production upgrade: use Redis Streams for durable delivery)

QUEUES:
  intake-queue    → intake worker processes raw FNOL
  decision-queue  → decision worker processes enriched claim
  settlement-queue→ settlement worker finalises the claim
  hitl-queue      → adjuster review pending (Slack sent)
"""

from __future__ import annotations
import json
from typing import Any, Optional
from loguru import logger
import redis

from src.config import settings


class RedisQueue:
    """
    Simple message queue backed by Azure Cache for Redis.
    All workers share this instance.
    """

    INTAKE_QUEUE     = "intake-queue"
    DECISION_QUEUE   = "decision-queue"
    SETTLEMENT_QUEUE = "settlement-queue"
    HITL_QUEUE       = "hitl-queue"

    def __init__(self) -> None:
        self._r = redis.Redis(
            host=settings.redis.host,
            port=settings.redis.port,
            password=settings.redis.password,
            ssl=settings.redis.ssl,
            decode_responses=True,
        )

    def push(self, queue_name: str, payload: dict[str, Any]) -> None:
        """Add a message to the LEFT of the queue (newest first for LPUSH)."""
        self._r.lpush(queue_name, json.dumps(payload))
        logger.info(f"redis.push | queue={queue_name} claim={payload.get('claim_id')}")

    def pop(self, queue_name: str, timeout: int = 5) -> Optional[dict[str, Any]]:
        """
        Wait up to `timeout` seconds for a message.
        Returns None if no message arrives within timeout.

        BRPOP pops from the RIGHT — combined with LPUSH gives FIFO ordering.
        """
        result = self._r.brpop(queue_name, timeout=timeout)
        if result is None:
            return None
        _, message = result
        return json.loads(message)

    def queue_length(self, queue_name: str) -> int:
        """How many messages are waiting. Used for monitoring."""
        return self._r.llen(queue_name)

    # ── Convenience methods for each stage ────────────────────────────────

    def enqueue_intake(self, claim_id: str, fnol_data: dict) -> None:
        self.push(self.INTAKE_QUEUE, {"claim_id": claim_id, "fnol": fnol_data})

    def enqueue_decision(self, claim_id: str) -> None:
        self.push(self.DECISION_QUEUE, {"claim_id": claim_id})

    def enqueue_settlement(self, claim_id: str, approved: bool,
                           adjuster_id: str | None = None) -> None:
        self.push(self.SETTLEMENT_QUEUE, {
            "claim_id": claim_id,
            "approved": approved,
            "adjuster_id": adjuster_id,
        })

    def enqueue_hitl(self, claim_id: str, summary: str) -> None:
        self.push(self.HITL_QUEUE, {"claim_id": claim_id, "summary": summary})

    def ping(self) -> bool:
        """Health check — returns True if Redis is reachable."""
        try:
            return self._r.ping()
        except Exception:
            return False


# Single shared instance
redis_queue = RedisQueue()
