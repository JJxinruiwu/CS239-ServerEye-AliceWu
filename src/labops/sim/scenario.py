"""Scenario files: reproducible setups for traces.

A scenario is a JSON file (see scenarios/) with:

    name, title, description, policies (P-ids from policy.md), expected_outcome, without_enforcement
    setup:  maintenance_window (bool), clear_jobs (bool), stop_daemon (service name),
            requests: [{student, software, text, version?, admin_approved?}],
            state_overrides: {key: value}
    steps:  [{"agent": "Gatekeeper", "message": "..."}
             | {"admin": {"approve_request": "REQ-101"}}
             | {"admin": {"upload_license": {"vendor": "synopsys", "features": [{feature, version, expires_in_days, count}]}}}
             | {"admin": {"stop_daemon": "snpslmd"}} | {"admin": {"maintenance_window": true}} ...]

`apply_setup()` resets nothing by itself; callers reset the state first.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from .server import LabServer
from .state import locked_state


def load_scenario(path: str | Path) -> dict:
    scenario = json.loads(Path(path).read_text(encoding="utf-8"))
    scenario.setdefault("name", Path(path).stem)
    scenario.setdefault("setup", {})
    scenario.setdefault("steps", [])
    return scenario


def _features(spec: list[dict]) -> list[dict]:
    """Turn ``expires_in_days`` into an ISO date so scenarios stay reproducible."""
    out = []
    for f in spec:
        f = dict(f)
        if "expires_in_days" in f:
            f["expires"] = (date.today() + timedelta(days=int(f.pop("expires_in_days")))).isoformat()
        out.append(f)
    return out


def apply_setup(setup: dict) -> list[str]:
    """Apply a scenario setup block to the current state. Returns human-readable log lines."""
    log: list[str] = []
    with locked_state() as st:
        srv = LabServer(st, actor="scenario-setup")
        if "maintenance_window" in setup:
            srv.set_maintenance_window(bool(setup["maintenance_window"]))
            log.append(f"maintenance window active = {bool(setup['maintenance_window'])}")
        if setup.get("clear_jobs"):
            st["jobs"] = []
            log.append("all jobs cleared")
        if setup.get("stop_daemon"):
            srv.stop_daemon(setup["stop_daemon"])
            log.append(f"daemon {setup['stop_daemon']} stopped")
        for req in setup.get("requests", []):
            created = srv.submit_request(req["student"], req["software"], req["text"], req.get("version", ""))
            log.append(f"request {created['id']} from {created['student']} for {created['software']}")
            if req.get("admin_approved"):
                srv.approve_request(created["id"], "pre-approved by admin for this scenario")
                log.append(f"request {created['id']} approved by admin")
        for key, value in setup.get("state_overrides", {}).items():
            st[key] = value
            log.append(f"state[{key!r}] overridden")
    return log


def apply_admin_action(action: dict) -> list[str]:
    """Run a human-admin step from a scenario's ``steps`` list."""
    log: list[str] = []
    with locked_state() as st:
        srv = LabServer(st, actor="admin")
        if "approve_request" in action:
            srv.approve_request(action["approve_request"])
            log.append(f"admin approved {action['approve_request']}")
        if "upload_license" in action:
            up = action["upload_license"]
            res = srv.upload_license_file(up["vendor"], _features(up["features"]))
            log.append(f"admin uploaded {up['vendor']} license file: " +
                       ", ".join(f"{f['feature']} exp {f['expires']}" for f in res["features"]))
        if "stop_daemon" in action:
            srv.stop_daemon(action["stop_daemon"])
            log.append(f"daemon {action['stop_daemon']} stopped")
        if "maintenance_window" in action:
            srv.set_maintenance_window(bool(action["maintenance_window"]))
            log.append(f"admin set maintenance window = {bool(action['maintenance_window'])}")
    return log
