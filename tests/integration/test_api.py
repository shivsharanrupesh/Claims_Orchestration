"""
tests/integration/test_api.py
──────────────────────────────
Integration tests for the FNOL API.

These tests spin up a real FastAPI TestClient but mock out all Azure services
(Cosmos DB, Redis) so they run without any cloud infrastructure.

WHAT IS TESTED:
  - POST /claims/submit: correct 202 response, Cosmos save called, Redis push called
  - GET  /claims/{id}:   correct status returned from mocked Cosmos
  - GET  /claims:        list endpoint returns correct shape
  - POST /webhooks/slack: Slack HITL callback updates Cosmos and enqueues settlement
  - GET  /health:        liveness probe returns ok + redis status

WHAT IS NOT TESTED HERE (covered by unit tests or eval harness):
  - AI agent quality (eval harness)
  - Actual Cosmos DB reads/writes (cosmos_repository unit tests)
  - Actual Redis operations (redis_queue unit tests)
"""

from __future__ import annotations
import json
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.models import (
    ClaimState, ClaimStage, FNOLPayload, ChannelType,
    ClaimDecision, DecisionPath,
)
from src.repositories.cosmos_repository import ClaimNotFoundError

client = TestClient(app)


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _sample_fnol_payload() -> dict:
    return {
        "policy_number": "POL-TEST-001",
        "claimant_id": "CLT-TEST-001",
        "channel": "web",
        "incident_summary": "My car was rear-ended at a red light. Minor damage to bumper.",
        "photo_urls": [],
        "document_urls": [],
    }


def _sample_state(claim_id: str = "TST-001") -> ClaimState:
    fnol = FNOLPayload(
        policy_number="POL-TEST-001",
        claimant_id="CLT-TEST-001",
        channel=ChannelType.WEB,
        incident_summary="Test incident.",
    )
    return ClaimState(id=claim_id, claim_id=claim_id, fnol=fnol)


def _state_with_decision(claim_id: str = "TST-002") -> ClaimState:
    state = _sample_state(claim_id)
    state.stage = ClaimStage.APPROVED
    state.decision = ClaimDecision(
        decision=DecisionPath.AUTO_APPROVE,
        rationale="Low severity, clear evidence, no fraud flags.",
        settlement_amount_cad=2500.0,
        confidence=0.91,
    )
    return state


# ── POST /claims/submit ────────────────────────────────────────────────────────

class TestSubmitClaim:

    @patch("src.api.main.redis_queue")
    @patch("src.api.main.repo")
    def test_returns_202_with_claim_id(self, mock_repo, mock_redis):
        """Submitting a valid FNOL returns 202 with a claim_id."""
        mock_repo.save = MagicMock()
        mock_redis.enqueue_intake = MagicMock()

        resp = client.post("/claims/submit", json=_sample_fnol_payload())

        assert resp.status_code == 202
        data = resp.json()
        assert "claim_id" in data
        assert len(data["claim_id"]) == 8       # UUID short form
        assert data["stage"] == "received"
        assert "queued" in data["message"].lower()

    @patch("src.api.main.redis_queue")
    @patch("src.api.main.repo")
    def test_cosmos_save_called_once(self, mock_repo, mock_redis):
        """Cosmos DB save must be called exactly once per submission."""
        mock_repo.save = MagicMock()
        mock_redis.enqueue_intake = MagicMock()

        client.post("/claims/submit", json=_sample_fnol_payload())

        mock_repo.save.assert_called_once()

    @patch("src.api.main.redis_queue")
    @patch("src.api.main.repo")
    def test_redis_enqueue_called(self, mock_repo, mock_redis):
        """Redis intake queue must be populated after submission."""
        mock_repo.save = MagicMock()
        mock_redis.enqueue_intake = MagicMock()

        client.post("/claims/submit", json=_sample_fnol_payload())

        mock_redis.enqueue_intake.assert_called_once()
        args = mock_redis.enqueue_intake.call_args
        assert "claim_id" in args.kwargs or args.args

    @patch("src.api.main.redis_queue")
    @patch("src.api.main.repo")
    def test_missing_policy_number_returns_422(self, mock_repo, mock_redis):
        """Pydantic validation catches missing required fields."""
        payload = _sample_fnol_payload()
        del payload["policy_number"]

        resp = client.post("/claims/submit", json=payload)
        assert resp.status_code == 422

    @patch("src.api.main.redis_queue")
    @patch("src.api.main.repo")
    def test_missing_incident_summary_returns_422(self, mock_repo, mock_redis):
        payload = _sample_fnol_payload()
        del payload["incident_summary"]

        resp = client.post("/claims/submit", json=payload)
        assert resp.status_code == 422

    @patch("src.api.main.redis_queue")
    @patch("src.api.main.repo")
    def test_invalid_channel_returns_422(self, mock_repo, mock_redis):
        payload = _sample_fnol_payload()
        payload["channel"] = "fax"  # not a valid ChannelType

        resp = client.post("/claims/submit", json=payload)
        assert resp.status_code == 422


# ── GET /claims/{claim_id} ─────────────────────────────────────────────────────

class TestGetClaim:

    @patch("src.api.main.repo")
    def test_returns_claim_state(self, mock_repo):
        """Status endpoint returns the current stage for a known claim."""
        mock_repo.get = MagicMock(return_value=_sample_state("TST-001"))

        resp = client.get("/claims/TST-001")

        assert resp.status_code == 200
        data = resp.json()
        assert data["claim_id"] == "TST-001"
        assert data["stage"] == "received"
        assert data["decision"] is None

    @patch("src.api.main.repo")
    def test_returns_decision_when_complete(self, mock_repo):
        """When a decision has been made, it's included in the response."""
        mock_repo.get = MagicMock(return_value=_state_with_decision("TST-002"))

        resp = client.get("/claims/TST-002")

        assert resp.status_code == 200
        data = resp.json()
        assert data["stage"] == "approved"
        assert data["decision"] == "auto_approve"

    @patch("src.api.main.repo")
    def test_returns_404_for_unknown_claim(self, mock_repo):
        """Unknown claim_id returns 404."""
        mock_repo.get = MagicMock(side_effect=ClaimNotFoundError("UNKNOWN-ID"))

        resp = client.get("/claims/UNKNOWN-ID")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()


# ── GET /claims ────────────────────────────────────────────────────────────────

class TestListClaims:

    @patch("src.api.main.repo")
    def test_returns_list_of_claims(self, mock_repo):
        """List endpoint returns all claims with correct shape."""
        states = [_sample_state("TST-001"), _state_with_decision("TST-002")]
        mock_repo.get_all = MagicMock(return_value=states)

        resp = client.get("/claims")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["claim_id"] == "TST-001"
        assert data[1]["decision"] == "auto_approve"

    @patch("src.api.main.repo")
    def test_returns_empty_list_when_no_claims(self, mock_repo):
        mock_repo.get_all = MagicMock(return_value=[])
        resp = client.get("/claims")
        assert resp.status_code == 200
        assert resp.json() == []

    @patch("src.api.main.repo")
    def test_limit_parameter_respected(self, mock_repo):
        """Limit query param is passed to repository."""
        mock_repo.get_all = MagicMock(return_value=[])

        client.get("/claims?limit=10")
        mock_repo.get_all.assert_called_with(limit=10)


# ── POST /webhooks/slack ───────────────────────────────────────────────────────

class TestSlackWebhook:

    def _build_slack_payload(self, claim_id: str, action: str,
                              adjuster_id: str = "U123") -> dict:
        """Build a realistic Slack interactive message payload."""
        return {
            "payload": json.dumps({
                "claim_id": claim_id,
                "user": {"id": adjuster_id},
                "actions": [{"action_id": action, "value": f"{claim_id}|{action}"}],
            })
        }

    @patch("src.api.main.redis_queue")
    @patch("src.api.main.repo")
    def test_approve_action_enqueues_settlement_approved(self, mock_repo, mock_redis):
        """Adjuster clicking Approve → settlement queue with approved=True."""
        mock_repo.get = MagicMock(return_value=_sample_state("TST-003"))
        mock_repo.save = MagicMock()
        mock_redis.enqueue_settlement = MagicMock()

        # Disable signature validation in test
        import src.api.main as api_module
        original = api_module.settings.slack.signing_secret
        api_module.settings.slack.signing_secret = ""

        resp = client.post(
            "/webhooks/slack",
            content=json.dumps(self._build_slack_payload("TST-003", "approve")).encode(),
            headers={"content-type": "application/json",
                     "x-slack-request-timestamp": "1234",
                     "x-slack-signature": "v0=test"},
        )

        api_module.settings.slack.signing_secret = original

        assert resp.status_code == 200
        mock_redis.enqueue_settlement.assert_called_once()
        call_args = mock_redis.enqueue_settlement.call_args
        assert call_args.kwargs.get("approved") is True or \
               (call_args.args and call_args.args[1] is True)

    @patch("src.api.main.redis_queue")
    @patch("src.api.main.repo")
    def test_deny_action_enqueues_settlement_denied(self, mock_repo, mock_redis):
        """Adjuster clicking Deny → settlement queue with approved=False."""
        mock_repo.get = MagicMock(return_value=_sample_state("TST-004"))
        mock_repo.save = MagicMock()
        mock_redis.enqueue_settlement = MagicMock()

        import src.api.main as api_module
        original = api_module.settings.slack.signing_secret
        api_module.settings.slack.signing_secret = ""

        resp = client.post(
            "/webhooks/slack",
            content=json.dumps(self._build_slack_payload("TST-004", "deny")).encode(),
            headers={"content-type": "application/json",
                     "x-slack-request-timestamp": "1234",
                     "x-slack-signature": "v0=test"},
        )

        api_module.settings.slack.signing_secret = original

        assert resp.status_code == 200
        mock_redis.enqueue_settlement.assert_called_once()

    @patch("src.api.main.repo")
    def test_unknown_claim_returns_404(self, mock_repo):
        """Slack webhook for unknown claim returns 404."""
        mock_repo.get = MagicMock(side_effect=ClaimNotFoundError("GHOST"))

        import src.api.main as api_module
        original = api_module.settings.slack.signing_secret
        api_module.settings.slack.signing_secret = ""

        resp = client.post(
            "/webhooks/slack",
            content=json.dumps({
                "payload": json.dumps({
                    "claim_id": "GHOST",
                    "user": {"id": "U999"},
                    "actions": [{"action_id": "approve", "value": "GHOST|approve"}],
                })
            }).encode(),
            headers={"content-type": "application/json",
                     "x-slack-request-timestamp": "1234",
                     "x-slack-signature": "v0=test"},
        )

        api_module.settings.slack.signing_secret = original
        assert resp.status_code == 404


# ── GET /health ────────────────────────────────────────────────────────────────

class TestHealth:

    @patch("src.api.main.redis_queue")
    def test_health_returns_ok(self, mock_redis):
        """Liveness probe always returns ok."""
        mock_redis.ping = MagicMock(return_value=True)

        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    @patch("src.api.main.redis_queue")
    def test_health_reports_redis_status(self, mock_redis):
        """Health check includes Redis connectivity."""
        mock_redis.ping = MagicMock(return_value=False)

        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["redis"] is False
