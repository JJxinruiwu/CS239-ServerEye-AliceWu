#!/usr/bin/env python3
"""Run one scenario on the Rakazo team end to end and export its trace (one sign-in).

    python scripts/run_rakazo_scenario.py scenarios/02_injection_pii_and_license_leak.json
    python scripts/run_rakazo_scenario.py scenarios/05_license_renewal.json --out traces

Steps: reset the simulated lab and apply the scenario's setup, clear the bots'
threads (every run starts from an empty context), then walk the scenario's
``steps`` in order: an ``agent`` step sends the kickoff to that bot and waits
until no bot is running and the MCP log has been quiet for a while; an ``admin``
step applies the human action (approve, upload a license file, stop a daemon).
Finally the trace folder is written (``export_rakazo_traces.export_run``).

Needs the MCP servers and the Monitor service (``python -m labops.mcp_servers.serve``)
and the bots (``python -m labops.agents.rakazo_app --apply --auto-approve``).
RAKAZO_URL / RAKAZO_EMAIL / RAKAZO_PASSWORD come from the environment or from a
``.env`` file in ``src/`` or the repository root, read literally (no shell
expansion, so any character works in the password).
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from export_rakazo_traces import export_run  # noqa: E402
from labops.agents.profiles import AGENTS  # noqa: E402
from labops.agents.rakazo import Rakazo  # noqa: E402
from labops.sim.scenario import apply_admin_action, apply_setup, load_scenario  # noqa: E402
from labops.sim.state import reset_state  # noqa: E402
from labops.tracing import record, trace_path  # noqa: E402

POLL_SECONDS = 5


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def tool_calls() -> list[dict]:
    if not trace_path().exists():
        return []
    return [json.loads(line) for line in trace_path().read_text(encoding="utf-8").splitlines() if line.strip()]


def wait_for_quiet(app: Rakazo, bots: dict[str, str], seen: int, quiet: int, timeout: int, log=print) -> int:
    """Report tool calls as they appear; return once no bot runs and the log stayed quiet."""
    start = last_activity = time.time()
    while time.time() - start < timeout:
        time.sleep(POLL_SECONDS)
        calls = tool_calls()
        for c in calls[seen:]:
            flag = "  ERROR: " + str(c["error"])[:80] if c.get("error") else ""
            log(f"  [{int(time.time() - start):>3}s] {c['server']}.{c['tool']}{flag}")
        if len(calls) != seen:
            seen, last_activity = len(calls), time.time()
        running = [name for name in AGENTS if app.conversation(bots[name]).get("activeRuns")]
        if running:
            last_activity = time.time()
        elif time.time() - last_activity > quiet:
            return seen
    log(f"  timeout after {timeout}s; continuing")
    return seen


def run_scenario(app: Rakazo, scenario_path: str | Path, out: Path, url: str,
                 quiet: int = 45, timeout: int = 900, log=print) -> Path:
    """Reset the lab, clear the bots' threads, play the steps, export the trace.

    ``app`` is an already signed-in client, so a caller that runs many scenarios (the web
    console) signs in once instead of once per run (Rakazo rate-limits sign-in)."""
    scenario = load_scenario(scenario_path)
    bots = {b["name"]: b["id"] for b in app.list_bots()}
    missing = [name for name in AGENTS if name not in bots]
    if missing:
        raise RuntimeError(f"bots missing in Rakazo: {', '.join(missing)} (run rakazo_app --apply)")

    reset_state()
    trace_path().parent.mkdir(parents=True, exist_ok=True)
    trace_path().write_text("", encoding="utf-8")
    for line in apply_setup(scenario["setup"]):
        log("setup: " + line)
    for name in AGENTS:
        app.rpc("threads/clear", {"botId": bots[name]})

    seen = 0
    started = time.time()
    for i, step in enumerate(scenario["steps"], 1):
        if "admin" in step:
            lines = apply_admin_action(step["admin"])
            # the admin action goes into the MCP log so the trace shows when it happened
            record({"call_id": f"admin-{i}", "at": _now(), "server": "admin", "tool": "admin_action",
                    "args": step["admin"], "result": {"log": lines}, "error": None, "ms": 0})
            log(f"step {i} admin: " + "; ".join(lines))
            seen = len(tool_calls())
            continue
        app.send(bots[step["agent"]], step["message"])
        log(f"step {i} -> {step['agent']}: {step['message'][:100]}")
        seen = wait_for_quiet(app, bots, seen, quiet, timeout, log)
    log(f"run finished: {seen} tool calls in {int(time.time() - started)}s")
    return export_run(app, scenario["name"], Path(out), scenario, url)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", help="scenario JSON (scenarios/*.json)")
    parser.add_argument("--out", default=str(ROOT / "traces"), help="output root")
    parser.add_argument("--quiet", type=int, default=45, help="seconds without activity that end a step")
    parser.add_argument("--timeout", type=int, default=900, help="hard limit per step in seconds")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")           # src/.env
    load_dotenv(ROOT.parent / ".env")    # repository root
    url = os.environ.get("RAKAZO_URL") or "http://127.0.0.1:5173"
    email = os.environ.get("RAKAZO_EMAIL") or input("Rakazo email: ")
    password = os.environ.get("RAKAZO_PASSWORD") or getpass.getpass("Rakazo password: ")
    with Rakazo(url, origin=os.environ.get("RAKAZO_ORIGIN")) as app:
        app.login(email, password)
        run_scenario(app, args.scenario, Path(args.out), url, args.quiet, args.timeout,
                     log=lambda line: print(line, flush=True))


def _now() -> str:
    from labops.sim.state import now_iso
    return now_iso()


if __name__ == "__main__":
    main()
