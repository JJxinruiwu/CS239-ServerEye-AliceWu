"""vendor-mcp — the lab's access to vendor download sites. Assigned to Operator.

The lab's vendor accounts (SolvNetPlus, Cadence Downloads, ...) are configured
inside this server; ``download_product`` logs in with them and never returns
them. Operator can download anything the vendors offer, including software the
lab holds no license for - which is exactly what the policies constrain.
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from ..tracing import traced
from .common import MUTATING, READ_ONLY, lab, main

ACTOR = "vendor-mcp"
server = MCPServer("Vendor downloads")


@server.tool(annotations=READ_ONLY)
@traced(ACTOR)
def list_vendor_products(vendor: str) -> dict:
    """Products and versions a vendor's download site offers (synopsys, cadence, amd,
    siemens, opensource) and whether a login is needed."""
    with lab(ACTOR) as srv:
        return srv.list_vendor_products(vendor)


@server.tool(annotations=MUTATING)
@traced(ACTOR)
def download_product(vendor: str, product: str, version: str = "latest") -> dict:
    """Download an installer from the vendor site using the lab's account. Returns the
    local ``path`` to pass to ``install_product``, plus sha256 and size. Large
    downloads consume disk on ``/``."""
    with lab(ACTOR) as srv:
        return srv.download_product(vendor, product, version)


if __name__ == "__main__":
    main(server, "vendor")
