"""Four MCP tool servers, one per privilege domain, plus the Monitor A2A service.

| key      | default port | assigned to   | may change the server? |
|----------|--------------|---------------|------------------------|
| portal   | 8771         | Gatekeeper    | request queue + student notifications only |
| vendor   | 8772         | Operator      | downloads from vendor sites (holds the vendor credentials) |
| server   | 8773         | Operator      | yes: installs, snapshots, root shell; read-only lmstat; health_check (A2A bridge) |
| license  | 8774         | LicenseKeeper | license files: read, lmreread, verify; admin notifications |

Monitor is not an MCP server: it is a rule-based agent behind an A2A endpoint
(``labops.monitor``, port 8780) that the ``server.health_check`` tool calls.

Splitting by privilege is what makes capability boundaries real rather than
promised: a bot that was never connected to ``server`` cannot call ``run_shell``
no matter what its prompt says, and a bot without ``license`` cannot read a
license file.
"""

SERVERS = {
    "portal": {"module": "labops.mcp_servers.portal", "port": 8771, "title": "Request portal"},
    "vendor": {"module": "labops.mcp_servers.vendor", "port": 8772, "title": "Vendor downloads"},
    "server": {"module": "labops.mcp_servers.server", "port": 8773, "title": "EDA server"},
    "license": {"module": "labops.mcp_servers.license", "port": 8774, "title": "License files"},
}
