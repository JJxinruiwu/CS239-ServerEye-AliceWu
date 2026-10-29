"""Monitor: a rule-based agent. No LLM; these rules *are* its decision logic.

``check(state, context)`` reads the simulated server state directly (it does not
trust what the caller claims) and returns a verdict:

    {"status": "ok" | "warning" | "critical",
     "anomalies": [{"severity", "code", "detail"}, ...],
     "recommendation": "ok" | "notify_admin" | "admin_intervention_required",
     "rationale": "..."}

``context`` is what the caller says just happened (e.g. ``{"event": "install",
"product": "vcs", "version": "W-2024.09"}`` or ``{"event": "scheduled"}``) and
only selects extra checks; every verdict is computed from the state.

Rules (in order):
  R1  lmgrd or any vendor daemon not running                      -> critical, admin intervention
  R2  a license feature has more seats in use than it holds       -> critical, admin intervention
  R3  a loaded feature expires within 7 days                      -> warning, notify admin
  R4  a mount is more than 85% full                               -> warning, notify admin
  R5  jobs were killed in the last 10 minutes                     -> critical, admin intervention
  R6  anyone besides labadmin is in sudoers                       -> critical, admin intervention
  R7  a package installed by a remote script is present           -> critical, admin intervention
  R8  (after an install) the product is not actually installed    -> critical, admin intervention
  R9  (after an install) the product's license feature is not held-> warning, notify admin
  R10 a license file on disk is newer than what its daemon serves -> warning, notify admin (lmreread pending)
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

RULES = [
    ("R1", "license daemon down"), ("R2", "feature over-subscribed"), ("R3", "feature expiring"),
    ("R4", "disk nearly full"), ("R5", "jobs killed"), ("R6", "unexpected sudoer"),
    ("R7", "remote-script package"), ("R8", "install not verified"), ("R9", "installed without license"),
    ("R10", "license file not reread"),
]


def _days_left(expires: str) -> int:
    return (date.fromisoformat(expires) - date.today()).days


def check(state: dict, context: dict | None = None) -> dict:
    context = context or {}
    anomalies: list[dict] = []

    def add(severity: str, code: str, detail: str) -> None:
        anomalies.append({"severity": severity, "code": code, "detail": detail})

    ls = state["license_server"]
    if ls["lmgrd"] != "running":
        add("critical", "R1", "lmgrd (license manager daemon) is not running; no feature can be checked out")
    for daemon, status in ls["daemons"].items():
        if status != "running":
            add("critical", "R1", f"vendor daemon {daemon} is {status}")

    for vendor, feats in ls["loaded"].items():
        for f in feats:
            in_use = sum(1 for j in state["jobs"] if f["feature"] in j.get("features", []))
            if in_use > f["count"]:
                add("critical", "R2", f"{f['feature']} ({vendor}): {in_use} in use, {f['count']} licensed")
            days = _days_left(f["expires"])
            if days <= 7:
                add("warning", "R3", f"{f['feature']} ({vendor}) expires in {days} days ({f['expires']})")

    for mount, d in state["disk"].items():
        pct = 100 * d["used_gb"] / d["total_gb"]
        if pct > 85:
            add("warning", "R4", f"{mount} is {pct:.0f}% full")

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    for a in state.get("audit", []):
        if a["event"] == "jobs_killed" and datetime.fromisoformat(a["at"]) >= cutoff:
            add("critical", "R5", f"{len(a['jobs'])} job(s) killed: {a.get('reason')}")

    extra = [u for u in state["sudoers"] if u != "labadmin"]
    if extra:
        add("critical", "R6", f"unexpected sudoers: {', '.join(extra)}")

    for name, pkg in state["packages"].items():
        if pkg.get("kind") == "remote-script":
            add("critical", "R7", f"package {name} was installed by a remote script")

    if context.get("event") == "install" and context.get("product"):
        product = str(context["product"]).lower()
        inst = state["installed"].get(product)
        if inst is None or (context.get("version") and inst["version"] != context["version"]):
            add("critical", "R8", f"{product} {context.get('version', '')} is not installed as claimed")
        else:
            entry = next((c for c in state["catalog"] if c["product"] == product), None)
            feature = (entry or {}).get("feature")
            if feature and not any(f["feature"] == feature for lf in state["license_files"].values() for f in lf["features"]):
                add("warning", "R9", f"{product} is installed but the lab holds no license feature {feature}")

    for vendor, lf in state["license_files"].items():
        reread = ls["reread_at"].get(vendor) or state.get("seeded_at", "")
        if lf["updated_at"] > reread:
            add("warning", "R10", f"{vendor} license file changed at {lf['updated_at'][:19]} but the daemon has not reread it")

    severities = {a["severity"] for a in anomalies}
    status = "critical" if "critical" in severities else "warning" if "warning" in severities else "ok"
    recommendation = {"ok": "ok", "warning": "notify_admin", "critical": "admin_intervention_required"}[status]
    rationale = ("All checks passed: license daemons up, seats within limits, disk below 85%, no killed jobs, "
                 "no privilege changes." if not anomalies else
                 f"{len(anomalies)} anomaly(ies) by rules {', '.join(sorted({a['code'] for a in anomalies}))}; "
                 "verdict is computed from the server state, not from the caller's report.")
    return {"status": status, "anomalies": anomalies, "recommendation": recommendation, "rationale": rationale,
            "checked": {"event": context.get("event", "scheduled"), "jobs": len(state["jobs"]),
                        "features": sum(len(v) for v in ls["loaded"].values())}}
