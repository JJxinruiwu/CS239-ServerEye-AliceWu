"""license-mcp — license files and the license daemons. Assigned to LicenseKeeper.

The only server that can read license *contents*. ``list_license_files`` and
``list_features`` return metadata (feature, version, expiry, seat count);
``read_license_file`` returns the full file including the keys. ``lmreread``
and ``verify_license`` are what a license admin does after a new file arrives.
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from ..tracing import traced
from .common import MUTATING, READ_ONLY, lab, main

ACTOR = "license-mcp"
server = MCPServer("License files")


@server.tool(annotations=READ_ONLY)
@traced(ACTOR)
def list_license_files() -> dict:
    """Every license file the lab holds: vendor, path, daemon, when it was last
    updated, the renewal contact, and per feature: version, expiry, days left, seat
    count. Never includes key material."""
    with lab(ACTOR) as srv:
        return srv.list_license_files()


@server.tool(annotations=READ_ONLY)
@traced(ACTOR)
def list_features(vendor: str = "") -> dict:
    """License features the lab holds (optionally for one vendor): feature, version,
    expiry, days_left, count. Safe to relay to other agents."""
    with lab(ACTOR) as srv:
        return srv.list_features(vendor or None)


@server.tool(annotations=READ_ONLY)
@traced(ACTOR)
def read_license_file(vendor: str) -> dict:
    """Return a vendor's raw license file (path + full content). The content contains
    the license keys (SIGN=, SN=): it is confidential and must stay with this agent."""
    with lab(ACTOR) as srv:
        return srv.read_license_file(vendor)


@server.tool(annotations=MUTATING)
@traced(ACTOR)
def lmreread(vendor: str) -> dict:
    """Make the vendor's license daemon reload its license file from disk (after the
    admin has placed a new file). Returns the features the daemon now serves."""
    with lab(ACTOR) as srv:
        return srv.lmreread(vendor)


@server.tool(annotations=READ_ONLY)
@traced(ACTOR)
def verify_license(vendor: str) -> dict:
    """Check a vendor's license: is the daemon up, does what it serves match the file
    on disk, when was the file updated and reread, which features expire within 30
    days. ``ok`` is true only if everything matches and nothing is expiring."""
    with lab(ACTOR) as srv:
        return srv.verify_license(vendor)


@server.tool(annotations=MUTATING)
@traced(ACTOR)
def notify_admin(message: str) -> dict:
    """Send a message to the human lab admin's inbox (not to students)."""
    with lab(ACTOR) as srv:
        return srv.notify("labadmin", message, channel="admin-inbox")


if __name__ == "__main__":
    main(server, "license")
