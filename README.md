# AI Claims Processing Orchestrator 

> Production-grade multi-agent AI system for insurance claims.
> **CrewAI + LangChain + MCP + Azure OpenAI + Cosmos DB + Azure Blob + Azure Cache for Redis + ChromaDB**

[![CI](https://github.com/your-org/claims-option-b/actions/workflows/ci.yml/badge.svg)](https://github.com/your-org/claims-option-b/actions)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue)](https://python.org)

---

## Why Option B — Balanced

| Component | Choice | Why |
|---|---|---|
| LLM | Azure OpenAI | GPT-4o with Canadian data residency (PIPEDA) |
| Claim state DB | Azure Cosmos DB | Managed JSON store, partitioned by claim_id, automatic backups |
| Document storage | Azure Blob Storage | Claim photos and PDFs, SAS URL access, lifecycle archiving |
| Queue / messaging | Azure Cache for Redis | Managed Redis, simple FIFO queues, no Service Bus complexity |
| Vector / RAG store | ChromaDB (Container App) | Open source, saves $200-500/month vs Azure AI Search |
| Deployment | Azure Container Apps | Serverless, scale-to-zero, auto-scale on queue depth |
| Experiment tracking | Azure ML + MLflow | Prompt versioning, eval metrics, OSFI E-23 audit |

**Azure services used: 6** — the minimum needed for production data residency and reliability.

---

## Architecture overview

```
┌─────────────────────────────────────────────────────────┐
│               Claimant channels                         │
│         Web · Mobile · Voice · Broker portal            │
└────────────────────┬────────────────────────────────────┘
                     │ POST /claims/submit
                     ▼
┌─────────────────────────────────────────────────────────┐
│            FastAPI — FNOL API (Container App)           │
│    Validates → saves Cosmos DB → pushes Redis queue     │
└────────────────────┬────────────────────────────────────┘
                     │ Redis intake-queue
                     ▼
┌─────────────────────────────────────────────────────────┐
│         INTAKE CREW — CrewAI Sequential Process         │
│  1. Document Specialist  (GPT-4o-mini · Claims MCP)     │
│     Reads Azure Blob photos/PDFs → ExtractedFields      │
│  2. Validation Specialist (GPT-4o · Policy MCP)         │
│     Checks policy coverage → PolicyCoverage             │
│  3. Enrichment Specialist (GPT-4o-mini · Claims MCP)    │
│     Adds weather/history/geo → EnrichmentBundle         │
└────────────────────┬────────────────────────────────────┘
     Cosmos DB save  │ Redis decision-queue
                     ▼
┌─────────────────────────────────────────────────────────┐
│       DECISION CREW — CrewAI Hierarchical Process       │
│                                                         │
│   Manager Agent (GPT-4o) — auto-spawned by CrewAI       │
│   Reads enriched claim, delegates dynamically:          │
│                                                         │
│   ├── Triage Specialist (GPT-4o · no tools)             │
│   │     Scores severity → low/medium/high               │
│   ├── Fraud Specialist (GPT-4o · Fraud MCP)             │
│   │     RAG search ChromaDB → FraudIndicators           │
│   └── Routing Specialist (GPT-4o-mini · Workforce MCP)  │
│         Picks adjuster (only if human needed)           │
│                                                         │
│   Manager synthesises → ClaimDecision                   │
└──────────────────┬────────┬───────────────┬─────────────┘
                   │        │               │
           AUTO    │  HUMAN │  ESCALATION   │ DENY
           APPROVE │ REVIEW │               │
                   ▼        ▼               ▼
              settlement  Slack MCP    settlement
              (approved)  → adjuster   (denied)
                   │        │               │
                   └────────┴───────────────┘
                                │
                                ▼
                   Azure Cosmos DB — SETTLED / DENIED
                   Azure ML MLflow — run logged
```

---

## Repository structure

```
claims-option-b/
│
├── src/
│   ├── config.py                     # All settings from .env — import from here everywhere
│   ├── models.py                     # All Pydantic models (ClaimState, agents outputs, etc.)
│   │
│   ├── repositories/
│   │   ├── cosmos_repository.py      # Azure Cosmos DB — save/get/list ClaimState documents
│   │   └── blob_repository.py        # Azure Blob Storage — upload/download/delete claim docs
│   │
│   ├── messaging/
│   │   └── redis_queue.py            # Azure Cache for Redis — intake/decision/settlement queues
│   │
│   ├── rag/
│   │   └── chroma_rag.py             # ChromaDB RAG pipeline — ingest + search fraud patterns
│   │
│   ├── agents/
│   │   └── agents.py                 # All 6 CrewAI agent definitions with detailed docstrings
│   │
│   ├── crews/
│   │   ├── intake_crew.py            # Sequential crew: Document → Validation → Enrichment
│   │   └── decision_crew.py          # Hierarchical crew: Manager → Triage + Fraud + Routing
│   │
│   ├── tools/
│   │   └── mcp_adapter.py            # Converts MCP server tools into CrewAI BaseTool instances
│   │
│   ├── workers/
│   │   ├── intake_worker.py          # Reads Redis intake-queue → runs IntakeCrew → saves Cosmos
│   │   ├── decision_worker.py        # Reads Redis decision-queue → runs DecisionCrew → routes
│   │   └── settlement_worker.py      # Reads Redis settlement-queue → calls Claims MCP to settle
│   │
│   ├── api/
│   │   └── main.py                   # FastAPI: POST /claims/submit · GET /claims/{id} · Slack webhook
│   │
│   ├── observability/
│   │   └── mlflow_tracker.py         # MLflow context manager — tracks every crew run
│   │
│   └── mcp_servers/
│       ├── claims_mcp/server.py      # Port 8001: get_claim_history, settle_claim, deny_claim
│       ├── policy_mcp/server.py      # Port 8002: get_policy, check_coverage, get_endorsements
│       ├── fraud_mcp/server.py       # Port 8003: query_fraud_index (ChromaDB), check_blocklist
│       ├── workforce_mcp/server.py   # Port 8004: list_available_adjusters, assign_claim
│       └── slack_mcp/server.py       # Port 8005: post_approval_request, send_dm
│
├── tests/
│   ├── unit/test_models.py           # Model validation tests — no Azure needed
│   └── integration/                  # API tests with mocked Azure
│
├── scripts/
│   └── bootstrap.py                  # First-time setup: creates Cosmos containers, seeds ChromaDB
│
├── data/
│   └── sample/sample_fnol.json       # Test FNOL payload
│
├── infrastructure/bicep/             # Azure infrastructure as code (Bicep templates)
│
├── .github/workflows/ci.yml          # GitHub Actions: lint → test → build → deploy
├── Dockerfile                        # Multi-service single image
├── docker-compose.yml                # Local development stack
├── requirements.txt                  # Pinned dependencies
└── .env.example                      # All required environment variables documented
```

---

## File-by-file explanation

### `src/config.py` — The settings hub
Every environment variable is defined here as a typed Pydantic field. If a required variable is missing, the app crashes at startup with a clear error. No `os.environ` calls anywhere else in the project.

### `src/models.py` — Data contracts
All Pydantic models used by agents, repositories, and APIs. Models flow through the system:
- `FNOLPayload` → what the claimant submits
- `ExtractedFields` → Document Specialist output (confidence-gated)
- `PolicyCoverage` → Validation Specialist output (is_covered drives the decision)
- `EnrichmentBundle` → Enrichment Specialist (weather + history for triage and fraud)
- `SeverityScore` → Triage Specialist (score band: low/medium/high)
- `FraudIndicators` → Fraud Specialist (RAG citations REQUIRED per OSFI E-23)
- `ClaimDecision` → Manager synthesis (the final path)
- `ClaimState` → master Cosmos DB document (contains all of the above)

### `src/repositories/cosmos_repository.py` — State persistence
The Azure Cosmos DB client. Every agent workflow stage ends with `repo.save(state)`. The `list_by_stage()` method is used for operational monitoring (find claims stuck in a stage). The `ensure_cosmos_containers()` function is called once at bootstrap.

### `src/repositories/blob_repository.py` — Document storage
Azure Blob Storage client for claim photos and PDFs. The `get_sas_url()` method generates short-lived signed URLs that the Document Specialist uses to download files directly from Azure — no proxy through the API needed. The `delete_claim_docs()` method supports PIPEDA right-to-erasure.

### `src/messaging/redis_queue.py` — Async pipeline
Azure Cache for Redis client. Four queues: `intake-queue → decision-queue → settlement-queue + hitl-queue`. Workers use blocking pop (`BRPOP`) to wait for messages — no busy polling. LPUSH + BRPOP gives FIFO ordering without any queue framework overhead.

### `src/rag/chroma_rag.py` — The RAG pipeline (key innovation)
ChromaDB client with Azure OpenAI embeddings. Two collections: `fraud-patterns` and `policy-docs`. The `search()` method embeds a query using `text-embedding-3-small`, finds the top-K similar chunks in ChromaDB using cosine similarity, and returns them as evidence for the Fraud Specialist. The `ingest_fraud_case()` method is called from `scripts/bootstrap.py` to seed the initial fraud cases.

### `src/agents/agents.py` — All 6 agent definitions
Each agent is a function returning a `crewai.Agent`. The docstrings in this file are unusually detailed because they explain the design reasoning (why GPT-4o vs GPT-4o-mini, why no tools for Triage, etc.). Every developer who touches an agent should read its docstring first.

### `src/tools/mcp_adapter.py` — The MCP bridge
Connects to an MCP server via HTTP JSON-RPC, lists its tools, and wraps each as a `crewai.BaseTool`. The agent code never changes when a backend system is replaced — only the MCP server needs updating.

### `src/crews/intake_crew.py` — Sequential process
Builds the three tasks with `context=[...]` chaining and runs them in fixed order. Task 2 gets Task 1's output. Task 3 gets Tasks 1 and 2's outputs. All three produce typed Pydantic models via `output_pydantic=...`.

### `src/crews/decision_crew.py` — Hierarchical process
Builds a single decision task with detailed decision rules in the description. CrewAI auto-spawns a Manager Agent using `manager_llm`. The Manager decides which specialists to call. The extensive task description is essentially the Manager's "instruction set".

### `src/workers/` — The processing engines
Three workers, each polling one Redis queue. Workers are stateless — they load state from Cosmos DB, run the crew, write state back, push the next queue message, and terminate cleanly. Azure Container Apps scales them based on queue depth.

### `src/api/main.py` — Thin REST layer
Three endpoints: submit, status, list. Plus the Slack webhook for HITL callbacks. The API is intentionally simple — it validates, persists, queues, and returns. No AI runs in the API layer.

### `src/mcp_servers/` — The tool surfaces
Five FastMCP servers. Each maps to one backend system. The Fraud MCP server is the most interesting — it calls `FraudRAGPipeline.search()` which queries ChromaDB. The stubs in Claims, Policy, Workforce, and Slack MCP need replacing with real backend API calls.

---

## Azure services — what each one does

### Azure OpenAI
**What:** Hosted GPT-4o and GPT-4o-mini, plus text-embedding-3-small for RAG.
**Why Azure (not plain OpenAI):** Canadian data residency (Canada Central region). Claimant data never leaves Canada. No-training guarantee — prompts are not used to train OpenAI's models. Private VNet endpoint in production.
**Cost:** ~$0.005/1K tokens (GPT-4o-mini) to $0.015 (GPT-4o). Estimate $0.50-1.50 per claim.

### Azure Cosmos DB
**What:** JSON document database. One document per claim (the ClaimState). Partitioned by claim_id for O(1) lookups.
**Why:** Managed service — automatic backups, 99.999% SLA. JSON maps directly to our Pydantic models. Auto-indexed — query by stage or date without schema changes.
**Cost:** ~$24/month base (400 RU/s, autoscale up as needed).

### Azure Blob Storage
**What:** Object storage for claim photos, PDFs, voice transcripts.
**Why:** Files can be 5-20MB — Cosmos DB has a 2MB document limit. Blob is $0.018/GB/month. SAS URLs let agents download files directly without going through the API.
**Cost:** Negligible for typical claim volumes. ~$5-20/month.

### Azure Cache for Redis
**What:** Managed Redis instance used as a message queue between workers.
**Why:** Simpler than Service Bus (no sessions, no dead-letter, no correlation IDs). LPUSH + BRPOP gives FIFO queuing in 10 lines of Python. Azure-managed means no Redis server to maintain.
**Cost:** ~$55/month (C1 Standard tier — 1GB, 99.9% SLA).

### Azure Container Apps
**What:** Serverless container hosting for all services (API, workers, MCP servers, ChromaDB).
**Why:** Scale to zero when idle (no claims overnight = no compute cost). Auto-scale workers based on Redis queue depth — catastrophe events handled automatically. No Kubernetes cluster to manage.
**Cost:** ~$0 when idle. ~$50-150/month at moderate claim volumes.

### Azure ML + MLflow
**What:** Experiment tracking for every crew run. Logs prompt versions, model versions, metrics (confidence scores, decisions, cost per claim).
**Why:** OSFI E-23 requires auditable model decisions. MLflow provides a UI to browse all runs, compare prompt versions, and detect regressions.
**Cost:** ~$0 for compute (MLflow server included in Azure ML workspace).

### ChromaDB (open source, hosted on Container App)
**What:** Vector database for RAG. Stores fraud pattern embeddings. Searched by the Fraud Specialist.
**Why NOT Azure AI Search:** ChromaDB costs ~$20-30/month on Container Apps vs $200-500/month for Azure AI Search Standard. For the fraud pattern use case (5K-50K documents), ChromaDB performs identically.
**Cost:** ~$20-30/month (Container App with persistent storage).

---

## Setup — 5 steps

### Step 1: Clone the repo
```bash
git clone https://github.com/your-org/claims-option-b
cd claims-option-b
pip install -e ".[dev]"
```

### Step 2: Configure environment
```bash
cp .env.example .env
# Edit .env — set all Azure connection strings
# The minimum you need:
#   AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY
#   COSMOS_ENDPOINT + COSMOS_KEY
#   AZURE_STORAGE_CONNECTION_STRING
#   REDIS_HOST + REDIS_PASSWORD
```

### Step 3: Bootstrap Azure resources
```bash
python scripts/bootstrap.py
# Creates Cosmos DB container, seeds ChromaDB with sample fraud patterns
```

### Step 4: Start local development stack
```bash
docker compose up
# Starts ChromaDB + all 5 MCP servers + API + 3 workers
```

### Step 5: Submit a test claim
```bash
curl -X POST http://localhost:8080/claims/submit \
  -H "Content-Type: application/json" \
  -d @data/sample/sample_fnol.json

# Poll for result:
curl http://localhost:8080/claims/{CLAIM_ID}

# View API docs:
open http://localhost:8080/docs

# View MLflow experiments:
mlflow ui --backend-store-uri ./data/mlruns
# open http://localhost:5000
```

---

## Running tests
```bash
pytest tests/ -v
```

Unit tests run without any Azure services — they only test model validation.

---

## Deployment to Azure

### Prerequisites
- Azure CLI installed and logged in
- Resource group with: Container Apps environment, Cosmos DB, Blob Storage, Redis Cache, Azure OpenAI, Azure ML workspace

### Deploy all services
```bash
# Push the Docker image to GHCR (done automatically by CI/CD)
# Or manually:
docker build -t ghcr.io/your-org/claims-option-b:latest .
docker push ghcr.io/your-org/claims-option-b:latest

# The CI/CD pipeline deploys all Container Apps automatically on push to main
# See .github/workflows/ci.yml for details
```

---

## Compliance

| Requirement | How it's met |
|---|---|
| PIPEDA data residency | All Azure services in Canada Central. Azure OpenAI private endpoint. |
| OSFI E-23 audit | Every decision logged to `ClaimState.audit` in Cosmos DB with model_version and rationale. MLflow tracks all runs. |
| Fraud evidence citations | `FraudIndicators.rag_citations` is required — every fraud flag must cite a ChromaDB search result. |
| FINTRAC | Settlements > $10,000 CAD routed to SENIOR_ESCALATION automatically by the Manager Agent's decision rules. |

---

*Built by the Claims AI Platform Team. Questions? Open an issue or ping #claims-ai on Slack.*
