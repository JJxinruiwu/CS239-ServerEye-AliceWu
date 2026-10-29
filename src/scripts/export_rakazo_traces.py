#!/usr/bin/env python3
"""Export a Rakazo run as a trace folder (raw threads + tool calls + a merged timeline).

    python scripts/export_rakazo_traces.py --name 01_compliant_install --out traces \
        --scenario scenarios/01_compliant_install.json

Reads RAKAZO_URL / RAKAZO_EMAIL / RAKAZO_PASSWORD (prompted if unset). For each
of the four bots it saves the thread returned by Rakazo's ``threads/get`` as
``rakazo_thread_<Bot>.json``, copies ``traces/raw/tool_calls.jsonl`` (the MCP
side of the story) and ``lab/state.json`` (the evidence: notifications, audit),
and writes ``trace.json`` + ``trace.md``: one timeline of both, ordered by time.

Thread schema (Rakazo edge, Oct 2026): ``messages[]`` with ``seq``, ``role``
(user/bot), ``createdAt``, ``runId`` and ``blocks[]``, each block one of
``text`` (kickoff input, or what the bot says), ``bot_message_sent``
(``toBotName``, ``intent``, ``text``), ``bot_message_received``
(``fromBotName``, ``intent``, ``text``) and ``steps`` (a tool-call summary; the
arguments and results come from the MCP log instead).
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from labops.agents.profiles import AGENTS, instructions_for  # noqa: E402
from labops.agents.rakazo import Rakazo  # noqa: E402
from labops.sim.state import state_path  # noqa: E402
from labops.tracing import trace_path  # noqa: E402

MD_TRUNCATE = 1500
# MCP log entries carry the server actor ("portal-mcp"); map it back to the agent that owns it.
AGENT_OF_ACTOR = {f"{key}-mcp": a.name for a in AGENTS.values() for key in a.servers}
AGENT_OF_ACTOR.update({"monitor-a2a": "Monitor", "admin": "admin"})


def _at(iso: str) -> str:
    """Normalise both timestamp styles (``...Z`` with ms, ``+00:00`` without) to sortable UTC ms."""
    iso = iso.replace("Z", "+00:00")
    base, _, offset = iso.partition("+")
    if "." not in base:
        base += ".000"
    return base[:23] + "+" + (offset or "00:00")


def thread_events(thread: dict, bot: str, actions: list[dict]) -> list[dict]:
    """Events from one bot's thread. ``actions`` are that bot's MCP actions in time order.

    Rakazo stores a whole turn as one message with one timestamp, but its blocks keep their
    order: text, steps (N tool calls), text, ... Each text block is therefore placed just
    before the next tool call it precedes, so a thought sits next to the action it explains.
    """
    events = []
    # steps count both MCP calls and message_bot calls, so both anchor the text before them
    sent = [{"at": _at(m["createdAt"])} for m in thread.get("messages", [])
            for b in m.get("blocks", []) if b.get("kind") == "bot_message_sent"]
    pending = sorted(list(actions) + sent, key=lambda e: e["at"])
    for m in thread.get("messages", []):
        stamp = _at(m["createdAt"])
        for b in m.get("blocks", []):
            kind = b.get("kind")
            # only a bot's own text shares a turn's timestamp; messages carry their exact time
            aligned = kind == "text" and m.get("role") == "bot" and pending and pending[0]["at"] <= stamp
            at = pending[0]["at"] if aligned else stamp
            common = {"at": at, "agent": bot, "rakazo_seq": m.get("seq"), "run_id": m.get("runId")}
            if kind == "steps":
                done = sum(int(s.get("count", 1)) for s in b.get("steps") or []
                           if s.get("label", "").lower().startswith(("mcp", "message bot")))
                del pending[:done]
            elif kind == "text" and m.get("role") == "user":
                events.append({**common, "kind": "input", "from": "admin", "text": b.get("text", "").strip()})
            elif kind == "text":
                events.append({**common, "kind": "thought", "text": b.get("text", "").strip()})
            elif kind == "bot_message_sent":
                events.append({**common, "kind": "message" if b.get("intent") != "result" else "reply",
                               "from": bot, "to": b.get("toBotName"), "intent": b.get("intent"),
                               "text": b.get("text", "").strip()})
            # bot_message_received repeats the sender's bot_message_sent; the sender's copy is kept
    # a bot's last text of a run is its final answer for that turn
    last_text_of_run: dict[str, dict] = {}
    for e in events:
        if e["kind"] == "thought":
            last_text_of_run[e["run_id"]] = e
    for e in last_text_of_run.values():
        e["kind"] = "final"
    return events


def _caller_first(calls: list[dict]) -> list[dict]:
    """The log records when a call *finished*, to the second. Monitor's own health_check entry is
    written inside Operator's server.health_check call, so it lands first with the same timestamp.
    Put each such Monitor entry right after the call that made it, so the timeline reads
    'Operator asks -> Monitor answers'."""
    calls = list(calls)
    for i, c in enumerate(calls):
        if c.get("server") == "monitor-a2a":
            for j in range(i + 1, min(i + 4, len(calls))):
                d = calls[j]
                if d.get("server") == "server-mcp" and d.get("tool") == "health_check" and d["at"][:19] == c["at"][:19]:
                    calls.insert(j, calls.pop(i))
                    break
    return calls


def tool_events(calls: list[dict]) -> list[dict]:
    events = []
    calls = _caller_first(calls)
    for c in calls:
        if c.get("server") == "admin":
            events.append({"at": _at(c["at"]), "kind": "admin", "agent": "admin", "action": c.get("args"),
                           "log": (c.get("result") or {}).get("log", [])})
            continue
        agent = AGENT_OF_ACTOR.get(c.get("server"), c.get("server"))
        server = c.get("server", "").removesuffix("-mcp")
        events.append({"at": _at(c["at"]), "kind": "action", "agent": agent, "server": server,
                       "tool": c["tool"], "args": c.get("args"), "call_id": c.get("call_id")})
        obs = {"at": _at(c["at"]), "kind": "observation", "agent": agent, "tool": c["tool"],
               "call_id": c.get("call_id"), "ms": c.get("ms")}
        obs["error" if c.get("error") else "result"] = c.get("error") or c.get("result")
        events.append(obs)
    return events


def to_markdown(name: str, scenario: dict | None, events: list[dict]) -> str:
    title = (scenario or {}).get("title") or name
    out = [f"# Trace: {title}", ""]
    if scenario and scenario.get("description"):
        out += [scenario["description"], ""]
    if scenario and scenario.get("policies"):
        out += ["**Policies:** " + ", ".join(scenario["policies"]), ""]
    out += ["Runtime: Rakazo (asynchronous message_bot). Tool calls from the MCP servers' own log; "
            "thoughts and messages from each bot's thread.", "", "---", ""]
    for e in events:
        t = e["at"][11:19]
        k = e["kind"]
        if k == "input":
            out += [f"### [{e['seq']}] {t} {e['from']} → **{e['agent']}** (input)", "", f"> {e['text']}", ""]
        elif k == "thought":
            out += [f"### [{e['seq']}] {t} **{e['agent']}** · thought", "", e["text"], ""]
        elif k == "final":
            out += [f"### [{e['seq']}] {t} **{e['agent']}** · end of turn", "", e["text"], ""]
        elif k == "message":
            out += [f"### [{e['seq']}] {t} **{e['from']}** → **{e['to']}** (message_bot, intent={e['intent']})", "",
                    f"> {e['text']}", ""]
        elif k == "reply":
            out += [f"### [{e['seq']}] {t} **{e['from']}** → **{e['to']}** (delivered, intent={e['intent']})", "",
                    f"> {e['text']}", ""]
        elif k == "action":
            out += [f"### [{e['seq']}] {t} **{e['agent']}** → tool `{e['server']}.{e['tool']}`", "",
                    "```json", json.dumps(e["args"], indent=2, ensure_ascii=False), "```", ""]
        elif k == "admin":
            out += [f"### [{e['seq']}] {t} human admin action", "", "- " + "\n- ".join(e["log"]), ""]
        elif k == "observation":
            body = json.dumps(e.get("result", e.get("error")), indent=2, ensure_ascii=False, default=str)
            if len(body) > MD_TRUNCATE:
                body = body[:MD_TRUNCATE] + f"\n… ({len(body) - MD_TRUNCATE} more chars in trace.json)"
            label = "observation (ERROR)" if "error" in e else "observation"
            out += [f"### [{e['seq']}] {t} **{e['agent']}** ← {label} from `{e['tool']}`", "",
                    "```json", body, "```", ""]
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", help="trace folder name, e.g. 01_compliant_install")
    parser.add_argument("--out", default="traces")
    parser.add_argument("--scenario", help="scenario JSON that produced the run (copied into the folder)")
    parser.add_argument("--rebuild", metavar="DIR", nargs="+",
                        help="regenerate trace.json/trace.md from the files already saved in DIR (no Rakazo needed)")
    args = parser.parse_args()

    if args.rebuild:
        for folder in args.rebuild:
            rebuild(Path(folder))
        return
    if not args.name:
        parser.error("--name is required unless --rebuild is given")
    url = os.environ.get("RAKAZO_URL") or "http://127.0.0.1:5173"
    email = os.environ.get("RAKAZO_EMAIL") or input("Rakazo email: ")
    password = os.environ.get("RAKAZO_PASSWORD") or getpass.getpass("Rakazo password: ")
    scenario = json.loads(Path(args.scenario).read_text(encoding="utf-8")) if args.scenario else None
    with Rakazo(url, origin=os.environ.get("RAKAZO_ORIGIN")) as app:
        app.login(email, password)
        export_run(app, args.name, Path(args.out), scenario, url)


def read_calls(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def export_run(app: Rakazo, name: str, out_root: Path, scenario: dict | None, url: str) -> Path:
    """Save the run that is in the bots' threads and the MCP log now, then build its trace."""
    out_dir = out_root / name
    out_dir.mkdir(parents=True, exist_ok=True)
    bots = {b["name"]: b for b in app.list_bots()}
    for bot_name in AGENTS:
        if bot_name not in bots:
            print(f"bot {bot_name} not found in Rakazo; skipping")
            continue
        thread = app.conversation(bots[bot_name]["id"])
        if thread.get("olderCursor"):
            print(f"warning: {bot_name}'s thread has older pages that were not exported")
        (out_dir / f"rakazo_thread_{bot_name}.json").write_text(
            json.dumps(thread, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if trace_path().exists():
        shutil.copy(trace_path(), out_dir / "tool_calls.jsonl")
    if state_path().exists():
        shutil.copy(state_path(), out_dir / "final_state.json")
    if scenario:
        (out_dir / "scenario.json").write_text(json.dumps(scenario, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    runtime = {"kind": "rakazo", "rakazo_url": url,
               "models": {n: f"{b.get('modelProvider')}/{b.get('modelId')}" for n, b in bots.items() if n in AGENTS}}
    (out_dir / "runtime.json").write_text(json.dumps(runtime, indent=2) + "\n", encoding="utf-8")
    return rebuild(out_dir)


def rebuild(out_dir: Path) -> Path:
    """Build trace.json and trace.md from a trace folder's raw files (threads + MCP log)."""
    name = out_dir.name
    scenario_file = out_dir / "scenario.json"
    scenario = json.loads(scenario_file.read_text(encoding="utf-8")) if scenario_file.exists() else None
    runtime_file = out_dir / "runtime.json"
    runtime = json.loads(runtime_file.read_text(encoding="utf-8")) if runtime_file.exists() else {"kind": "rakazo"}
    tools = tool_events(read_calls(out_dir / "tool_calls.jsonl"))
    events: list[dict] = []
    for bot_name in AGENTS:
        thread_file = out_dir / f"rakazo_thread_{bot_name}.json"
        if thread_file.exists():
            actions = [e for e in tools if e["kind"] == "action" and e["agent"] == bot_name]
            events += thread_events(json.loads(thread_file.read_text(encoding="utf-8")), bot_name, actions)

    # at equal timestamps a thought comes before the action or message it explains;
    # the sort is stable, so an action stays before its observation
    timeline = sorted(events + tools, key=lambda e: (e["at"], 0 if e["kind"] == "thought" else 1))
    for i, e in enumerate(timeline, 1):
        e["seq"] = i
    trace = {
        "scenario": scenario or {"name": name},
        "runtime": runtime,
        "agents": {n: {"servers": list(a.servers), "may_message": list(a.may_message),
                       "instructions": instructions_for(a, "async")} for n, a in AGENTS.items()},
        "events": timeline,
    }
    (out_dir / "trace.json").write_text(json.dumps(trace, indent=2, ensure_ascii=False, default=str) + "\n",
                                        encoding="utf-8")
    (out_dir / "trace.md").write_text(to_markdown(name, scenario, timeline), encoding="utf-8")
    kinds = {k: sum(1 for e in timeline if e["kind"] == k) for k in
             ("input", "thought", "message", "reply", "action", "admin", "final")}
    print(f"wrote {out_dir}/trace.json + trace.md: {len(timeline)} events {kinds}")
    return out_dir


if __name__ == "__main__":
    main()
