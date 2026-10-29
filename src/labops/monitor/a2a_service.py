"""Monitor as an A2A service (Agent2Agent protocol, JSON-RPC over HTTP).

    python -m labops.monitor.a2a_service --port 8780

The agent card is served at /.well-known/agent.json. A caller sends one message
whose text is a JSON context (``{"event": "install", "product": ..., "version": ...}``
or ``{"event": "scheduled"}``); the executor reads the simulated server state,
runs ``rules.check`` and returns the verdict as a text artifact. If the verdict is
not ``ok`` the service notifies the admin itself (admin inbox) and writes a
``health_check`` audit entry either way.

Modelled on the course tutorial's ``examples/a2a_server.py`` (a rule-based
reviewer). The caller never learns how the verdict is produced; A2A only fixes
the boundary.
"""
from __future__ import annotations

import argparse
import json

import uvicorn
from a2a.helpers import get_message_text, new_task_from_user_message, new_text_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, TaskState
from starlette.applications import Starlette

from ..sim.server import LabServer
from ..sim.state import locked_state
from ..tracing import record
from . import A2A_PORT
from .rules import check

ACTOR = "monitor-a2a"


def run_check(context: dict) -> dict:
    """Read the lab state, apply the rules, notify the admin if needed. Used by the A2A
    executor and directly by tests."""
    with locked_state() as st:
        verdict = check(st, context)
        srv = LabServer(st, actor=ACTOR)
        srv.audit("health_check", trigger=context.get("event", "scheduled"), status=verdict["status"],
                  codes=sorted({a["code"] for a in verdict["anomalies"]}))
        if verdict["status"] != "ok":
            lines = [f"[Monitor] {verdict['status'].upper()}: {verdict['recommendation'].replace('_', ' ')} "
                     f"(after {context.get('event', 'scheduled')}{' of ' + str(context.get('product')) if context.get('product') else ''})"]
            lines += [f"- {a['code']} {a['severity']}: {a['detail']}" for a in verdict["anomalies"]]
            srv.notify("labadmin", "\n".join(lines), channel="admin-inbox")
            verdict["admin_notified"] = True
        else:
            verdict["admin_notified"] = False
    record({"call_id": "a2a", "at": verdict_at(), "server": ACTOR, "tool": "health_check", "args": context,
            "result": verdict, "error": None, "ms": 0})
    return verdict


def verdict_at() -> str:
    from ..sim.state import now_iso
    return now_iso()


class MonitorExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        task = context.current_task or new_task_from_user_message(context.message)
        if context.current_task is None:
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        await updater.update_status(TaskState.TASK_STATE_WORKING, message=new_text_message("Checking the lab server."))
        try:
            payload = json.loads(get_message_text(context.message) or "{}")
            if not isinstance(payload, dict):
                raise ValueError("context must be a JSON object")
            result = run_check(payload)
        except (ValueError, KeyError, TypeError) as exc:
            result = {"status": "error", "anomalies": [], "recommendation": "retry", "rationale": str(exc)}
        await updater.add_artifact(parts=[new_text_part(text=json.dumps(result), media_type="text/plain")])
        await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=new_text_message("Health check finished."))

    async def cancel(self, context, event_queue):
        raise NotImplementedError("a health check cannot be cancelled")


def make_app(port: int, host: str = "127.0.0.1") -> Starlette:
    card = AgentCard(
        name="Monitor",
        description="Rule-based health check of the EDA lab server: license daemons, seat usage, "
                    "disk, killed jobs, privilege changes, and the outcome of the last install.",
        version="1.0.0",
        default_input_modes=["text/plain"], default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=f"http://{host}:{port}/", protocol_version="1.0")],
        skills=[AgentSkill(id="health-check", name="Health check",
                           description="Return status, anomalies and a recommendation; notify the admin when not ok.",
                           tags=["monitoring"])],
    )
    handler = DefaultRequestHandler(agent_executor=MonitorExecutor(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=(create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/")))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=A2A_PORT)
    args = parser.parse_args()
    uvicorn.run(make_app(args.port, args.host), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
