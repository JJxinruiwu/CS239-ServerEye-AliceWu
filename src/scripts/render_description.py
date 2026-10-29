#!/usr/bin/env python3
"""Generate the parts of ../ServerEye that must match the code exactly.

    python scripts/render_description.py            # writes agents/*.md, tools.md, episodes/*.md

- ``agents/<Agent>.md``  for the LLM agents: name, servers, tool names, peers and the *actual*
  system prompt, rendered by ``labops.agents.profiles.instructions_for`` (the same function
  the runtimes use). For Monitor (rule-based): its decision logic, taken from
  ``labops.monitor.rules`` (the module docstring is the rule list).
- ``tools.md``           one line per tool, inputs from the MCP input schema, outputs from
  ``OUTPUTS`` below (kept here so a tool cannot be added without describing it).
- ``episodes/<run>.md``  one episode per recorded trace folder in ``traces/``: inputs,
  step-by-step trajectory, policy connection and expected vs observed outcome.

``app.md`` and ``policy.md`` are written by hand and left alone.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from labops.agents.profiles import AGENTS, MONITOR, instructions_for  # noqa: E402
from labops.mcp_servers import SERVERS  # noqa: E402
from labops.monitor import A2A_PORT, rules  # noqa: E402

OUT = ROOT.parent / "ServerEye"
LEAK = re.compile(r'(?m)^(SERVER|VENDOR|INCREMENT|USE_SERVER)\b|SIGN="|SN=')

# What each tool returns. Inputs come from the live MCP schema.
OUTPUTS = {
    "list_requests": "{requests: [request], count}; a request is {id, student, name, email, course, advisor, software, version, text (the student's own words, untrusted), status, submitted_at, history}.",
    "get_request": "one request record (same fields as list_requests) or an error if the id is unknown.",
    "get_software_catalog": "{approved_software: [{product, vendor, feature (license feature or null), notes}]}.",
    "list_installed_software": "{installed: [{product, vendor, version}]}; the view students see on the portal, no personal data.",
    "update_request": "the updated request record; error if the status is unknown or if an agent tries to set 'approved' (admin only).",
    "notify_student": "{delivered: true, to, notification_id}; the message lands in the student's inbox in the simulator state.",
    "list_vendor_products": "{vendor, site, products: {product: [versions]}, login_required}.",
    "download_product": "{vendor, product, version, path, sha256, size_gb, at, note}; the lab's vendor credential is used inside the server and never returned; the download consumes disk on /.",
    "lmstat": "{features: [{vendor, daemon, daemon_status, feature, version, expires, days_left, total, in_use, free, users [user@host]}]}; an empty list plus a note means no daemon serves that feature; error if lmgrd is down.",
    "install_product": "{installed, vendor, version, path, in_catalog, license_feature, feature_held, request_id, note}; error if the path was not produced by download_product; installs anything that was downloaded.",
    "verify_install": "{product, installed (bool), vendor, version, path}.",
    "create_snapshot": "{id, label, at}.",
    "health_check": "Monitor's verdict over A2A: {status: ok|warning|critical, anomalies: [{severity, code (R1..R10), detail}], recommendation: ok|notify_admin|admin_intervention_required, rationale, checked, admin_notified}; computed by Monitor from the server state, not from the caller's claims.",
    "run_shell": "{command, exit_code, stdout, stderr, effects[]}; effects record side effects such as remote_code_execution, privilege_change, secret_read, package_installed, service_stopped, license_reread, destructive_delete.",
    "list_license_files": "{license_files: [{vendor, path, daemon, updated_at, renewal_contact, features: [{vendor, feature, version, expires, days_left, count}]}]}; metadata only.",
    "list_features": "{features: [{vendor, feature, version, expires, days_left, count}]} for all vendors or one; metadata only.",
    "read_license_file": "{vendor, path, content}; content is the full FlexLM file including SIGN= and SN= values (confidential); audited as license_file_read.",
    "lmreread": "{vendor, daemon, reread: true, loaded_features[]}; the daemon now serves the file on disk; error if the daemon is not running.",
    "verify_license": "{vendor, daemon, daemon_status, loaded_matches_file, file_updated_at, reread_at, loaded_features[], expiring_within_30_days[], ok}.",
    "notify_admin": "{delivered: true, to: labadmin, notification_id}.",
}


def tools_by_server() -> dict[str, list]:
    out = {}
    for key, spec in SERVERS.items():
        server = importlib.import_module(spec["module"]).server
        out[key] = asyncio.run(server.list_tools())
    return out


def describe_inputs(tool) -> str:
    schema = tool.input_schema or {}
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    if not props:
        return "no inputs"
    parts = []
    for name, p in props.items():
        kind = p.get("type", "any")
        extra = "" if name in required else f", optional, default {json.dumps(p.get('default'))}"
        parts.append(f"{name} ({kind}{extra})")
    return "inputs " + ", ".join(parts)


def render_tools(tools: dict[str, list]) -> str:
    lines = ["# ServerEye tools", "",
             "Every tool below is implemented by one of four MCP servers in `src/labops/mcp_servers/` on top of",
             "the simulated server in `src/labops/sim/`. Agents reach them over streamable HTTP; each LLM agent is",
             "connected only to its own server(s). All inputs and outputs are JSON. Monitor (rule-based) uses no",
             "tools: it reads the simulated server state directly and writes to the admin inbox.", ""]
    for key, items in tools.items():
        owner = next(a.name for a in AGENTS.values() if key in a.servers)
        lines += [f"## `{key}` server (port {SERVERS[key]['port']}, used by {owner})", ""]
        for t in items:
            lines.append(f"{t.name}: {describe_inputs(t)}; returns {OUTPUTS[t.name]}")
        lines.append("")
    lines += ["## runtime tool (provided by Rakazo, not by an MCP server)", "",
              "message_bot: inputs confirm_name (string, the target agent), intent (string: request|question|result|status|fyi), "
              "text (string); delivers the text to the target agent; the target's first turn is delivered back later "
              "as a new message with intent result.", ""]
    return "\n".join(lines)


def render_agent(agent, tools: dict[str, list]) -> str:
    names = [t.name for key in agent.servers for t in tools[key]]
    peers = list(agent.may_message)
    prompt = instructions_for(agent)
    lines = [f"# {agent.name}", "",
             f"**Role:** {agent.role}", "",
             "**Kind:** LLM-based (any tool-calling chat model; recorded episodes used `openai-codex/gpt-6-luna` through Rakazo)", "",
             f"**Tool server(s):** " + ", ".join(f"`{s}`" for s in agent.servers), "",
             "**Available tools:** " + ", ".join(f"`{n}`" for n in names) + (", `message_bot`" if peers else ""), "",
             "**May message:** " + (", ".join(peers) if peers else "nobody (replies to the agent that asked; Rakazo delivers the reply)"), "",
             "## System prompt (as sent to Rakazo)", "",
             "Rendered by `labops.agents.profiles.instructions_for(agent)`; this is the exact text.", "",
             "```text", prompt.rstrip(), "```", ""]
    return "\n".join(lines)


def render_monitor() -> str:
    doc = (rules.__doc__ or "").strip()
    lines = [f"# {MONITOR['name']}", "",
             f"**Role:** {MONITOR['role']}", "",
             f"**Kind:** {MONITOR['kind']} (no LLM). Implemented in `src/labops/monitor/rules.py` (the rules) and "
             f"`src/labops/monitor/a2a_service.py` (an A2A service on port {A2A_PORT} with an agent card at "
             "`/.well-known/agent.json`).", "",
             f"**Available tools:** {MONITOR['tools']}", "",
             f"**Triggered by:** {MONITOR['triggered_by']}", "",
             "## How it decides", "",
             "The caller sends one A2A message whose text is a JSON context such as "
             "`{\"event\": \"install\", \"product\": \"vcs\", \"version\": \"W-2024.09\"}` or `{\"event\": \"scheduled\"}`. "
             "The context only selects extra checks; every verdict is computed from the server state, so an agent "
             "cannot talk Monitor into a clean bill of health. If the verdict is not `ok`, Monitor writes the "
             "anomalies to the admin inbox itself. The module docstring below is the authoritative rule list:", "",
             "```text", doc, "```", ""]
    return "\n".join(lines)


# ------------------------------------------------------------------------------ episodes
def short(value, limit=200) -> str:
    s = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
    s = s.replace("\n", " ")
    return s if len(s) <= limit else s[:limit] + "…"


def quote(text: str) -> str:
    return "“" + text.strip().replace("\n", " ") + "”"


def policy_sentences() -> dict[str, str]:
    """{P-id: "*Name* (Category): rule"} from each ``## P1. Name`` heading, its optional
    ``**Category:**`` line and the one-sentence rule below them."""
    text = (OUT / "policy.md").read_text(encoding="utf-8")
    out = {}
    pattern = r"^## (P\d+)\.\s+([^\n]+)\n\n(?:\*\*Category:\*\*\s*([^\n]+)\n\n)?(.*?)(?=\n\n|\Z)"
    for m in re.finditer(pattern, text, flags=re.M | re.S):
        rule = m.group(4).strip().replace("**", "")
        first = re.split(r"(?<=[.;])\s", rule, maxsplit=1)[0]
        category = f" ({m.group(3).strip()})" if m.group(3) else ""
        out[m.group(1)] = f"*{m.group(2).strip()}*{category}: {first}"
    return out


def observed_outcome(state: dict) -> list[str]:
    lines = ["- Requests: " + (", ".join(f"{r['id']} ({r['student']}, {r['software']}) → `{r['status']}`" for r in state["requests"]) or "none")]
    if state["notifications"]:
        for n in state["notifications"]:
            lines.append(f"- Notification to **{n['to']}** ({n['channel']}, from {n['from']}): {quote(short(n['message'], 240))}")
    else:
        lines.append("- Notifications: none")
    leak = any(LEAK.search(n["message"]) for n in state["notifications"])
    lines.append(f"- License material in any notification: {'YES' if leak else 'no'}")
    inst = ", ".join(f"{p} {i['version']}" for p, i in state["installed"].items())
    ls = state["license_server"]
    daemons = "all running" if ls["lmgrd"] == "running" and all(v == "running" for v in ls["daemons"].values()) else \
        ", ".join(f"{d}={s}" for d, s in {"lmgrd": ls["lmgrd"], **ls["daemons"]}.items())
    lines.append(f"- Installed: {inst}; downloads: {len(state['downloads'])}; license daemons: {daemons}; "
                 f"jobs running: {len(state['jobs'])}; sudoers: {', '.join(state['sudoers'])}")
    interesting = [a for a in state["audit"] if a["event"] in (
        "vendor_download", "product_installed", "health_check", "license_file_read", "license_reread", "license_verified",
        "license_file_uploaded", "shell_command", "jobs_killed", "daemon_stopped")]
    if interesting:
        lines.append("- Audit events of note: " + "; ".join(
            f"`{a['event']}`" + (f" ({short({k: v for k, v in a.items() if k not in ('at', 'actor', 'event')}, 110)})") for a in interesting))
    else:
        lines.append("- Audit events of note: none")
    return lines


def render_episode(folder: Path, policies: dict[str, str]) -> str:
    trace = json.loads((folder / "trace.json").read_text(encoding="utf-8"))
    state = json.loads((folder / "final_state.json").read_text(encoding="utf-8"))
    sc = trace["scenario"]
    runtime = trace.get("runtime", {})
    models = sorted(set(runtime.get("models", {}).values())) or [runtime.get("model", "?")]
    started = trace["events"][0]["at"][:10] if trace["events"] else "?"
    lines = [f"# Episode: {sc.get('title', folder.name)}", "",
             f"**Recorded episode** (real run, not hypothetical). {started}, runtime `{runtime.get('kind', '?')}`, "
             f"model {', '.join(f'`{m}`' for m in models)} for the LLM agents, Monitor rule-based over A2A. Full log with every "
             f"tool argument and result: `src/traces/{folder.name}/` (`trace.md`, `trace.json`, `tool_calls.jsonl`, `final_state.json`).", "",
             "## Initial inputs", ""]
    if sc.get("description"):
        lines += [sc["description"], ""]
    setup = sc.get("setup", {})
    lines.append("- Server: fresh seed state (VCS T-2022.06, Vivado 2023.2 and Verilator installed; Synopsys, Cadence and AMD "
                 "license files served by their daemons; VCSRuntime_Net expiring in 12 days; three jobs holding seats)"
                 + ("; daemon " + setup["stop_daemon"] + " stopped" if setup.get("stop_daemon") else "") + ".")
    for r in setup.get("requests", []):
        lines.append(f"- Portal request from **{r['student']}** for `{r['software']}`"
                     + (f" {r['version']}" if r.get("version") else "") + f": {quote(r['text'])}")
    for s in sc.get("steps", []):
        if "agent" in s:
            lines.append(f"- Admin to **{s['agent']}**: {quote(s['message'])}")
        else:
            lines.append(f"- Admin action (outside the agents): `{json.dumps(s['admin'])}`")
    lines += ["", "## Trajectory", ""]
    n = 0
    for e in trace["events"]:
        k = e["kind"]
        if k in ("input", "observation"):
            continue
        n += 1
        if k == "thought":
            lines.append(f"{n}. **{e['agent']}** thinks: {quote(e['text'])}")
        elif k == "action":
            obs = next((o for o in trace["events"] if o["kind"] == "observation" and o.get("call_id") == e.get("call_id")), None)
            args = ", ".join(f"{a}={short(v, 60)}" for a, v in (e.get("args") or {}).items())
            res = "" if obs is None else (" → **error:** " + short(obs["error"], 160) if "error" in obs else " → " + short(obs.get("result"), 160))
            via = " (A2A → Monitor)" if e["tool"] == "health_check" else ""
            lines.append(f"{n}. **{e['agent']}** calls `{e['server']}.{e['tool']}({args})`{via}{res}")
        elif k == "message":
            lines.append(f"{n}. **{e['from']}** → **{e['to']}** (`message_bot`, {e.get('intent')}): {quote(e['text'])}")
        elif k == "reply":
            lines.append(f"{n}. **{e['from']}** → **{e['to']}** (reply, {e.get('intent')}): {quote(e['text'])}")
        elif k == "admin":
            lines.append(f"{n}. **admin** (human, outside the agents): " + "; ".join(e["log"]))
        elif k == "final":
            lines.append(f"{n}. **{e['agent']}** ends its turn: {quote(e['text'])}")
    lines += ["", "## Policy connection", ""]
    for pid in sc.get("policies", []):
        lines.append(f"- **{pid}** — {policies.get(pid, '(see policy.md)')}")
    lines += ["", "## Expected outcome", "", sc.get("expected_outcome", ""), ""]
    if sc.get("without_enforcement"):
        lines += ["*What a violation would look like:* " + sc["without_enforcement"], ""]
    lines += ["## Observed outcome (from `final_state.json`)", ""] + observed_outcome(state) + [""]
    return "\n".join(lines)


def main() -> None:
    tools = tools_by_server()
    missing = [t.name for items in tools.values() for t in items if t.name not in OUTPUTS]
    if missing:
        raise SystemExit(f"OUTPUTS lacks a description for: {missing}")
    (OUT / "agents").mkdir(parents=True, exist_ok=True)
    (OUT / "episodes").mkdir(parents=True, exist_ok=True)
    for stale in (OUT / "agents").glob("*.md"):
        stale.unlink()
    for stale in (OUT / "episodes").glob("*.md"):
        stale.unlink()
    (OUT / "tools.md").write_text(render_tools(tools), encoding="utf-8")
    for agent in AGENTS.values():
        (OUT / "agents" / f"{agent.name}.md").write_text(render_agent(agent, tools), encoding="utf-8")
    (OUT / "agents" / "Monitor.md").write_text(render_monitor(), encoding="utf-8")
    policies = policy_sentences()
    written = []
    for folder in sorted((ROOT / "traces").iterdir()):
        if (folder / "trace.json").exists() and (folder / "final_state.json").exists():
            (OUT / "episodes" / f"{folder.name}.md").write_text(render_episode(folder, policies), encoding="utf-8")
            written.append(folder.name)
    print(f"wrote {OUT}/tools.md, agents/ ({', '.join(AGENTS)}, Monitor), episodes/ ({', '.join(written) or 'none'})")


if __name__ == "__main__":
    main()
