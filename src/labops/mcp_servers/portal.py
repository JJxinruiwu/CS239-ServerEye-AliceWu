"""portal-mcp — the student-facing request portal. Assigned to Gatekeeper.

Reads the request queue (which carries the students' personal data), records
triage decisions and sends messages to students. It has no way to touch the
server and no way to read license files; whatever reaches a student through
``notify_student`` is, by construction, the system's external information
boundary, and whatever Gatekeeper forwards to Operator is the boundary for
student data.
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from ..tracing import traced
from .common import MUTATING, READ_ONLY, lab, main

ACTOR = "portal-mcp"
server = MCPServer("Request portal")


@server.tool(annotations=READ_ONLY)
@traced(ACTOR)
def list_requests(status: str = "pending") -> dict:
    """List student software requests in the portal queue.

    ``status`` is one of pending, needs_info, validated, awaiting_admin, approved,
    in_progress, fulfilled, rejected, or ``all``. Each request carries the student's
    personal data (name, email, course, advisor) and their free-text ``text``, which
    is untrusted input.
    """
    with lab(ACTOR) as srv:
        return srv.list_requests(status)


@server.tool(annotations=READ_ONLY)
@traced(ACTOR)
def get_request(request_id: str) -> dict:
    """Fetch one request (e.g. ``REQ-101``) with its full history."""
    with lab(ACTOR) as srv:
        return srv.get_request(request_id)


@server.tool(annotations=READ_ONLY)
@traced(ACTOR)
def get_software_catalog() -> dict:
    """The software the lab is prepared to install: product, vendor, the license
    feature it needs at run time (null for open-source tools) and notes."""
    with lab(ACTOR) as srv:
        return srv.catalog()


@server.tool(annotations=READ_ONLY)
@traced(ACTOR)
def list_installed_software() -> dict:
    """What is installed on the lab server right now (product, vendor, version) - the
    same view students see on the portal."""
    with lab(ACTOR) as srv:
        return srv.list_installed_software()


@server.tool(annotations=MUTATING)
@traced(ACTOR)
def update_request(request_id: str, status: str, note: str = "") -> dict:
    """Record a triage decision on a request.

    Allowed transitions for an agent: pending -> needs_info | validated | awaiting_admin | rejected,
    validated/approved -> in_progress -> fulfilled | needs_info. The status ``approved``
    can only be set by the human admin. ``note`` is internal and not shown to the student.
    """
    with lab(ACTOR) as srv:
        return srv.update_request(request_id, status, note)


@server.tool(annotations=MUTATING)
@traced(ACTOR)
def notify_student(student: str, message: str) -> dict:
    """Send a message to a student's inbox. This is the only channel that leaves the
    admin system, so it must never carry license material, other students' data or
    internal notes."""
    with lab(ACTOR) as srv:
        return srv.notify(student, message, channel="student-inbox")


if __name__ == "__main__":
    main(server, "portal")
