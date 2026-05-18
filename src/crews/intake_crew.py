"""
crews/intake_crew.py
─────────────────────
The Intake Crew — sequential process.

WHAT IT DOES:
  Takes a raw FNOL claim and enriches it into a fully understood claim.
  Three agents run in fixed order, each passing their output to the next.

  Agent 1 → Document Specialist → ExtractedFields
  Agent 2 → Validation Specialist → PolicyCoverage  (gets Agent 1 output as context)
  Agent 3 → Enrichment Specialist → EnrichmentBundle (gets Agents 1+2 as context)

WHY SEQUENTIAL (not parallel or hierarchical):
  The tasks are genuinely dependent:
  - Validation NEEDS the incident_date from extraction to check if policy was active
  - Enrichment NEEDS the incident_location from extraction for the weather API
  Running them in parallel would break these dependencies.

CONTEXT PASSING IN CREWAI:
  The `context=[previous_task]` parameter tells CrewAI to pass the
  previous task's output as additional context to the next task.
  This is CrewAI's built-in mechanism — no manual state passing needed.

AFTER THE CREW RUNS:
  The caller (intake worker) writes all three outputs to ClaimState in Cosmos DB,
  then pushes to the Redis decision-queue.
"""

from __future__ import annotations
from dataclasses import dataclass
from loguru import logger
from crewai import Crew, Task, Process

from src.agents.agents import (
    document_specialist,
    validation_specialist,
    enrichment_specialist,
)
from src.models import ClaimState, ExtractedFields, PolicyCoverage, EnrichmentBundle


@dataclass
class IntakeResult:
    """Typed container for all three intake crew outputs."""
    extracted_fields: ExtractedFields
    policy_coverage: PolicyCoverage
    enrichment: EnrichmentBundle


class IntakeCrew:
    """Builds and runs the sequential intake crew."""

    def run(self, state: ClaimState) -> IntakeResult:
        """
        Execute the sequential crew for a single claim.

        Args:
            state: Current ClaimState (stage should be INTAKE_RUNNING)

        Returns:
            IntakeResult with all three agent outputs typed and validated
        """
        fnol = state.fnol

        # ── Build agents ──────────────────────────────────────────────
        doc_agent = document_specialist()
        val_agent = validation_specialist()
        enr_agent = enrichment_specialist()

        # ── Build tasks ───────────────────────────────────────────────
        # Task 1 — Document Extraction
        extract_task = Task(
            description=f"""
                Extract structured fields from this FNOL submission.

                Claim ID: {state.claim_id}
                Policy number: {fnol.policy_number}
                Claimant ID: {fnol.claimant_id}
                Channel: {fnol.channel}
                Incident summary: {fnol.incident_summary}
                Photo URLs (Azure Blob SAS links): {fnol.photo_urls or 'none provided'}
                Document URLs (Azure Blob SAS links): {fnol.document_urls or 'none provided'}

                Instructions:
                - Use the Claims MCP tool to fetch and analyze each artifact
                - For photos: perform visual damage assessment
                - For PDFs: extract text and structured data (OCR)
                - Report extraction_confidence honestly (0.0=guessing, 1.0=certain)

                Return a JSON object matching the ExtractedFields schema.
            """,
            expected_output=(
                "JSON with: incident_date, incident_location, damage_description, "
                "estimated_loss_cad, parties_involved, extraction_confidence (0.0-1.0). "
                "Null for fields where evidence is insufficient."
            ),
            agent=doc_agent,
            output_pydantic=ExtractedFields,
        )

        # Task 2 — Policy Validation (uses Task 1 output as context)
        validate_task = Task(
            description=f"""
                Verify policy coverage for this claim.

                Policy number: {fnol.policy_number}
                Claimant ID: {fnol.claimant_id}

                Use the extracted fields from the previous task (especially
                incident_date and damage_description) to:
                1. Confirm the policy was active at the incident date
                2. Determine which coverage clauses apply
                3. Check for triggered exclusions
                4. Return deductible and coverage limit

                If coverage is ambiguous, set is_covered=False and explain in exclusions_triggered.
                Return a JSON object matching the PolicyCoverage schema.
            """,
            expected_output=(
                "JSON with: is_active, is_covered, deductible_cad, coverage_limit_cad, "
                "applicable_clauses (list), exclusions_triggered (list)."
            ),
            agent=val_agent,
            context=[extract_task],
            output_pydantic=PolicyCoverage,
        )

        # Task 3 — Enrichment (uses Tasks 1 and 2 as context)
        enrich_task = Task(
            description=f"""
                Add contextual information to this claim.

                Claimant ID: {fnol.claimant_id}

                Using the incident location and date from earlier tasks:
                1. Retrieve weather conditions at the incident location and date
                2. Geocode the incident location (city, province)
                3. Get the claimant's claim history for the past 24 months

                This context will be used by the Triage and Fraud Specialists.
                Return a JSON object matching the EnrichmentBundle schema.
            """,
            expected_output=(
                "JSON with: weather_at_incident (string), geocode (dict), "
                "claimant_history_summary (string), prior_claims_count_24mo (int)."
            ),
            agent=enr_agent,
            context=[extract_task, validate_task],
            output_pydantic=EnrichmentBundle,
        )

        # ── Run the crew ──────────────────────────────────────────────
        crew = Crew(
            agents=[doc_agent, val_agent, enr_agent],
            tasks=[extract_task, validate_task, enrich_task],
            process=Process.sequential,
            memory=True,      # agents can reference each other's earlier outputs
            verbose=True,
        )

        logger.info(f"intake_crew.start | claim={state.claim_id}")
        crew.kickoff(inputs={"claim_id": state.claim_id})
        logger.info(f"intake_crew.complete | claim={state.claim_id}")

        # ── Extract typed outputs ─────────────────────────────────────
        extracted: ExtractedFields = extract_task.output.pydantic
        coverage: PolicyCoverage = validate_task.output.pydantic
        enrichment: EnrichmentBundle = enrich_task.output.pydantic

        return IntakeResult(
            extracted_fields=extracted,
            policy_coverage=coverage,
            enrichment=enrichment,
        )
