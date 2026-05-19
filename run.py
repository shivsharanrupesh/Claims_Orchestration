"""
run.py
───────
Start the entire Option B stack locally with one command.

Usage:
    python run.py

What this starts:
  Port 8000 — ChromaDB (vector store for RAG)
  Port 8001 — Claims MCP server
  Port 8002 — Policy MCP server
  Port 8003 — Fraud MCP server
  Port 8004 — Workforce MCP server
  Port 8005 — Slack MCP server
  Port 8080 — FastAPI FNOL API
  Background — Intake worker (polls Redis)
  Background — Decision worker (polls Redis)
  Background — Settlement worker (polls Redis)

Prerequisites:
  pip install -r requirements.txt
  cp .env.example .env  # fill in your Azure connection strings
  python scripts/bootstrap.py  # first time only

Try it:
  curl -X POST http://localhost:8080/claims/submit \\
    -H "Content-Type: application/json" \\
    -d @data/sample/sample_fnol.json

  curl http://localhost:8080/claims/{CLAIM_ID}

  open http://localhost:8080/docs   # interactive API docs
"""

import subprocess
import sys
import time
import signal
from pathlib import Path
from loguru import logger


SERVICES = [
    # (name, module_path, port)
    ("ChromaDB",        None,                                                8000),  # special case
    ("Claims MCP",      "src.mcp_servers.claims_mcp.server:app",            8001),
    ("Policy MCP",      "src.mcp_servers.policy_mcp.server:app",            8002),
    ("Fraud MCP",       "src.mcp_servers.fraud_mcp.server:app",             8003),
    ("Workforce MCP",   "src.mcp_servers.workforce_mcp.server:workforce_app", 8004),
    ("Slack MCP",       "src.mcp_servers.slack_mcp.server:app",             8005),
    ("FNOL API",        "src.api.main:app",                                 8080),
]

WORKERS = [
    ("Intake worker",     "src.workers.intake_worker"),
    ("Decision worker",   "src.workers.decision_worker"),
    ("Settlement worker", "src.workers.settlement_worker"),
]


def start_uvicorn(name: str, module: str, port: int) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", module,
         "--host", "0.0.0.0", "--port", str(port), "--log-level", "warning"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    logger.info(f"  started {name} on port {port} (PID {proc.pid})")
    return proc


def start_chroma() -> subprocess.Popen:
    """Start ChromaDB in server mode."""
    Path("data/chroma").mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [sys.executable, "-m", "chromadb.cli.cli", "run",
         "--path", "data/chroma", "--host", "0.0.0.0", "--port", "8000"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    logger.info(f"  started ChromaDB on port 8000 (PID {proc.pid})")
    return proc


def start_worker(name: str, module: str) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-m", module],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    logger.info(f"  started {name} (PID {proc.pid})")
    return proc


def main() -> None:
    Path("data").mkdir(exist_ok=True)

    logger.info("\n╔══════════════════════════════════════════════╗")
    logger.info("║  Claims Orchestrator — Option B              ║")
    logger.info("║  Starting all services...                    ║")
    logger.info("╚══════════════════════════════════════════════╝\n")

    procs = []

    # ── Start services ────────────────────────────────────────────────
    logger.info("Starting infrastructure and MCP servers:")
    for name, module, port in SERVICES:
        try:
            if name == "ChromaDB":
                p = start_chroma()
            else:
                p = start_uvicorn(name, module, port)
            procs.append(p)
        except Exception as e:
            logger.error(f"  failed to start {name}: {e}")

    logger.info("\nWaiting 4 seconds for servers to initialise...")
    time.sleep(4)

    # ── Start workers ─────────────────────────────────────────────────
    logger.info("\nStarting background workers:")
    for name, module in WORKERS:
        try:
            p = start_worker(name, module)
            procs.append(p)
        except Exception as e:
            logger.error(f"  failed to start {name}: {e}")

    logger.info("\n╔══════════════════════════════════════════════╗")
    logger.info("║  All services started!                       ║")
    logger.info("║                                              ║")
    logger.info("║  API:    http://localhost:8080               ║")
    logger.info("║  Docs:   http://localhost:8080/docs          ║")
    logger.info("║  Health: http://localhost:8080/health        ║")
    logger.info("╚══════════════════════════════════════════════╝\n")
    logger.info("Press Ctrl+C to stop all services.\n")

    def shutdown(*_):
        logger.info("\nShutting down all services...")
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # ── Keep alive ────────────────────────────────────────────────────
    while True:
        time.sleep(1)
        # Restart any crashed service
        for i, p in enumerate(procs):
            if p.poll() is not None:
                logger.warning(f"Process {p.pid} exited with code {p.returncode}")


if __name__ == "__main__":
    main()
