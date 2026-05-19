"""
observability/tracing.py
────────────────────────
Azure Application Insights — distributed tracing across all workers.

HOW IT WORKS:
  Every worker and the API call setup_tracing() at startup.
  This configures OpenTelemetry to export spans to App Insights.
  All spans from all services are linked by a correlation_id,
  so you can see the full journey of one claim across all pods
  in a single App Insights Transaction Diagnostics view.

WHY APP INSIGHTS IN OPTION B:
  Azure Monitor is included in the Container Apps environment.
  You get distributed tracing across all workers for near-zero cost.
  The alternative (self-hosted Jaeger or Zipkin) would need its own container.

FOR LOCAL DEV:
  Set APPLICATIONINSIGHTS_CONNECTION_STRING="" to disable.
  Traces are logged to stdout instead.
"""

from __future__ import annotations
from loguru import logger


def setup_tracing(service_name: str) -> None:
    """
    Configure OpenTelemetry exporter for Azure App Insights.

    Call once at the top of each worker's main() and in the FastAPI lifespan.
    Safe to call multiple times — subsequent calls are no-ops.

    Args:
        service_name: Identifies this service in the trace (e.g. "intake-worker")
    """
    from src.config import settings

    conn_str = settings.appinsights_connection_string
    if not conn_str:
        logger.info(f"tracing.disabled | service={service_name} (no App Insights connection string)")
        return

    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
        configure_azure_monitor(
            connection_string=conn_str,
            service_name=service_name,
        )
        logger.info(f"tracing.enabled | service={service_name}")
    except Exception as e:
        logger.warning(f"tracing.setup.failed | service={service_name} error={e}")
