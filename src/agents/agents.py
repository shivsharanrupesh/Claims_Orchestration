"""
agents/agents.py
────────────────
All 6 CrewAI agent definitions for Option B.

AGENT SUMMARY:
┌─────────────────────────┬────────────┬──────────────────┬─────────────────────┐
│ Agent                   │ Crew       │ Model            │ Tools               │
├─────────────────────────┼────────────┼──────────────────┼─────────────────────┤
│ Document Specialist     │ Intake     │ GPT-4o-mini      │ Claims MCP          │
│ Validation Specialist   │ Intake     │ GPT-4o           │ Policy MCP          │
│ Enrichment Specialist   │ Intake     │ GPT-4o-mini      │ Claims MCP          │
│ Triage Specialist       │ Decision   │ GPT-4o           │ None (reasoning)    │
│ Fraud Specialist        │ Decision   │ GPT-4o           │ Fraud MCP (ChromaDB)│
│ Routing Specialist      │ Decision   │ GPT-4o-mini      │ Workforce MCP       │
└─────────────────────────┴────────────┴──────────────────┴─────────────────────┘

WHY TWO MODEL SIZES:
  GPT-4o (pro):   reasoning-heavy tasks — policy interpretation, fraud analysis,
                  triage judgement, manager synthesis. Worth the extra cost.
  GPT-4o-mini:    structured extraction and lookup tasks — OCR post-processing,
                  weather/history enrichment, adjuster matching. Fast and cheap.
"""

from __future__ import annotations
from langchain_openai import AzureChatOpenAI
from crewai import Agent
from src.config import settings
from src.tools.mcp_adapter import load_mcp_tools


def _llm(deployment: str, temperature: float = 0.1) -> AzureChatOpenAI:
    """Create an Azure OpenAI LLM client."""
    return AzureChatOpenAI(
        azure_endpoint=settings.azure_openai.endpoint,
        api_key=settings.azure_openai.api_key,
        api_version=settings.azure_openai.api_version,
        azure_deployment=deployment,
        temperature=temperature,
        max_tokens=2000,
        timeout=60,
    )


def llm_pro(temperature: float = 0.1) -> AzureChatOpenAI:
    return _llm(settings.azure_openai.deployment_pro, temperature)

def llm_mini(temperature: float = 0.0) -> AzureChatOpenAI:
    return _llm(settings.azure_openai.deployment_mini, temperature)


# ══════════════════════════════════════════════════════════════
# INTAKE CREW AGENTS (run sequentially, each feeds the next)
# ══════════════════════════════════════════════════════════════

def document_specialist() -> Agent:
    """
    AGENT 1 — Document Specialist
    ─────────────────────────────
    PURPOSE: Extract structured facts from all FNOL artifacts
    MODEL: GPT-4o-mini (vision-capable, temperature=0.0 for consistency)
    TOOLS: Claims MCP (fetch_artifact — downloads files from Azure Blob)

    WHAT IT READS:
      - Claimant's incident summary text
      - Photo URLs (SAS links to Azure Blob) → vision analysis
      - Document URLs (police reports, repair estimates) → OCR + extraction

    WHAT IT PRODUCES: ExtractedFields
      - incident_date, incident_location, damage_description
      - estimated_loss_cad, parties_involved
      - extraction_confidence (0.0 to 1.0)

    CONFIDENCE SCORE MATTERS:
      Below 0.7 = agent is unsure → downstream crew may flag for human review
      Above 0.9 = high confidence → eligible for straight-through processing

    WHY GPT-4o-MINI:
      Vision extraction is high-volume and deterministic.
      GPT-4o-mini handles it well at ~3x lower cost.
    """
    return Agent(
        role="Document Extraction Specialist",
        goal=(
            "Extract all structured fields from the FNOL submission artifacts. "
            "Combine OCR output from documents with visual analysis of photos. "
            "Always include an extraction_confidence score between 0.0 and 1.0. "
            "Report low confidence honestly rather than guessing."
        ),
        backstory=(
            "You are an expert at reading insurance claim documents and photos. "
            "You have processed millions of FNOLs — you know how to spot "
            "key facts even in blurry photos or handwritten forms. "
            "When evidence is unclear, you flag it. A wrong extraction "
            "is far worse than an honest low-confidence report."
        ),
        llm=llm_mini(temperature=0.0),
        tools=load_mcp_tools(settings.mcp.claims_url),
        allow_delegation=False,
        verbose=True,
        max_iter=5,
    )


def validation_specialist() -> Agent:
    """
    AGENT 2 — Validation Specialist
    ─────────────────────────────────
    PURPOSE: Verify policy coverage for the specific claim
    MODEL: GPT-4o (precise policy interpretation, temperature=0.0)
    TOOLS: Policy MCP (get_policy, check_coverage, get_endorsements)

    WHAT IT READS:
      - ExtractedFields from Agent 1 (especially incident_date, damage type)
      - Calls Policy MCP to retrieve the live policy record

    WHAT IT PRODUCES: PolicyCoverage
      - is_active: is the policy valid at incident_date?
      - is_covered: does it cover this damage type?
      - deductible_cad, coverage_limit_cad
      - applicable_clauses, exclusions_triggered

    CRITICAL RULE:
      When coverage is ambiguous, the agent sets is_covered=False
      and adds a note to exclusions_triggered. It never assumes coverage.
      This protects the insurer from bad-faith arguments.

    WHY GPT-4o:
      Policy interpretation is legal work — it needs careful reasoning.
      The extra cost is worth it to avoid coverage errors.
    """
    return Agent(
        role="Policy Validation Specialist",
        goal=(
            "Verify whether this specific claim is covered under the policyholder's "
            "active policy. Cite the exact clauses that apply and any exclusions. "
            "If coverage is ambiguous, flag it for human review rather than assuming."
        ),
        backstory=(
            "You are a senior policy analyst with 20 years of experience reading "
            "insurance contracts. You interpret policy wording precisely and "
            "never speculate beyond what the contract states. "
            "You know that ambiguous coverage should go to a human, not be decided by AI."
        ),
        llm=llm_pro(temperature=0.0),
        tools=load_mcp_tools(settings.mcp.policy_url),
        allow_delegation=False,
        verbose=True,
        max_iter=5,
    )


def enrichment_specialist() -> Agent:
    """
    AGENT 3 — Enrichment Specialist
    ─────────────────────────────────
    PURPOSE: Add contextual signals that improve triage and fraud detection
    MODEL: GPT-4o-mini (factual lookups, temperature=0.0)
    TOOLS: Claims MCP (get_claim_history, weather API wrapper)

    WHAT IT READS:
      - ExtractedFields (location, date) from Agent 1
      - PolicyCoverage from Agent 2
      - Calls Claims MCP to get claimant's history

    WHAT IT PRODUCES: EnrichmentBundle
      - weather_at_incident: "heavy snowfall, -8°C, reduced visibility"
      - geocode: city, province, coordinates
      - claimant_history_summary: "2 prior auto claims in 18 months"
      - prior_claims_count_24mo: integer count

    WHY THIS MATTERS FOR DOWNSTREAM AGENTS:
      Triage: "collision during heavy snowfall" → different severity than
              "collision on a clear day" for same damage estimate
      Fraud: "3 claims in 18 months, same repair shop" → fraud signal
    """
    return Agent(
        role="Context Enrichment Specialist",
        goal=(
            "Add situational context to the claim: weather at incident, "
            "geocoded location, and the claimant's 24-month claim history. "
            "Be precise and factual. Pull only relevant data."
        ),
        backstory=(
            "You build the situational picture around a claim. "
            "You know that context changes everything — the same $5,000 "
            "damage estimate tells a different story in a hailstorm vs on a "
            "clear summer day. You pull just the signals that matter."
        ),
        llm=llm_mini(temperature=0.0),
        tools=load_mcp_tools(settings.mcp.claims_url),
        allow_delegation=False,
        verbose=True,
        max_iter=4,
    )


# ══════════════════════════════════════════════════════════════
# DECISION CREW AGENTS (called by Manager Agent as needed)
# ══════════════════════════════════════════════════════════════

def triage_specialist() -> Agent:
    """
    AGENT 4 — Triage Specialist
    ────────────────────────────
    PURPOSE: Score claim severity to determine the processing path
    MODEL: GPT-4o (reasoning, temperature=0.1)
    TOOLS: None — pure reasoning over the enriched claim data

    WHAT IT READS:
      - Full enriched claim context (all three intake crew outputs)
      - Called by the Manager Agent with specific instructions

    WHAT IT PRODUCES: SeverityScore
      - score: 0.0 to 1.0
      - band: "low" | "medium" | "high"
      - rationale: plain language explanation
      - requires_specialist: true if domain-expert adjuster needed

    SEVERITY BANDS:
      LOW (0.0-0.3):    minor damage, first claim, clear evidence
                        → eligible for AUTO_APPROVE
      MEDIUM (0.3-0.7): moderate damage, some ambiguity, multiple parties
                        → HUMAN_REVIEW
      HIGH (0.7-1.0):   total loss, complex liability, specialist needed
                        → SENIOR_ESCALATION

    WHY NO TOOLS:
      Triage is pure judgement. The enriched claim data is already
      in the Manager's context. No lookups needed.
    """
    return Agent(
        role="Claims Triage Specialist",
        goal=(
            "Score claim severity from 0.0 to 1.0 and classify as low/medium/high. "
            "LOW = minor, first claim, clear evidence. "
            "MEDIUM = moderate damage, some ambiguity. "
            "HIGH = total loss, complex, specialist needed. "
            "Always provide a concise rationale for your score."
        ),
        backstory=(
            "You are a senior adjuster who has triaged 50,000+ claims. "
            "You are conservative: when in doubt, you classify higher. "
            "An auto-approved claim that later turns out to be complex "
            "damages customer trust more than a slightly delayed review."
        ),
        llm=llm_pro(temperature=0.1),
        tools=[],
        allow_delegation=False,
        verbose=True,
        max_iter=3,
    )


def fraud_specialist() -> Agent:
    """
    AGENT 5 — Fraud Specialist
    ──────────────────────────
    PURPOSE: Assess fraud risk using ChromaDB RAG + pattern matching
    MODEL: GPT-4o (reasoning, temperature=0.1)
    TOOLS: Fraud MCP (query_fraud_index → ChromaDB, check_blocklist, score_network)

    WHAT IT READS:
      - Enriched claim context from Manager
      - Queries ChromaDB via Fraud MCP for similar historical fraud patterns
      - Checks blocklist for claimant and providers

    WHAT IT PRODUCES: FraudIndicators
      - risk_score: 0.0 to 1.0
      - behavioural_flags, network_flags, document_flags
      - rag_citations: REQUIRED — every flag needs cited evidence

    HOW RAG WORKS HERE:
      1. Agent sends query: "third auto claim in 18 months, same body shop"
      2. Fraud MCP calls ChromaDB.search() with that query
      3. ChromaDB finds 5 most similar historical fraud cases
      4. Agent receives the matching text as context
      5. Agent reasons: "This matches fraud pattern: provider ring (similarity 0.87)"
      6. Agent includes the matched case as evidence in rag_citations

    OSFI COMPLIANCE:
      The output guardrail rejects any FraudIndicators where
      requires_human_review=True but rag_citations is empty.
      No accusation without cited evidence.
    """
    return Agent(
        role="Insurance Fraud Specialist",
        goal=(
            "Assess fraud risk by searching the historical fraud pattern database "
            "via the Fraud MCP tools. Use RAG search with ChromaDB. "
            "Score risk 0.0 to 1.0. "
            "EVERY flag must include a cited evidence source from the RAG results. "
            "Never make an unsupported fraud accusation."
        ),
        backstory=(
            "You are a fraud investigator with deep knowledge of staged collisions, "
            "inflated repair invoices, ghost broker schemes, and provider rings. "
            "You know that false positives ruin innocent customers' lives. "
            "When patterns are suspicious but inconclusive, you recommend "
            "human review — not automatic denial."
        ),
        llm=llm_pro(temperature=0.1),
        tools=load_mcp_tools(settings.mcp.fraud_url),
        allow_delegation=False,
        verbose=True,
        max_iter=6,
    )


def routing_specialist() -> Agent:
    """
    AGENT 6 — Routing Specialist
    ─────────────────────────────
    PURPOSE: Assign the right adjuster when human review is needed
    MODEL: GPT-4o-mini (structured lookup, temperature=0.0)
    TOOLS: Workforce MCP (list_available_adjusters, assign_claim)

    CALLED ONLY WHEN:
      Manager decides HUMAN_REVIEW or SENIOR_ESCALATION path.
      For AUTO_APPROVE and DENY, this agent is skipped entirely.

    WHAT IT READS:
      - Severity band, claim type, province, language requirement
      - Queries Workforce MCP for available adjusters

    WHAT IT PRODUCES: assigned_adjuster_id
      - Best match based on: skill × availability × current workload
      - Formally registers the assignment in the WFM system

    MATCHING LOGIC:
      - claim_type must match adjuster specialty
      - province must match adjuster jurisdiction
      - If language=fr (Quebec), must find French-speaking adjuster
      - Checks conflict of interest (adjuster can't handle own claims)
    """
    return Agent(
        role="Claims Routing Specialist",
        goal=(
            "Assign the best available adjuster for this claim based on "
            "claim type, complexity, province, language, and current workload. "
            "Use the Workforce MCP tools. Return the assigned_adjuster_id."
        ),
        backstory=(
            "You manage a team of 200 adjusters across Canada. "
            "You know who specialises in what, who has capacity today, "
            "and how to balance workload while matching skills to claim complexity."
        ),
        llm=llm_mini(temperature=0.0),
        tools=load_mcp_tools(settings.mcp.workforce_url),
        allow_delegation=False,
        verbose=True,
        max_iter=4,
    )
