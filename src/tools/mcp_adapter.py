"""
tools/mcp_adapter.py
─────────────────────
Converts MCP server tools into CrewAI BaseTool instances.

WHAT IS MCP?
  Model Context Protocol — a standard way for AI agents to call external tools.
  Instead of writing custom code for each backend system, we write one MCP server
  per system. Each MCP server exposes its tools over JSON-RPC.

WHY THIS ADAPTER:
  CrewAI agents accept tools as BaseTool subclasses.
  MCP servers speak JSON-RPC over HTTP.
  This adapter bridges the two:
    1. Connect to MCP server → ask "what tools do you have?"
    2. For each tool, build a CrewAI BaseTool wrapper
    3. Agents receive the wrappers and call them like normal Python functions

BENEFIT:
  When you change the Claims Management System (e.g. Guidewire → Duck Creek),
  you only update the Claims MCP server.
  None of the agent code changes.
"""

from __future__ import annotations
import asyncio
import json
from typing import Any, Type
from loguru import logger
import httpx
from crewai.tools import BaseTool
from pydantic import BaseModel, Field, create_model


class _MCPClient:
    """Minimal HTTP client for JSON-RPC calls to an MCP server."""

    def __init__(self, base_url: str) -> None:
        self._url = base_url.rstrip("/") + "/rpc"
        self._id = 0

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    async def list_tools(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(self._url, json={
                "jsonrpc": "2.0", "id": self._next_id(),
                "method": "tools/list", "params": {},
            })
            r.raise_for_status()
            return r.json()["result"]["tools"]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(self._url, json={
                "jsonrpc": "2.0", "id": self._next_id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            })
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                raise RuntimeError(f"MCP error: {data['error']}")
            return data["result"]


def _json_type_to_python(t: str) -> type:
    return {"string": str, "integer": int, "number": float,
            "boolean": bool, "array": list, "object": dict}.get(t, str)


def _make_tool(client: _MCPClient, spec: dict[str, Any]) -> BaseTool:
    """Build a single CrewAI BaseTool that proxies to one MCP tool."""
    name = spec["name"]
    description = spec.get("description", name)
    schema = spec.get("inputSchema", {})
    properties = schema.get("properties", {})
    required_fields = set(schema.get("required", []))

    fields: dict[str, Any] = {}
    for fname, prop in properties.items():
        py_type = _json_type_to_python(prop.get("type", "string"))
        default = ... if fname in required_fields else None
        fields[fname] = (py_type, Field(default, description=prop.get("description", "")))

    ArgsModel: Type[BaseModel] = create_model(f"{name}Args", **fields)

    class MCPProxyTool(BaseTool):
        name: str = name
        description: str = description
        args_schema: Type[BaseModel] = ArgsModel

        def _run(self, **kwargs: Any) -> str:
            try:
                result = asyncio.run(client.call_tool(name, kwargs))
                return json.dumps(result) if not isinstance(result, str) else result
            except Exception as e:
                logger.error(f"mcp.tool.error | tool={name} error={e}")
                return json.dumps({"error": str(e), "tool": name})

    return MCPProxyTool()


def load_mcp_tools(server_url: str) -> list[BaseTool]:
    """
    Connect to an MCP server and return all its tools as CrewAI tools.

    Called at agent construction time. If the server is unreachable
    (e.g. not yet started), returns an empty list with a warning.
    Agents can still run — they just won't have those tools available.
    """
    try:
        client = _MCPClient(server_url)
        specs = asyncio.run(client.list_tools())
        tools = [_make_tool(client, s) for s in specs]
        logger.info(f"mcp.tools.loaded | url={server_url} count={len(tools)}")
        return tools
    except Exception as e:
        logger.warning(f"mcp.tools.unavailable | url={server_url} error={e}")
        return []
