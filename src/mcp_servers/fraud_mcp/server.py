"""
mcp_servers/fraud_mcp/server.py
────────────────────────────────
Fraud MCP Server — fraud detection tools backed by ChromaDB RAG.

WHY THIS IS THE KEY INNOVATION IN OPTION B:
  The Fraud Specialist agent calls this MCP server.
  This server calls ChromaDB (the open-source vector database).
  ChromaDB returns similar historical fraud patterns.
  The agent uses these patterns as evidence for its risk assessment.

  Azure AI Search does the same thing but costs $200-500/month more.
  ChromaDB running on a Container App costs ~$20-30/month.
  For 95% of insurance use cases, ChromaDB is sufficient.

DEPLOYMENT: uvicorn src.mcp_servers.fraud_mcp.server:app --port 8003
"""

from fastmcp import FastMCP
from loguru import logger
from src.rag.chroma_rag import FraudRAGPipeline

mcp = FastMCP("fraud-mcp")
_rag = FraudRAGPipeline()


@mcp.tool()
def query_fraud_index(query: str, top_k: int = 5) -> dict:
    """
    Search the historical fraud patterns index using semantic RAG.

    This is the core tool used by the Fraud Specialist agent.
    Sends the query to ChromaDB, returns the most similar fraud cases.

    Args:
        query: Natural language description of the suspicious pattern
               e.g. "third auto collision claim, same repair shop, rear-end"
        top_k: Number of similar cases to return (default 5)

    Returns:
        List of similar fraud cases with similarity scores
    """
    logger.info(f"fraud_mcp.query | query={query[:80]}")
    results = _rag.search(query=query, top_k=top_k)
    return {"results": results, "total_indexed": _rag.count()}


@mcp.tool()
def check_blocklist(claimant_id: str, provider_ids: list = None) -> dict:
    """
    Check whether a claimant or service providers are on the fraud blocklist.

    Args:
        claimant_id: The claimant's unique identifier
        provider_ids: Optional list of repair shop / medical provider IDs

    Returns:
        Whether the claimant is blocked and which providers (if any) are blocked
    """
    logger.info(f"fraud_mcp.blocklist | claimant={claimant_id}")
    # ── STUB: replace with real blocklist DB query ──
    return {
        "claimant_blocked": False,
        "blocked_providers": [],
        "checked_at": "2024-11-12T14:30:00Z",
    }


@mcp.tool()
def score_provider_network(claimant_id: str, provider_id: str) -> dict:
    """
    Score the graph proximity between a claimant and known-fraud providers.
    Higher score = closer connection to fraud rings.

    Args:
        claimant_id: The claimant's identifier
        provider_id: Repair shop / medical provider identifier

    Returns:
        proximity_score (0.0-1.0), whether the connection is flagged
    """
    # ── STUB: replace with graph DB (Neo4j) traversal ──
    return {"proximity_score": 0.05, "flagged": False, "path": []}


app = mcp.http_app()
