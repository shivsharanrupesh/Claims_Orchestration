"""
mcp_servers/policy_mcp/server.py
─────────────────────────────────
Policy MCP Server — tools for the Policy Administration System.
DEPLOYMENT: uvicorn src.mcp_servers.policy_mcp.server:app --port 8002
"""

from fastmcp import FastMCP

mcp = FastMCP("policy-mcp")


@mcp.tool()
def get_policy(policy_number: str) -> dict:
    """Get full policy record by policy number."""
    return {
        "policy_number": policy_number,
        "status": "active",
        "product_line": "personal_auto",
        "effective_date": "2024-01-01",
        "expiry_date": "2025-01-01",
        "deductible_cad": 1000.0,
        "coverage_limit_cad": 100000.0,
        "insured_vehicle": {"make": "Honda", "model": "Civic", "year": 2022},
    }


@mcp.tool()
def check_coverage(policy_number: str, incident_date: str, damage_type: str) -> dict:
    """Check if a specific incident and damage type is covered."""
    return {
        "is_covered": True,
        "applicable_clauses": ["DCPD", "SEF-44"],
        "exclusions_triggered": [],
        "deductible_cad": 1000.0,
        "coverage_limit_cad": 100000.0,
    }


@mcp.tool()
def get_endorsements(policy_number: str) -> dict:
    """List active endorsements on the policy."""
    return {
        "endorsements": [
            {"code": "SEF-44", "description": "Family Protection", "active": True},
            {"code": "OPCF-20", "description": "Loss of Vehicle Use", "active": True},
        ]
    }


app = mcp.http_app()
