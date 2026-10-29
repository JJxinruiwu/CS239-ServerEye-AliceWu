"""Helpers shared by the four MCP servers."""
from __future__ import annotations

import argparse
import os

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from ..sim.server import LabServer
from ..sim.state import locked_state
from . import SERVERS

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
MUTATING = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False)


def lab(actor: str):
    """Context manager yielding a LabServer bound to the locked state.

    Usage::

        with lab("operator-mcp") as srv:
            return srv.install_package(...)

    A ``ValueError`` raised by the simulator (bad argument, missing approval, ...)
    is converted into an MCP ``ToolError`` so the calling model sees the reason
    instead of a bare "Error executing tool". The state is not saved when the
    operation fails.
    """

    class _Ctx:
        def __enter__(self):
            self._cm = locked_state()
            state = self._cm.__enter__()
            return LabServer(state, actor=actor)

        def __exit__(self, exc_type, exc, tb):
            suppressed = self._cm.__exit__(exc_type, exc, tb)
            if exc is not None and isinstance(exc, ValueError) and not suppressed:
                raise ToolError(str(exc)) from exc
            return suppressed

    return _Ctx()


def main(server: MCPServer, key: str) -> None:
    """CLI entry point used by every server module (``python -m labops.mcp_servers.<key>``)."""
    parser = argparse.ArgumentParser(description=f"{SERVERS[key]['title']} MCP server")
    parser.add_argument("--http", action="store_true", help="serve over streamable HTTP (default: stdio)")
    parser.add_argument("--host", default=os.environ.get("LABOPS_MCP_HOST", "0.0.0.0"),
                        help="bind address; 0.0.0.0 lets a Dockerised Rakazo reach the server via host.docker.internal")
    parser.add_argument("--port", type=int, default=int(os.environ.get(f"LABOPS_{key.upper()}_PORT", SERVERS[key]["port"])))
    args = parser.parse_args()
    if args.http:
        server.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        server.run(transport="stdio")
