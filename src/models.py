"""
models.py
─────────
All Pydantic data models used across the project.

THINK OF THESE AS CONTRACTS:
  - Every agent reads some fields and writes others
  - Every Azure service (Cosmos, Redis, Blob) stores these shapes
  - If an agent produces a bad output, Pydantic validation catches it

MODEL HIERARCHY:
  FNOLPayload                   ← what the claimant submits
      └── stored in ClaimState
  ExtractedFields               ← Document Specialist output
  PolicyCoverage                ← Validation Specialist output
  EnrichmentBundle              ← Enrichment Specialist output
  SeverityScore                 ← Triage Specialist output
  FraudIndicators               ← Fraud Specialist output (with RAG citations)
  ClaimDecision                 ← Manager Agent final synthesis
  ClaimState                    ← master document in Cosmos DB (contains all above)
"""

from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


# ══════════════════════════════════════════════════════════════
# ENUMS — Define all possible values upfront
# ══════════════════════════════════════════════════════════════

class ClaimStage(str, Enum):
    """
    The claim's position in the processing pipeline.
    This is the 'stage' field in Cosmos DB.
    Every stage transition triggers a Cosmos write.
    """
    RECEIVED          = "received"           # just arrived at the API
    INTAKE_RUNNING    = "intake_running"     # intake crew is working
    INTAKE_COMPLETE   = "intake_complete"    # ready for decision crew
    DECISION_RUNNING  = "decision_running"   # decision crew is working
    AWAITING_REVIEW   = "awaiting_review"    # waiting for adjuster in Slack
    APPROVED          = "approved"           # auto-approved, awaiting settlement
    DENIED            = "denied"             # denied, reason recorded
    SETTLED           = "settled"            # payment triggered, case closed
    FAILED            = "failed"             # error occurred, needs investigation


class DecisionPath(str, Enum):
    """
    The four outcomes the Manager Agent can choose.
    AUTO_APPROVE = AI handles it entirely (low risk, clear evidence)
    HUMAN_REVIEW = adjuster reviews via Slack (medium complexity)
    SENIOR_ESCALATION = senior adjuster gets it (high risk / high value)
    DENY = policy does not cover this claim (with cited reason)
    """
    AUTO_APPROVE      = "auto_approve"
    HUMAN_REVIEW      = "human_review"
    SENIOR_ESCALATION = "senior_escalation"
    DENY              = "deny"


class ChannelType(str, Enum):
    """How the claimant submitted the FNOL."""
    WEB    = "web"
    MOBILE = "mobile"
    VOICE  = "voice"
    BROKER = "broker"


# ══════════════════════════════════════════════════════════════
# INPUT MODELS
# ══════════════════════════════════════════════════════════════

class FNOLPayload(BaseModel):
    """
    First Notice of Loss — raw submission from the claimant.
    This is exactly what arrives at POST /claims/submit.

    photo_urls and document_urls point to Azure Blob Storage paths.
    """
    policy_number: str
    claimant_id: str
    channel: ChannelType = ChannelType.WEB
    incident_summary: str = Field(..., max_length=4000)
    photo_urls: list[str] = Field(default_factory=list)
    document_urls: list[str] = Field(default_factory=list)
    voice_transcript_url: Optional[str] = None
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════
# AGENT OUTPUT MODELS — each agent writes exactly one of these
# ══════════════════════════════════════════════════════════════

class ExtractedFields(BaseModel):
    """
    Document Specialist output.
    Facts pulled from photos, PDFs, and voice transcripts.

    extraction_confidence is critical:
      - Below 0.7: agent flags low confidence, downstream crew may request human review
      - Above 0.9: high confidence, safe for auto-processing
    """
    incident_date: Optional[str]        = None
    incident_location: Optional[str]    = None
    damage_description: Optional[str]   = None
    estimated_loss_cad: Optional[float] = Field(None, ge=0)
    parties_involved: list[str]         = Field(default_factory=list)
    photos_analyzed: int                = Field(0, ge=0)
    documents_analyzed: int             = Field(0, ge=0)
    extraction_confidence: float        = Field(0.0, ge=0.0, le=1.0)
    blob_uris: list[str]                = Field(default_factory=list,
        description="Azure Blob URIs of analyzed documents stored for audit")


class PolicyCoverage(BaseModel):
    """
    Validation Specialist output.
    Determines if this claim is actually covered by the policy.

    is_covered = False → Manager will choose DENY path
    exclusions_triggered → specific clauses that block coverage
    """
    is_active: bool                  = False
    is_covered: bool                 = False
    deductible_cad: float            = Field(0.0, ge=0)
    coverage_limit_cad: float        = Field(0.0, ge=0)
    applicable_clauses: list[str]    = Field(default_factory=list)
    exclusions_triggered: list[str]  = Field(default_factory=list)
    policy_effective_date: Optional[str] = None
    policy_expiry_date: Optional[str]    = None


class EnrichmentBundle(BaseModel):
    """
    Enrichment Specialist output.
    Contextual signals that improve triage accuracy and fraud detection.

    prior_claims_count_24mo is a key fraud signal:
      - 0-1: normal
      - 2+: reviewed by Fraud Specialist
      - 3+: almost always triggers Fraud Specialist deep-dive
    """
    weather_at_incident: Optional[str]        = None
    geocode: Optional[dict[str, Any]]         = None
    claimant_history_summary: Optional[str]   = None
    prior_claims_count_24mo: int              = 0
    location_risk_score: Optional[float]      = Field(None, ge=0.0, le=1.0)


class RAGCitation(BaseModel):
    """
    A single piece of evidence from the ChromaDB fraud index.
    Every fraud flag MUST include at least one citation.
    This is required by OSFI E-23: no accusation without evidence.
    """
    source: str          # e.g. "chromadb:fraud-patterns"
    document_id: str
    excerpt: str         = Field(..., max_length=500)
    similarity_score: float = Field(..., ge=0.0, le=1.0)


class FraudIndicators(BaseModel):
    """
    Fraud Specialist output.
    Produced after RAG search against ChromaDB fraud patterns.

    risk_score thresholds:
      0.0-0.2: clean → eligible for AUTO_APPROVE
      0.2-0.6: suspicious → HUMAN_REVIEW
      0.6+:    high risk → SENIOR_ESCALATION
    """
    risk_score: float          = Field(0.0, ge=0.0, le=1.0)
    behavioural_flags: list[str]  = Field(default_factory=list)
    network_flags: list[str]      = Field(default_factory=list)
    document_flags: list[str]     = Field(default_factory=list)
    rag_citations: list[RAGCitation] = Field(default_factory=list,
        description="Evidence from ChromaDB search — required for any flag")
    requires_human_review: bool = False


class SeverityScore(BaseModel):
    """
    Triage Specialist output.
    How serious is this claim? Determines the processing path.

    band thresholds:
      low:    score 0.0-0.3 → eligible for AUTO_APPROVE
      medium: score 0.3-0.7 → HUMAN_REVIEW
      high:   score 0.7-1.0 → SENIOR_ESCALATION
    """
    score: float                  = Field(0.0, ge=0.0, le=1.0)
    band: str                     = "low"
    rationale: str                = ""
    requires_specialist: bool     = False
    estimated_complexity_hours: float = Field(1.0, ge=0)


class ClaimDecision(BaseModel):
    """
    Manager Agent final synthesis.
    This is the output that drives the claim to its outcome.

    review_summary is shown to the adjuster in the Slack HITL message.
    settlement_amount_cad is passed to the Claims MCP settle_claim tool.
    """
    decision: DecisionPath
    rationale: str                         = ""
    settlement_amount_cad: Optional[float] = Field(None, ge=0)
    assigned_adjuster_id: Optional[str]    = None
    confidence: float                      = Field(0.5, ge=0.0, le=1.0)
    review_summary: Optional[str]          = None


# ══════════════════════════════════════════════════════════════
# AUDIT
# ══════════════════════════════════════════════════════════════

class AuditEntry(BaseModel):
    """
    One row in the audit trail.
    Every agent action, stage change, and human decision is logged here.
    Required by OSFI E-23 model risk management.
    """
    timestamp: datetime   = Field(default_factory=lambda: datetime.now(timezone.utc))
    actor: str            = Field(..., description="agent role, worker name, or human user_id")
    action: str
    detail: dict[str, Any] = Field(default_factory=dict)
    model_version: Optional[str]  = None
    prompt_version: Optional[str] = None


# ══════════════════════════════════════════════════════════════
# MASTER DOCUMENT — stored in Azure Cosmos DB
# ══════════════════════════════════════════════════════════════

class ClaimState(BaseModel):
    """
    The master claim document stored in Azure Cosmos DB.
    Partitioned by claim_id for fast retrieval.

    This is the single source of truth for a claim.
    Every worker reads this, mutates it, and writes it back.

    WHY COSMOS DB:
      - JSON document storage — perfect for this nested structure
      - Automatic indexing — query by stage, date, claimant_id
      - Managed backups — no DBA needed
      - Canada Central region — PIPEDA compliant
    """
    id: str          = Field(..., description="Cosmos DB doc id, same as claim_id")
    claim_id: str
    stage: ClaimStage = ClaimStage.RECEIVED
    fnol: FNOLPayload

    # Intake crew outputs (filled in order by agents 1, 2, 3)
    extracted_fields: Optional[ExtractedFields] = None
    policy_coverage:  Optional[PolicyCoverage]  = None
    enrichment:       Optional[EnrichmentBundle] = None

    # Decision crew outputs
    severity:  Optional[SeverityScore]    = None
    fraud:     Optional[FraudIndicators]  = None
    decision:  Optional[ClaimDecision]    = None

    # Metadata
    audit:      list[AuditEntry] = Field(default_factory=list)
    errors:     list[str]        = Field(default_factory=list)
    cost_usd:   float            = Field(0.0, ge=0)
    created_at: datetime         = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime         = Field(default_factory=lambda: datetime.now(timezone.utc))

    def log(self, actor: str, action: str,
            detail: dict | None = None,
            model_version: str | None = None) -> None:
        """Append an audit entry. Call this on every meaningful state change."""
        self.audit.append(AuditEntry(
            actor=actor, action=action,
            detail=detail or {},
            model_version=model_version,
        ))
        self.updated_at = datetime.now(timezone.utc)
