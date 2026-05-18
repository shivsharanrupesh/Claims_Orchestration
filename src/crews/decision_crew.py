"""
crews/decision_crew.py
───────────────────────
The Decision Crew — hierarchical process.

WHAT IT DOES:
  Given the fully enriched claim, decides what to do with it.
  A Manager Agent dynamically delegates to the three specialists:
  - Triage Specialist (always called)
  - Fraud Specialist (called for medium/high severity or any suspicion)
  - Routing Specialist (called only if path requires human involvement)

WHY HIERARCHICAL (not sequential):
  The processing path depends on what the Manager learns from each specialist:
  - If Triage says LOW severity → Manager may skip Fraud (no red flags)
  - If Fraud finds patterns → Manager escalates to senior regardless of severity
  - If AUTO_APPROVE → Manager skips Routing (no adjuster needed)

  A sequential process couldn't make these conditional decisions.
  The Manager's dynamic delegation is the key feature.

HOW CREWAI HIERARCHICAL WORKS:
  1. You define one high-level task (the decision task)
  2. You provide the specialist agents
  3. CrewAI auto-spawns a Manager Agent (using manager_llm)
  4. Manager reads the task + all enriched context
  5. Manager decides which specialists to call and when
  6. Manager synthesises their outputs into the final answer

THE MANAGER IS NOT YOU:
  The Manager Agent is auto-created by CrewAI. You don't write it.
  You control it by choosing manager_llm (GPT-4o) and writing
  the decision task description very clearly.

AFTER THE CREW RUNS:
  The caller (decision worker) reads the ClaimDecision and routes:
  AUTO_APPROVE → enqueue settlement
  HUMAN_REVIEW → post Slack message, enqueue hitl-queue
  SENIOR_ESCALATION → post Slack message (priority), enqueue hitl-queue
  DENY → enqueue settlement (approved=False)
"""

from __future__ import annotations
from loguru import logger
from crewai import Crew, Task, Process

from src.agents.agents import (
    triage_specialist,
    fraud_specialist,
    routing_specialist,
    llm_pro,
)
from src.models import ClaimDecision, ClaimState, DecisionPath


class DecisionCrew:
    """Builds and runs the hierarchical decision crew."""

    def run(self, state: ClaimState) -> ClaimDecision:
        """
        Execute the hierarchical decision crew.

        Args:
            state: Enriched ClaimState (stage should be DECISION_RUNNING)
                   Must have extracted_fields, policy_coverage, enrichment populated

        Returns:
            ClaimDecision with the final path and full rationale
        """
        # ── Prepare context strings ───────────────────────────────────
        extracted  = state.extracted_fields.model_dump_json()  if state.extracted_fields  else "{}"
        coverage   = state.policy_coverage.model_dump_json()   if state.policy_coverage   else "{}"
        enrichment = state.enrichment.model_dump_json()        if state.enrichment        else "{}"

        # ── Build agents ──────────────────────────────────────────────
        triage  = triage_specialist()
        fraud   = fraud_specialist()
        routing = routing_specialist()

        # ── Single decision task ──────────────────────────────────────
        decision_task = Task(
            description=f"""
                Make the final claim decision for claim {state.claim_id}.

                === CLAIM CONTEXT ===

                Extracted fields from documents:
                {extracted}

                Policy coverage determination:
                {coverage}

                Enrichment context (weather, history, location):
                {enrichment}

                === YOUR JOB ===

                As the Claims Decision Manager, delegate to your specialists:

                ALWAYS delegate to the Triage Specialist first to score severity.

                DELEGATE to Fraud Specialist if ANY of these are true:
                  - prior_claims_count_24mo >= 2
                  - estimated_loss_cad > 15000
                  - damage_description mentions unusual circumstances
                  - extraction_confidence < 0.8

                DELEGATE to Routing Specialist ONLY IF you decide the path
                is human_review or senior_escalation.

                === DECISION RULES ===

                AUTO_APPROVE only if ALL of these:
                  - severity_band = "low" AND severity_score < 0.3
                  - fraud risk_score < 0.2 (or fraud not run)
                  - policy is_covered = true
                  - estimated_loss_cad > deductible_cad

                HUMAN_REVIEW if:
                  - severity_band = "medium"
                  - OR fraud risk_score between 0.2 and 0.6
                  - OR extraction_confidence < 0.7

                SENIOR_ESCALATION if:
                  - severity_band = "high"
                  - OR fraud risk_score > 0.6
                  - OR estimated_loss_cad > 50000
                  - OR requires_specialist = true

                DENY only if:
                  - policy is_covered = false
                  - Must cite the specific exclusion clause

                For human_review and senior_escalation:
                  - Include a 3-sentence review_summary for the adjuster's Slack message
                  - Include the assigned_adjuster_id from the Routing Specialist

                Return JSON matching the ClaimDecision schema.
            """,
            expected_output=(
                "JSON matching ClaimDecision schema with: "
                "decision (auto_approve|human_review|senior_escalation|deny), "
                "rationale, settlement_amount_cad (or null), "
                "assigned_adjuster_id (or null), confidence (0.0-1.0), "
                "review_summary (3-sentence Slack message or null)."
            ),
            output_pydantic=ClaimDecision,
        )

        # ── Run hierarchical crew ─────────────────────────────────────
        crew = Crew(
            agents=[triage, fraud, routing],
            tasks=[decision_task],
            process=Process.hierarchical,
            manager_llm=llm_pro(temperature=0.1),
            memory=True,
            verbose=True,
        )

        logger.info(f"decision_crew.start | claim={state.claim_id}")
        crew.kickoff()
        logger.info(f"decision_crew.complete | claim={state.claim_id}")

        decision: ClaimDecision = decision_task.output.pydantic
        return decision
