"""
repositories/cosmos_repository.py
──────────────────────────────────
Azure Cosmos DB repository for ClaimState documents.

WHY COSMOS DB IN OPTION B:
  ✓ Managed service — no DB admin, automatic backups
  ✓ JSON documents — ClaimState maps directly, no ORM needed
  ✓ Partitioned by claim_id — O(1) lookups
  ✓ Canada Central region — PIPEDA data residency
  ✓ Auto-indexing — query by stage, date, claimant_id out of the box
  ✓ Change feed — future event-driven architectures built on top

PATTERN: Every crew run ends with a Cosmos upsert.
  This is the durable checkpoint. If a worker crashes, the
  next worker loads the latest state and continues from there.
"""

from __future__ import annotations
import json
from typing import Optional
from loguru import logger
from azure.cosmos import CosmosClient, PartitionKey
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.config import settings
from src.models import ClaimState, ClaimStage


class CosmosRepository:
    """
    CRUD operations on the Cosmos DB claim-state container.

    Each claim = one JSON document, with claim_id as the partition key.
    Documents are ~5-50KB depending on audit trail length.
    """

    def __init__(self) -> None:
        client = CosmosClient(
            url=settings.cosmos.endpoint,
            credential=settings.cosmos.key,
        )
        db = client.get_database_client(settings.cosmos.database)
        self._container = db.get_container_client(settings.cosmos.container)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=0.5, max=4),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    def save(self, state: ClaimState) -> ClaimState:
        """
        Upsert (insert or update) a ClaimState document.
        Called after every stage transition.

        IDEMPOTENT: calling save() twice with the same state
        is safe — the second call overwrites the first.
        """
        body = json.loads(state.model_dump_json())
        result = self._container.upsert_item(body=body)
        logger.info(f"cosmos.save | claim={state.claim_id} stage={state.stage.value}")
        return ClaimState.model_validate(result)

    def get(self, claim_id: str) -> ClaimState:
        """
        Load a claim by ID.
        Raises ClaimNotFoundError if it doesn't exist.
        """
        try:
            item = self._container.read_item(
                item=claim_id, partition_key=claim_id
            )
            return ClaimState.model_validate(item)
        except CosmosResourceNotFoundError:
            raise ClaimNotFoundError(claim_id)

    def get_or_none(self, claim_id: str) -> Optional[ClaimState]:
        """Load a claim, returns None if not found (no exception)."""
        try:
            return self.get(claim_id)
        except ClaimNotFoundError:
            return None

    def update_stage(self, claim_id: str, new_stage: ClaimStage,
                     actor: str = "orchestrator") -> ClaimState:
        """
        Convenience method: load → update stage → save.
        Adds an audit entry automatically.
        """
        state = self.get(claim_id)
        old_stage = state.stage
        state.stage = new_stage
        state.log(actor=actor, action="stage_transition",
                  detail={"from": old_stage.value, "to": new_stage.value})
        return self.save(state)

    def list_by_stage(self, stage: ClaimStage, limit: int = 100) -> list[ClaimState]:
        """
        List claims in a given stage.
        Useful for monitoring: find claims stuck in intake_running.
        """
        query = "SELECT * FROM c WHERE c.stage = @stage ORDER BY c.updated_at DESC"
        params = [{"name": "@stage", "value": stage.value}]
        items = self._container.query_items(
            query=query, parameters=params,
            enable_cross_partition_query=True,
            max_item_count=limit,
        )
        return [ClaimState.model_validate(i) for i in items]

    def get_all(self, limit: int = 200) -> list[ClaimState]:
        """Return recent claims — for the dashboard."""
        items = self._container.query_items(
            query="SELECT * FROM c ORDER BY c.updated_at DESC",
            enable_cross_partition_query=True,
            max_item_count=limit,
        )
        return [ClaimState.model_validate(i) for i in items]


class ClaimNotFoundError(Exception):
    def __init__(self, claim_id: str):
        super().__init__(f"Claim not found: {claim_id}")
        self.claim_id = claim_id


def ensure_cosmos_containers() -> None:
    """
    Bootstrap helper — creates the Cosmos database and container if missing.
    Run once at first deploy via: python scripts/bootstrap.py
    """
    client = CosmosClient(settings.cosmos.endpoint, settings.cosmos.key)
    db = client.create_database_if_not_exists(settings.cosmos.database)
    db.create_container_if_not_exists(
        id=settings.cosmos.container,
        partition_key=PartitionKey(path="/claim_id"),
        offer_throughput=400,
    )
    logger.info("cosmos.containers.ready")
