"""ServerEye console (Starlette). See ``labops.console`` for what each page shows.

    python -m labops.console.app [--port 8790]

Rakazo credentials (for the live agent timeline and for starting scenarios) come from
the environment or a ``.env`` file in ``src/`` or the repository root.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import date
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ..agents.profiles import AGENTS
from ..monitor.client import health_check_async
from ..monitor.rules import check
from ..sim.server import LabServer
from ..sim.state import REPO_ROOT, load_state, locked_state
from ..tracing import trace_path
from . import CONSOLE_PORT

SRC = REPO_ROOT  # labops/sim/state.py: REPO_ROOT is the directory that contains labops/
STATIC = Path(__file__).with_name("static")
TRACES = SRC / "traces"
SCENARIOS = SRC / "scenarios"
sys.path.insert(0, str(SRC / "scripts"))
from export_rakazo_traces import thread_events, tool_events  # noqa: E402

LANES = ["admin", "Gatekeeper", "Operator", "LicenseKeeper", "Monitor"]
SESSION_FILE = SRC / "lab" / ".rakazo_session.json"  # git-ignored, mode 600


def load_dotenv() -> None:
    for path in (SRC / ".env", SRC.parent / ".env"):
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())


# --------------------------------------------------------------------------- Rakazo (live threads)
class RakazoSession:
    """One signed-in Rakazo client shared by all requests (sign-in is rate limited)."""

    def __init__(self) -> None:
        self.app = None
        self.bots: dict[str, str] = {}
        self.lock = threading.Lock()
        self.error: str | None = None
        self.retry_at = 0.0  # no sign-in attempt before this time (Rakazo rate-limits sign-in)

    def _connect(self) -> None:
        from ..agents.rakazo import Rakazo
        url = os.environ.get("RAKAZO_URL") or "http://127.0.0.1:5173"
        if not (os.environ.get("RAKAZO_EMAIL") and os.environ.get("RAKAZO_PASSWORD")):
            raise RuntimeError("set RAKAZO_EMAIL and RAKAZO_PASSWORD (env or .env) for the live view")
        app = Rakazo(url, origin=os.environ.get("RAKAZO_ORIGIN"))
        # reuse the session saved by the last console run: restarts cost no sign-in
        if SESSION_FILE.exists():
            for name, value in json.loads(SESSION_FILE.read_text()).items():
                app.http.cookies.set(name, value)
            try:
                app.rpc("bots/list")
            except RuntimeError:
                app.http.cookies.clear()
        if not app.http.cookies:
            app.login(os.environ["RAKAZO_EMAIL"], os.environ["RAKAZO_PASSWORD"])
            SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
            SESSION_FILE.write_text(json.dumps(dict(app.http.cookies)))
            SESSION_FILE.chmod(0o600)
        self.app = app
        self.bots = {b["name"]: b["id"] for b in app.list_bots() if b["name"] in AGENTS}

    def call(self, fn):
        """Run ``fn(app, bots)`` with the shared signed-in client. Only signing in is serialised;
        the client itself is used concurrently (by the live view and a running scenario).
        A failed call does not sign in again, because Rakazo rate-limits sign-in."""
        with self.lock:
            if self.app is None:
                if time.time() < self.retry_at:
                    raise RuntimeError(f"{self.error or 'sign-in failed'} (next sign-in attempt in "
                                       f"{int(self.retry_at - time.time())}s)")
                try:
                    self._connect()
                except Exception as exc:
                    self.retry_at = time.time() + (120 if "429" in str(exc) else 15)
                    self.error = str(exc)
                    raise
        try:
            return fn(self.app, self.bots)
        except RuntimeError as exc:
            if "HTTP 401" in str(exc):  # session expired: sign in again on the next call
                self.app = None
            raise

    def threads(self) -> dict[str, dict]:
        try:
            out = self.call(lambda app, bots: {name: app.conversation(bot_id) for name, bot_id in bots.items()})
            self.error = None
            return out
        except Exception as exc:  # noqa: BLE001 - shown in the UI, retried after the back-off
            self.error = str(exc).split(" (next sign-in")[0]
            self.last_error = str(exc)
            return {}


RAKAZO = RakazoSession()


# --------------------------------------------------------------------------- scenario runner
class Runner:
    """At most one scenario run at a time, in a background thread of the console, using the
    console's signed-in Rakazo client (one sign-in for the console's lifetime)."""

    def __init__(self) -> None:
        self.thread: threading.Thread | None = None
        self.scenario: str | None = None
        self.log: list[str] = []
        self.started: float | None = None
        self.error: str | None = None
        self.done = False

    def start(self, name: str) -> dict:
        if self.thread and self.thread.is_alive():
            raise ValueError(f"{self.scenario} is still running")
        path = SCENARIOS / f"{name}.json"
        if not path.exists():
            raise ValueError(f"unknown scenario {name}")
        RAKAZO.call(lambda app, bots: None)  # sign in now, so a failure shows up immediately
        self.scenario, self.log, self.started, self.error, self.done = name, [], time.time(), None, False
        self.thread = threading.Thread(target=self._run, args=(path,), daemon=True)
        self.thread.start()
        return self.status()

    def _run(self, path: Path) -> None:
        from run_rakazo_scenario import run_scenario
        try:
            url = os.environ.get("RAKAZO_URL") or "http://127.0.0.1:5173"
            run_scenario(RAKAZO.app, path, TRACES, url, log=self.log.append)
        except Exception as exc:  # noqa: BLE001 - shown in the UI
            self.error = f"{type(exc).__name__}: {exc}"
            self.log.append("FAILED: " + self.error)
        finally:
            self.done = True

    def status(self) -> dict:
        running = bool(self.thread and self.thread.is_alive())
        return {"scenario": self.scenario, "running": running, "error": self.error,
                "exit_code": None if running or not self.done else (1 if self.error else 0),
                "elapsed": int(time.time() - self.started) if self.started else 0, "log": self.log[-40:]}


RUNNER = Runner()


# --------------------------------------------------------------------------- helpers
def ok(data, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status)


def err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def days_left(expires: str) -> int:
    return (date.fromisoformat(expires) - date.today()).days


def read_calls(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def timeline(threads: dict[str, dict], calls: list[dict]) -> list[dict]:
    tools = tool_events(calls)
    events = []
    for name, thread in threads.items():
        actions = [e for e in tools if e["kind"] == "action" and e["agent"] == name]
        events += thread_events(thread, name, actions)
    merged = sorted(events + tools, key=lambda e: (e["at"], 0 if e["kind"] == "thought" else 1))
    for i, e in enumerate(merged, 1):
        e["seq"] = i
    return merged


# --------------------------------------------------------------------------- pages
NO_CACHE = {"Cache-Control": "no-store"}  # the console is edited while running; never serve a stale page


def page(name: str):
    async def handler(request: Request):
        return FileResponse(STATIC / f"{name}.html", headers=NO_CACHE)
    return handler


class NoCacheStatic(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers.update(NO_CACHE)
        return response


# --------------------------------------------------------------------------- portal API (student view)
async def portal_students(request: Request):
    st = load_state()
    return ok({"students": [{"id": k, "name": v["name"], "course": v["course"]} for k, v in st["students"].items()]})


async def portal_view(request: Request):
    student = request.query_params.get("student", "")
    st = load_state()
    if student not in st["students"]:
        return err("unknown student", 404)
    lab = LabServer(st)
    held = {f["feature"] for lf in st["license_files"].values() for f in lf["features"]}
    catalog = []
    for c in st["catalog"]:
        inst = st["installed"].get(c["product"])
        offered = st["vendor_site"].get(c["vendor"], {}).get("products", {}).get(c["product"], [])
        status = "installed" if inst else ("available" if c["feature"] is None or c["feature"] in held else "needs_license")
        catalog.append({**c, "status": status, "installed_version": inst["version"] if inst else None,
                        "versions": offered})
    me = st["students"][student]
    # a student sees only their own requests and their own inbox (P6)
    mine = [{k: r[k] for k in ("id", "software", "version", "text", "status", "submitted_at")} |
            {"history": [{"at": h["at"], "status": h["status"]} for h in r["history"]]}
            for r in st["requests"] if r["student"] == student]
    inbox = [{k: n[k] for k in ("id", "message", "at")} for n in st["notifications"]
             if n["to"] == student and n["channel"] == "student-inbox"]
    return ok({"student": {"id": student, **me}, "installed": lab.list_installed_software()["installed"],
               "catalog": catalog, "requests": list(reversed(mine)), "inbox": list(reversed(inbox))})


async def portal_submit(request: Request):
    body = await request.json()
    student, software = body.get("student", ""), (body.get("software") or "").strip()
    text = (body.get("text") or "").strip()
    if not software or not text:
        return err("software and a justification are required")
    with locked_state() as st:
        if student not in st["students"]:
            return err("unknown student", 404)
        req = LabServer(st, actor="portal-web").submit_request(student, software, text, (body.get("version") or "").strip())
    return ok({"id": req["id"], "status": req["status"]})


# --------------------------------------------------------------------------- monitor API (admin view)
async def monitor_view(request: Request):
    st = load_state()
    ls = st["license_server"]
    features = []
    for vendor, feats in ls["loaded"].items():
        daemon = st["vendors"][vendor]["daemon"]
        file_feats = {f["feature"]: f for f in st["license_files"][vendor]["features"]}
        for f in feats:
            holders = [f"{j['user']}@{j['host']}" for j in st["jobs"] if f["feature"] in j.get("features", [])]
            on_disk = file_feats.get(f["feature"])
            features.append({"vendor": vendor, "daemon": daemon, "daemon_status": ls["daemons"].get(daemon, "down"),
                             "feature": f["feature"], "version": f["version"], "expires": f["expires"],
                             "days_left": days_left(f["expires"]), "total": f["count"], "in_use": len(holders),
                             "holders": holders,
                             "file_differs": bool(on_disk and on_disk["expires"] != f["expires"])})
    disk = [{"mount": m, "used_gb": round(d["used_gb"], 1), "total_gb": d["total_gb"],
             "pct": round(100 * d["used_gb"] / d["total_gb"], 1)} for m, d in st["disk"].items()]
    checks = [a for a in st["audit"] if a["event"] == "health_check"][-8:]
    inbox = [n for n in st["notifications"] if n["channel"] == "admin-inbox"]
    pending = [{k: r[k] for k in ("id", "student", "software", "version", "status")} for r in st["requests"]
               if r["status"] in ("awaiting_admin", "needs_info", "pending", "validated", "in_progress")]
    return ok({
        "host": {"hostname": st["hostname"], "os": st["os"], "uptime_days": st["uptime_days"], "load": st["load"],
                 "maintenance": st["maintenance_window"]["active"]},
        "daemons": [{"name": "lmgrd", "status": ls["lmgrd"]}] + [{"name": d, "status": s} for d, s in ls["daemons"].items()],
        "features": sorted(features, key=lambda f: f["days_left"]),
        "disk": disk,
        "jobs": st["jobs"],
        "sudoers": st["sudoers"],
        "installed": [{"product": p, **i} for p, i in st["installed"].items()],
        "verdict": check(st, {"event": "scheduled"}),  # what Monitor would say right now (read-only preview)
        "checks": list(reversed(checks)),
        "inbox": list(reversed(inbox)),
        "requests": pending,
    })


async def monitor_check(request: Request):
    try:
        return ok(await health_check_async({"event": "scheduled"}))
    except Exception as exc:  # noqa: BLE001
        return err(f"Monitor service unreachable ({exc}); is labops.mcp_servers.serve running?", 503)


async def admin_action(request: Request):
    body = await request.json()
    action = body.get("action")
    try:
        with locked_state() as st:
            admin = LabServer(st, actor="admin")
            if action == "approve":
                result = admin.approve_request(body["request_id"])
            elif action == "stop_daemon":
                result = admin.stop_daemon(body["name"])
            elif action == "start_daemon":
                result = admin.start_daemon(body["name"])
            else:
                return err(f"unknown action {action}")
    except (ValueError, KeyError) as exc:
        return err(str(exc))
    return ok(result)


# --------------------------------------------------------------------------- agents API
async def agents_events(request: Request):
    source = request.query_params.get("source", "live")
    if source == "live":
        threads = RAKAZO.threads()
        events = timeline(threads, read_calls(trace_path()))
        return ok({"source": "live", "lanes": LANES, "events": events,
                   "rakazo_error": getattr(RAKAZO, "last_error", None) if RAKAZO.app is None else None,
                   "runner": RUNNER.status()})
    folder = TRACES / source
    if not (folder / "trace.json").exists():
        return err(f"no recorded trace {source}", 404)
    trace = json.loads((folder / "trace.json").read_text(encoding="utf-8"))
    return ok({"source": source, "lanes": LANES, "events": trace["events"], "scenario": trace.get("scenario"),
               "runner": RUNNER.status()})


async def agents_send(request: Request):
    """Send a free-form message to one bot on Rakazo, on top of the current lab state."""
    body = await request.json()
    agent, text = body.get("agent", ""), (body.get("text") or "").strip()
    if agent not in AGENTS:
        return err(f"unknown agent {agent}")
    if not text:
        return err("empty message")
    if RUNNER.status()["running"]:
        return err(f"{RUNNER.scenario} is running; wait for it to finish", 409)
    try:
        run = RAKAZO.call(lambda app, bots: app.send(bots[agent], text))
    except Exception as exc:  # noqa: BLE001
        return err(f"Rakazo: {exc}", 502)
    return ok({"sent": True, "agent": agent, "run": run.get("runId") if isinstance(run, dict) else None})


async def agents_reset(request: Request):
    """Maintenance: reset the lab to the seed state and/or clear the bots' threads."""
    body = await request.json()
    if RUNNER.status()["running"]:
        return err(f"{RUNNER.scenario} is running", 409)
    done = []
    if body.get("lab"):
        from ..sim.state import reset_state
        reset_state()
        trace_path().parent.mkdir(parents=True, exist_ok=True)
        trace_path().write_text("", encoding="utf-8")
        done.append("lab reset to seed state, tool-call log cleared")
    if body.get("threads"):
        try:
            RAKAZO.call(lambda app, bots: [app.rpc("threads/clear", {"botId": b}) for b in bots.values()])
            done.append("bot threads cleared")
        except Exception as exc:  # noqa: BLE001
            return err(f"Rakazo: {exc}", 502)
    return ok({"done": done})


async def agents_catalog(request: Request):
    scenarios = []
    for p in sorted(SCENARIOS.glob("*.json")):
        sc = json.loads(p.read_text(encoding="utf-8"))
        scenarios.append({"name": p.stem, "title": sc.get("title"), "policies": sc.get("policies", []),
                          "recorded": (TRACES / p.stem / "trace.json").exists()})
    return ok({"scenarios": scenarios})


async def agents_run(request: Request):
    body = await request.json()
    try:
        return ok(RUNNER.start(body.get("scenario", "")))
    except ValueError as exc:
        return err(str(exc), 409)
    except Exception as exc:  # noqa: BLE001 - e.g. Rakazo sign-in rate limit
        return err(f"Rakazo: {exc}", 502)


# --------------------------------------------------------------------------- app
def make_app() -> Starlette:
    routes = [
        Route("/", lambda r: RedirectResponse("/agents")),
        Route("/portal", page("portal")),
        Route("/monitor", page("monitor")),
        Route("/agents", page("agents")),
        Route("/api/portal/students", portal_students),
        Route("/api/portal", portal_view),
        Route("/api/portal/requests", portal_submit, methods=["POST"]),
        Route("/api/monitor", monitor_view),
        Route("/api/monitor/check", monitor_check, methods=["POST"]),
        Route("/api/admin", admin_action, methods=["POST"]),
        Route("/api/agents/events", agents_events),
        Route("/api/agents/scenarios", agents_catalog),
        Route("/api/agents/run", agents_run, methods=["POST"]),
        Route("/api/agents/send", agents_send, methods=["POST"]),
        Route("/api/agents/reset", agents_reset, methods=["POST"]),
        Mount("/static", NoCacheStatic(directory=STATIC), name="static"),
    ]
    return Starlette(routes=routes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=CONSOLE_PORT)
    args = parser.parse_args()
    load_dotenv()
    print(f"ServerEye console: http://{args.host}:{args.port}/portal  /monitor  /agents", flush=True)
    uvicorn.run(make_app(), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
