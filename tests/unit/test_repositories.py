"""
tests/unit/test_repositories.py
────────────────────────────────
Unit tests for the repository layer.

Tests cosmos_repository and blob_repository using mocked Azure SDK clients.
No real Azure credentials needed.
"""

from __future__ import annotations
import json
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

from src.models import ClaimState, ClaimStage, FNOLPayload, ChannelType
from src.repositories.cosmos_repository import CosmosRepository, ClaimNotFoundError


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _make_state(claim_id: str = "TEST-001") -> ClaimState:
    fnol = FNOLPayload(
        policy_number="POL-001",
        claimant_id="CLT-001",
        channel=ChannelType.WEB,
        incident_summary="Test claim.",
    )
    return ClaimState(id=claim_id, claim_id=claim_id, fnol=fnol)


# ── CosmosRepository ───────────────────────────────────────────────────────────

class TestCosmosRepository:

    @patch("src.repositories.cosmos_repository.CosmosClient")
    def _make_repo(self, mock_cosmos_cls):
        """Build a CosmosRepository with a mocked CosmosClient."""
        mock_client = MagicMock()
        mock_cosmos_cls.return_value = mock_client
        mock_db = MagicMock()
        mock_client.get_database_client.return_value = mock_db
        mock_container = MagicMock()
        mock_db.get_container_client.return_value = mock_container
        return CosmosRepository(), mock_container

    def test_save_calls_upsert(self):
        """save() must call upsert_item on the container."""
        with patch("src.repositories.cosmos_repository.CosmosClient") as mock_cls:
            mock_container = MagicMock()
            mock_cls.return_value.get_database_client.return_value \
                .get_container_client.return_value = mock_container

            state = _make_state()
            mock_container.upsert_item.return_value = json.loads(state.model_dump_json())

            repo = CosmosRepository()
            result = repo.save(state)

            mock_container.upsert_item.assert_called_once()
            assert result.claim_id == "TEST-001"

    def test_get_returns_claim_state(self):
        """get() deserialises the Cosmos document back into ClaimState."""
        with patch("src.repositories.cosmos_repository.CosmosClient") as mock_cls:
            mock_container = MagicMock()
            mock_cls.return_value.get_database_client.return_value \
                .get_container_client.return_value = mock_container

            state = _make_state("FETCH-001")
            mock_container.read_item.return_value = json.loads(state.model_dump_json())

            repo = CosmosRepository()
            result = repo.get("FETCH-001")

            assert result.claim_id == "FETCH-001"
            assert result.stage == ClaimStage.RECEIVED

    def test_get_raises_claim_not_found(self):
        """get() raises ClaimNotFoundError for missing documents."""
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        with patch("src.repositories.cosmos_repository.CosmosClient") as mock_cls:
            mock_container = MagicMock()
            mock_cls.return_value.get_database_client.return_value \
                .get_container_client.return_value = mock_container
            mock_container.read_item.side_effect = \
                CosmosResourceNotFoundError(message="Not found")

            repo = CosmosRepository()
            with pytest.raises(ClaimNotFoundError) as exc_info:
                repo.get("MISSING-001")
            assert exc_info.value.claim_id == "MISSING-001"

    def test_get_or_none_returns_none_when_missing(self):
        """get_or_none() returns None instead of raising."""
        with patch("src.repositories.cosmos_repository.CosmosClient") as mock_cls:
            mock_container = MagicMock()
            mock_cls.return_value.get_database_client.return_value \
                .get_container_client.return_value = mock_container

            repo = CosmosRepository()
            # Patch the get() method directly
            repo.get = MagicMock(side_effect=ClaimNotFoundError("NONE-001"))

            result = repo.get_or_none("NONE-001")
            assert result is None

    def test_update_stage_transitions_correctly(self):
        """update_stage() loads, changes stage, adds audit entry, saves."""
        with patch("src.repositories.cosmos_repository.CosmosClient") as mock_cls:
            mock_container = MagicMock()
            mock_cls.return_value.get_database_client.return_value \
                .get_container_client.return_value = mock_container

            state = _make_state("STAGE-001")
            saved_state = state.model_copy(deep=True)
            saved_state.stage = ClaimStage.INTAKE_RUNNING

            mock_container.read_item.return_value = json.loads(state.model_dump_json())
            mock_container.upsert_item.return_value = json.loads(saved_state.model_dump_json())

            repo = CosmosRepository()
            result = repo.update_stage("STAGE-001", ClaimStage.INTAKE_RUNNING)

            assert result.stage == ClaimStage.INTAKE_RUNNING
            mock_container.upsert_item.assert_called_once()


# ── ClaimState audit trail ─────────────────────────────────────────────────────

class TestAuditTrail:

    def test_log_appends_entry(self):
        state = _make_state()
        state.log("test-actor", "test-action", {"k": "v"})
        assert len(state.audit) == 1
        assert state.audit[0].actor == "test-actor"
        assert state.audit[0].detail == {"k": "v"}

    def test_log_updates_timestamp(self):
        state = _make_state()
        before = state.updated_at
        state.log("actor", "action")
        assert state.updated_at >= before

    def test_multiple_logs_accumulate(self):
        state = _make_state()
        for i in range(5):
            state.log(f"actor-{i}", f"action-{i}")
        assert len(state.audit) == 5

    def test_audit_entries_are_ordered(self):
        state = _make_state()
        state.log("first", "first_action")
        state.log("second", "second_action")
        assert state.audit[0].actor == "first"
        assert state.audit[1].actor == "second"
