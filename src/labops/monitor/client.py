"""A2A client for the Monitor service: ``health_check(context)`` returns the verdict dict.

Used by the ``server`` MCP tool of the same name (so Operator waits for a real
verdict) and by ``scripts/monitor_routine.py`` (the scheduled check).
"""
from __future__ import annotations

import asyncio
import json
import os

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import new_text_message
from a2a.types import Role, SendMessageRequest
from google.protobuf.json_format import MessageToDict

from . import A2A_PORT


def monitor_url() -> str:
    return os.environ.get("LABOPS_MONITOR_URL", f"http://127.0.0.1:{A2A_PORT}")


async def health_check_async(context: dict) -> dict:
    async with httpx.AsyncClient(trust_env=False, timeout=30) as http:
        card = await A2ACardResolver(httpx_client=http, base_url=monitor_url()).get_agent_card()
    client = await create_client(agent=card, client_config=ClientConfig(streaming=False))
    try:
        message = new_text_message(json.dumps(context), role=Role.ROLE_USER)
        last = None
        async for event in client.send_message(SendMessageRequest(message=message)):
            last = MessageToDict(event, preserving_proto_field_name=True)
    finally:
        await client.close()
    return _verdict(last)


def _verdict(event: dict | None) -> dict:
    """Pull the JSON verdict out of the completed task's text artifact."""
    if not event:
        raise RuntimeError("no response from the Monitor service")
    task = event.get("task") or event
    for artifact in task.get("artifacts", []):
        for part in artifact.get("parts", []):
            text = part.get("text")
            if text:
                return json.loads(text)
    raise RuntimeError(f"Monitor returned no verdict: {json.dumps(event)[:300]}")


def health_check(context: dict) -> dict:
    return asyncio.run(health_check_async(context))
