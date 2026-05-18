"""Tests — run with: pytest tests/ -v"""

import pytest
from src.models import (
    ClaimState, ClaimStage, FNOLPayload, ChannelType,
    ExtractedFields, PolicyCoverage, FraudIndicators, SeverityScore,
    ClaimDecision, DecisionPath, RAGCitation
)


@pytest.fixture
def fnol():
    return FNOLPayload(
        policy_number="POL-TEST-001",
        claimant_id="CLT-001",
        incident_summary="Rear-ended at a red light on Yonge and Bloor.",
        channel=ChannelType.WEB,
    )

@pytest.fixture
def state(fnol):
    return ClaimState(id="CLM-001", claim_id="CLM-001", fnol=fnol)


class TestModels:
    def test_initial_stage(self, state):
        assert state.stage == ClaimStage.RECEIVED

    def test_audit_logging(self, state):
        state.log("test", "test_action", {"key": "val"})
        assert len(state.audit) == 1
        assert state.audit[0].actor == "test"

    def test_serialisation(self, state):
        j = state.model_dump_json()
        assert "CLM-001" in j
        assert "POL-TEST-001" in j

    def test_round_trip(self, state):
        d = state.model_dump()
        restored = ClaimState.model_validate(d)
        assert restored.claim_id == state.claim_id

    def test_extraction_confidence_bounds(self):
        with pytest.raises(Exception):
            ExtractedFields(extraction_confidence=1.5)
        with pytest.raises(Exception):
            ExtractedFields(extraction_confidence=-0.1)
        ExtractedFields(extraction_confidence=0.0)
        ExtractedFields(extraction_confidence=1.0)

    def test_fraud_indicators_rag_citation(self):
        citation = RAGCitation(
            source="chromadb:fraud-patterns",
            document_id="FRAUD-001",
            excerpt="Third claim in 18 months, same repair shop.",
            similarity_score=0.87,
        )
        fraud = FraudIndicators(
            risk_score=0.65,
            behavioural_flags=["multiple_claims"],
            rag_citations=[citation],
        )
        assert len(fraud.rag_citations) == 1
        assert fraud.rag_citations[0].similarity_score == 0.87

    def test_decision_paths(self):
        for path in DecisionPath:
            assert path.value in [
                "auto_approve", "human_review", "senior_escalation", "deny"
            ]

    def test_severity_bands(self):
        s = SeverityScore(score=0.15, band="low", rationale="minor", estimated_complexity_hours=1)
        assert s.band == "low"
        s2 = SeverityScore(score=0.85, band="high", rationale="complex", estimated_complexity_hours=8)
        assert s2.band == "high"
