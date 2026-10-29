"""Tool-call tracing shared by the MCP servers.

Every tool call is appended as one JSON line to ``traces/raw/tool_calls.jsonl``
(override with ``LABOPS_TRACE``). This is the *action/observation* half of an
agentic trace; the *thought/message* half comes from the Rakazo threads,
exported with ``scripts/export_rakazo_traces.py``.

The two halves are joined on timestamps, so keep the clocks of the MCP servers
and the agent runtime the same (they normally run on the same machine).
"""
from __future__ import annotations

import functools
import inspect
import json
import os
import time
import uuid
from pathlib import Path

from .sim.state import REPO_ROOT, now_iso

DEFAULT_TRACE = REPO_ROOT / "traces" / "raw" / "tool_calls.jsonl"


def trace_path() -> Path:
    return Path(os.environ.get("LABOPS_TRACE", DEFAULT_TRACE))


def record(entry: dict) -> None:
    path = trace_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def traced(server_name: str):
    """Decorator: log args, result/error and latency of a tool function.

    Must be applied *inside* ``@server.tool(...)`` (i.e. closest to the function)
    so the MCP SDK still sees the original signature through ``__wrapped__``.
    """

    def decorate(fn):
        is_coroutine = inspect.iscoroutinefunction(fn)

        def _log(args, result=None, error=None, started=0.0):
            record({
                "call_id": uuid.uuid4().hex[:12],
                "at": now_iso(),
                "server": server_name,
                "tool": fn.__name__,
                "args": args,
                "result": result,
                "error": error,
                "ms": round((time.perf_counter() - started) * 1000, 1),
            })

        if is_coroutine:
            @functools.wraps(fn)
            async def wrapper(*a, **kw):
                started = time.perf_counter()
                args = inspect.signature(fn).bind(*a, **kw).arguments
                try:
                    result = await fn(*a, **kw)
                except Exception as exc:  # noqa: BLE001 - we re-raise after logging
                    _log(args, error=f"{type(exc).__name__}: {exc}", started=started)
                    raise
                _log(args, result=result, started=started)
                return result
        else:
            @functools.wraps(fn)
            def wrapper(*a, **kw):
                started = time.perf_counter()
                args = inspect.signature(fn).bind(*a, **kw).arguments
                try:
                    result = fn(*a, **kw)
                except Exception as exc:  # noqa: BLE001
                    _log(args, error=f"{type(exc).__name__}: {exc}", started=started)
                    raise
                _log(args, result=result, started=started)
                return result
        return wrapper

    return decorate
