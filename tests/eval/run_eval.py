"""
tests/eval/run_eval.py
───────────────────────
Evaluation harness — measures the quality of the Decision Crew
against a labelled holdout set of historical claims.

WHAT IT MEASURES:
  - Decision accuracy (did the AI pick the same path as the expert adjuster?)
  - Triage band accuracy (low/medium/high match rate)
  - Fraud detection precision and recall
  - Auto-approval rate for confirmed low-severity claims
  - Average confidence score per decision path

WHY THIS MATTERS:
  This runs automatically in the CI/CD pipeline after every deployment.
  A deployment is blocked if any metric drops more than 5 percentage points
  below the established baseline.

  This is also how you satisfy OSFI E-23 model risk management:
  you have documented, reproducible evidence that the model performs
  at the expected level before every production change.

HOW TO RUN:
  python -m tests.eval.run_eval --eval-set data/eval/claims_eval.jsonl
  python -m tests.eval.run_eval --eval-set data/eval/claims_eval.jsonl --baseline mlruns/baseline.json

EVAL SET FORMAT (data/eval/claims_eval.jsonl):
  One JSON record per line. Each record has:
  - All fields needed to reconstruct a ClaimState with intake results
  - expected_decision: the correct DecisionPath value
  - expected_severity_band: "low" | "medium" | "high"
  - expected_fraud: true | false
"""

from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.config import settings
from src.crews.decision_crew import DecisionCrew
from src.models import (
    ClaimState, ClaimStage, FNOLPayload, ChannelType,
    ExtractedFields, PolicyCoverage, EnrichmentBundle,
    DecisionPath,
)


# ── Result tracking ────────────────────────────────────────────────────────────

@dataclass
class EvalResult:
    total: int = 0
    decision_correct: int = 0
    severity_correct: int = 0
    fraud_tp: int = 0       # true positive — correctly flagged
    fraud_fp: int = 0       # false positive — wrongly flagged
    fraud_fn: int = 0       # false negative — missed fraud
    auto_approve_on_low: int = 0
    low_count: int = 0
    confidence_sum: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def decision_accuracy(self) -> float:
        return self.decision_correct / self.total if self.total else 0.0

    @property
    def severity_accuracy(self) -> float:
        return self.severity_correct / self.total if self.total else 0.0

    @property
    def fraud_precision(self) -> float:
        denom = self.fraud_tp + self.fraud_fp
        return self.fraud_tp / denom if denom else 0.0

    @property
    def fraud_recall(self) -> float:
        denom = self.fraud_tp + self.fraud_fn
        return self.fraud_tp / denom if denom else 0.0

    @property
    def auto_approve_rate_on_low(self) -> float:
        return self.auto_approve_on_low / self.low_count if self.low_count else 0.0

    @property
    def avg_confidence(self) -> float:
        return self.confidence_sum / self.total if self.total else 0.0

    def summary(self) -> dict:
        return {
            "total_claims": self.total,
            "decision_accuracy": round(self.decision_accuracy, 4),
            "severity_accuracy": round(self.severity_accuracy, 4),
            "fraud_precision": round(self.fraud_precision, 4),
            "fraud_recall": round(self.fraud_recall, 4),
            "auto_approve_rate_on_low_severity": round(self.auto_approve_rate_on_low, 4),
            "avg_decision_confidence": round(self.avg_confidence, 4),
            "errors": len(self.errors),
        }


# ── Eval data loading ──────────────────────────────────────────────────────────

def load_eval_set(path: Path) -> list[dict]:
    records = []
    with path.open() as f:
        for i, line in enumerate(f):
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as e:
                    logger.warning(f"Skipping line {i+1}: {e}")
    return records


def build_state_from_record(record: dict) -> ClaimState:
    """Reconstruct a ClaimState from an eval record."""
    fnol = FNOLPayload(
        policy_number=record.get("policy_number", "POL-EVAL"),
        claimant_id=record.get("claimant_id", "CLT-EVAL"),
        channel=ChannelType.WEB,
        incident_summary=record.get("incident_summary", ""),
    )
    state = ClaimState(
        id=record["claim_id"],
        claim_id=record["claim_id"],
        stage=ClaimStage.DECISION_RUNNING,
        fnol=fnol,
    )
    if record.get("extracted_fields"):
        state.extracted_fields = ExtractedFields(**record["extracted_fields"])
    if record.get("policy_coverage"):
        state.policy_coverage = PolicyCoverage(**record["policy_coverage"])
    if record.get("enrichment"):
        state.enrichment = EnrichmentBundle(**record["enrichment"])
    return state


# ── Main eval loop ─────────────────────────────────────────────────────────────

def run_eval(eval_path: Path) -> EvalResult:
    records = load_eval_set(eval_path)
    if not records:
        logger.error(f"No records found in {eval_path}")
        return EvalResult()

    logger.info(f"Running eval on {len(records)} claims...")
    crew = DecisionCrew()
    result = EvalResult()

    for i, record in enumerate(records):
        claim_id = record.get("claim_id", f"EVAL-{i:04d}")
        logger.info(f"  [{i+1}/{len(records)}] {claim_id}")

        try:
            state = build_state_from_record(record)
            decision = crew.run(state)

            result.total += 1
            result.confidence_sum += decision.confidence

            # Decision accuracy
            expected_decision = record.get("expected_decision")
            if expected_decision and decision.decision.value == expected_decision:
                result.decision_correct += 1

            # Severity accuracy (from state.severity if set by triage)
            expected_band = record.get("expected_severity_band")
            if expected_band and state.severity and state.severity.band == expected_band:
                result.severity_correct += 1

            # Auto-approve rate on confirmed low-severity claims
            if expected_band == "low":
                result.low_count += 1
                if decision.decision == DecisionPath.AUTO_APPROVE:
                    result.auto_approve_on_low += 1

            # Fraud detection metrics
            expected_fraud: bool = record.get("expected_fraud", False)
            is_flagged = (state.fraud and state.fraud.risk_score > 0.5)
            if expected_fraud and is_flagged:
                result.fraud_tp += 1
            elif not expected_fraud and is_flagged:
                result.fraud_fp += 1
            elif expected_fraud and not is_flagged:
                result.fraud_fn += 1

        except Exception as e:
            logger.error(f"  Error on {claim_id}: {e}")
            result.errors.append(f"{claim_id}: {e}")

    return result


def check_against_baseline(current: dict, baseline_path: Optional[Path],
                             threshold: float = 0.05) -> bool:
    """
    Returns True if all metrics are within threshold of baseline.
    Used by CI/CD to gate deployment.
    """
    if not baseline_path or not baseline_path.exists():
        logger.warning("No baseline found — skipping regression check")
        return True

    baseline = json.loads(baseline_path.read_text())
    all_pass = True
    key_metrics = [
        "decision_accuracy", "fraud_precision", "fraud_recall",
        "auto_approve_rate_on_low_severity"
    ]

    for metric in key_metrics:
        current_val = current.get(metric, 0.0)
        baseline_val = baseline.get(metric, 0.0)
        drop = baseline_val - current_val

        if drop > threshold:
            logger.error(
                f"REGRESSION: {metric} dropped {drop:.2%} "
                f"(baseline={baseline_val:.4f}, current={current_val:.4f})"
            )
            all_pass = False
        else:
            logger.info(f"  OK: {metric} = {current_val:.4f} (baseline={baseline_val:.4f})")

    return all_pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Claims Decision Crew eval harness")
    parser.add_argument("--eval-set", type=Path, required=True,
                        help="Path to .jsonl eval set")
    parser.add_argument("--baseline", type=Path, default=None,
                        help="Path to baseline metrics JSON for regression check")
    parser.add_argument("--output", type=Path, default=None,
                        help="Save metrics to this JSON file")
    parser.add_argument("--log-mlflow", action="store_true",
                        help="Log metrics to MLflow")
    args = parser.parse_args()

    result = run_eval(args.eval_set)
    summary = result.summary()

    logger.info("\n═══════════════ EVAL RESULTS ═══════════════")
    for k, v in summary.items():
        logger.info(f"  {k:40s}: {v}")
    logger.info("══════════════════════════════════════════════")

    if args.output:
        args.output.write_text(json.dumps(summary, indent=2))
        logger.info(f"Metrics saved to {args.output}")

    if args.log_mlflow:
        try:
            import mlflow
            mlflow.set_tracking_uri(settings.mlflow.tracking_uri)
            mlflow.set_experiment(settings.mlflow.experiment)
            with mlflow.start_run(run_name="eval-run"):
                for k, v in summary.items():
                    if isinstance(v, (int, float)):
                        mlflow.log_metric(k, v)
            logger.info("Metrics logged to MLflow")
        except Exception as e:
            logger.warning(f"MLflow logging failed: {e}")

    passes = check_against_baseline(summary, args.baseline)
    if not passes:
        logger.error("Eval FAILED — regression detected. Blocking deployment.")
        sys.exit(1)
    else:
        logger.info("Eval PASSED — all metrics within acceptable range.")


if __name__ == "__main__":
    main()
