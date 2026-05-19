#!/usr/bin/env bash
# scripts/deploy_container_apps.sh
# ──────────────────────────────────
# Deploys all Azure Container Apps after the Bicep infrastructure is provisioned.
#
# Usage:
#   ./scripts/deploy_container_apps.sh \
#     --resource-group rg-claims-option-b \
#     --image ghcr.io/your-org/claims-option-b:latest \
#     --env dev
#
# Prerequisites:
#   - az cli installed and logged in
#   - Container Apps Environment already provisioned (run Bicep first)
#   - Docker image already built and pushed to the registry

set -e

RESOURCE_GROUP=""
IMAGE=""
ENV="dev"

while [[ "$#" -gt 0 ]]; do
  case $1 in
    --resource-group) RESOURCE_GROUP="$2"; shift ;;
    --image)          IMAGE="$2";          shift ;;
    --env)            ENV="$2";            shift ;;
  esac
  shift
done

if [ -z "$RESOURCE_GROUP" ] || [ -z "$IMAGE" ]; then
  echo "Usage: $0 --resource-group <rg> --image <image> [--env dev|staging|prod]"
  exit 1
fi

ENV_NAME="cae-claims-orchestrator-${ENV}"
echo "Deploying to Container Apps Environment: ${ENV_NAME}"
echo "Image: ${IMAGE}"
echo ""

# ── Helper function ────────────────────────────────────────────────────────────
deploy_app() {
  local NAME=$1
  local MODULE=$2
  local PORT=${3:-8000}
  local MIN_REPLICAS=${4:-0}
  local MAX_REPLICAS=${5:-5}

  echo "Deploying ${NAME}..."

  az containerapp create \
    --name "${NAME}" \
    --resource-group "${RESOURCE_GROUP}" \
    --environment "${ENV_NAME}" \
    --image "${IMAGE}" \
    --target-port "${PORT}" \
    --ingress internal \
    --min-replicas "${MIN_REPLICAS}" \
    --max-replicas "${MAX_REPLICAS}" \
    --env-vars "SERVICE_MODULE=${MODULE}" \
    --cpu 0.5 \
    --memory 1.0Gi \
    2>/dev/null || \
  az containerapp update \
    --name "${NAME}" \
    --resource-group "${RESOURCE_GROUP}" \
    --image "${IMAGE}" \
    --set-env-vars "SERVICE_MODULE=${MODULE}"

  echo "  ✓ ${NAME} deployed"
}

# ── Deploy MCP servers (internal only — not exposed externally) ────────────────
deploy_app "claims-mcp"    "mcp_servers.claims_mcp.server:app"         8000 1 3
deploy_app "policy-mcp"    "mcp_servers.policy_mcp.server:app"         8000 1 3
deploy_app "fraud-mcp"     "mcp_servers.fraud_mcp.server:app"          8000 1 3
deploy_app "workforce-mcp" "mcp_servers.workforce_mcp.server:workforce_app" 8000 1 3
deploy_app "slack-mcp"     "mcp_servers.slack_mcp.server:app"          8000 1 2

# ── Deploy ChromaDB (persistent volume for vector store) ──────────────────────
echo "Deploying ChromaDB..."
az containerapp create \
  --name "chromadb" \
  --resource-group "${RESOURCE_GROUP}" \
  --environment "${ENV_NAME}" \
  --image "chromadb/chroma:0.5.0" \
  --target-port 8000 \
  --ingress internal \
  --min-replicas 1 \
  --max-replicas 1 \
  --cpu 0.5 \
  --memory 1.0Gi \
  2>/dev/null || true
echo "  ✓ ChromaDB deployed"

# ── Deploy FNOL API (externally accessible) ────────────────────────────────────
echo "Deploying FNOL API..."
az containerapp create \
  --name "claims-api" \
  --resource-group "${RESOURCE_GROUP}" \
  --environment "${ENV_NAME}" \
  --image "${IMAGE}" \
  --target-port 8000 \
  --ingress external \
  --min-replicas 1 \
  --max-replicas 10 \
  --env-vars "SERVICE_MODULE=api.main" \
  --cpu 0.5 \
  --memory 1.0Gi \
  2>/dev/null || \
az containerapp update \
  --name "claims-api" \
  --resource-group "${RESOURCE_GROUP}" \
  --image "${IMAGE}"
echo "  ✓ FNOL API deployed"

# ── Deploy workers (scale on Redis queue depth) ────────────────────────────────
for WORKER in "intake-worker:workers.intake_worker" "decision-worker:workers.decision_worker" "settlement-worker:workers.settlement_worker"; do
  NAME="${WORKER%%:*}"
  MODULE="${WORKER##*:}"
  echo "Deploying ${NAME}..."
  az containerapp create \
    --name "${NAME}" \
    --resource-group "${RESOURCE_GROUP}" \
    --environment "${ENV_NAME}" \
    --image "${IMAGE}" \
    --min-replicas 1 \
    --max-replicas 20 \
    --cpu 1.0 \
    --memory 2.0Gi \
    --command "python" "-m" "src.${MODULE}" \
    2>/dev/null || \
  az containerapp update \
    --name "${NAME}" \
    --resource-group "${RESOURCE_GROUP}" \
    --image "${IMAGE}"
  echo "  ✓ ${NAME} deployed"
done

echo ""
echo "╔══════════════════════════════════════╗"
echo "║  All Container Apps deployed!        ║"
echo "╚══════════════════════════════════════╝"

# Get API URL
API_URL=$(az containerapp show \
  --name claims-api \
  --resource-group "${RESOURCE_GROUP}" \
  --query properties.configuration.ingress.fqdn \
  --output tsv 2>/dev/null || echo "unknown")
echo "API URL: https://${API_URL}"
