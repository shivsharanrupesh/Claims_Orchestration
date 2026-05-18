"""
observability/mlflow_tracker.py
────────────────────────────────
MLflow experiment tracking for every claim run.

WHY MLFLOW IN OPTION B:
  ✓ Tracks every crew run: which model, which prompt version, what decision
  ✓ Azure ML hosts MLflow in the cloud — accessible at ml.azure.com
  ✓ For local dev: tracking_uri=./data/mlruns → browse at localhost:5000
  ✓ Provides regression detection: if eval metrics drop, alert fires

WHAT GETS LOGGED PER CLAIM:
  Params:  claim_id, stage, channel, decision path
  Metrics: extraction_confidence, fraud_risk_score, decision_confidence, cost_usd
  Tags:    model versions, environment

USAGE:
  with ClaimRunTracker(claim_id, "intake") as tracker:
      tracker.log("extraction_confidence", 0.92)
"""

from __future__ import annotations
from typing import Any
from loguru import logger

try:
    import mlflow
    _MLFLOW_AVAILABLE = True
except ImportError:
    _MLFLOW_AVAILABLE = False

from src.config import settings


class ClaimRunTracker:
    """Context manager that wraps one processing stage as an MLflow run."""

    def __init__(self, claim_id: str, stage: str) -> None:
        self._claim_id = claim_id
        self._stage = stage
        self._run = None

    def __enter__(self) -> "ClaimRunTracker":
        if _MLFLOW_AVAILABLE and settings.mlflow.tracking_uri:
            try:
                mlflow.set_tracking_uri(settings.mlflow.tracking_uri)
                mlflow.set_experiment(settings.mlflow.experiment)
                self._run = mlflow.start_run(
                    run_name=f"{self._claim_id}-{self._stage}"
                )
                mlflow.log_params({
                    "claim_id": self._claim_id,
                    "stage": self._stage,
                    "environment": settings.environment,
                })
            except Exception as e:
                logger.warning(f"mlflow.start_run.failed | {e}")
        return self

    def log(self, key: str, value: Any) -> None:
        """Log a metric or param."""
        if not _MLFLOW_AVAILABLE or not self._run:
            return
        try:
            if isinstance(value, (int, float)):
                mlflow.log_metric(key, float(value))
            else:
                mlflow.log_param(key, str(value))
        except Exception:
            pass

    def __exit__(self, *_) -> None:
        if _MLFLOW_AVAILABLE and self._run:
            try:
                mlflow.end_run()
            except Exception:
                pass
