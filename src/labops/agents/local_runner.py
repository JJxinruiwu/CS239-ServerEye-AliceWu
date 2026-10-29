"""Local multi-agent runtime: no Rakazo, no Docker, full traces.

Each agent is a tool-calling loop against an OpenAI-compatible chat endpoint,
with its MCP servers' tools plus a ``message_bot`` tool. ``message_bot`` runs
the target agent's turn synchronously and returns its final reply, so one
scenario produces one complete, ordered trace of thoughts, actions,
inter-agent messages and observations.

    python -m labops.agents.local_runner --scenario scenarios/01_compliant_install.json
    python -m labops.agents.local_runner --agent LicenseKeeper --message "Run the daily license check." --name adhoc

Environment:
    MODEL_BASE_URL   OpenAI-compatible endpoint (e.g. https://api.openai.com/v1, http://localhost:11434/v1)
    MODEL_ID         a model with reliable tool calling
    MODEL_API_KEY    if needed
    LABOPS_MCP_HOST  where the MCP servers listen (default 127.0.0.1)

Start the MCP servers first:  python -m labops.mcp_servers.serve
Outputs go to traces/<scenario>/ (override with --out).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from contextlib import AsyncExitStack
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from openai import AsyncOpenAI

from ..mcp_servers import SERVERS
from ..sim.scenario import apply_admin_action, apply_setup, load_scenario
from ..sim.state import REPO_ROOT, now_iso, reset_state, state_path
from ..tracing import trace_path
from .profiles import AGENTS, MAX_STEPS, AgentProfile, instructions_for

MAX_DEPTH = 4          # Gatekeeper -> Operator is depth 1; leave headroom
MAX_EVENTS = 400       # hard stop for runaway conversations
MD_TRUNCATE = 1500     # characters of a tool result shown in trace.md (trace.json keeps everything)


def message_bot_spec(targets: tuple[str, ...]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "message_bot",
            "description": "Send a message to another agent of the team and get its reply. "
                           f"Allowed targets: {', '.join(targets)}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "confirm_name": {"type": "string", "enum": list(targets), "description": "the agent to message"},
                    "intent": {"type": "string", "enum": ["request", "question", "result", "status", "fyi"]},
                    "text": {"type": "string", "description": "the message"},
                },
                "required": ["confirm_name", "intent", "text"],
            },
        },
    }


class Trace:
    def __init__(self, meta: dict):
        self.meta = meta
        self.events: list[dict] = []
        self.started = now_iso()

    def add(self, **event) -> dict:
        event = {"seq": len(self.events) + 1, "at": now_iso(), **event}
        self.events.append(event)
        if len(self.events) > MAX_EVENTS:
            raise RuntimeError("trace event limit reached; the agents are probably looping")
        return event

    def to_json(self, agents: dict) -> dict:
        return {
            "scenario": self.meta,
            "runtime": {"kind": "local_runner", "model": os.environ.get("MODEL_ID", ""), "started": self.started, "finished": now_iso()},
            "agents": {name: {"servers": list(a.servers), "may_message": list(a.may_message),
                              "instructions": instructions_for(a, "sync")} for name, a in agents.items()},
            "events": self.events,
        }

    def to_markdown(self) -> str:
        m = self.meta
        out = [f"# Trace: {m.get('title') or m.get('name')}", ""]
        if m.get("description"):
            out += [m["description"], ""]
        if m.get("policies"):
            out += ["**Policies:** " + ", ".join(m["policies"]), ""]
        out += [f"Runtime: local_runner · model: `{os.environ.get('MODEL_ID', '?')}` · started {self.started}", "", "---", ""]
        for e in self.events:
            indent = "↳ " * e.get("depth", 0)
            k = e["kind"]
            if k == "input":
                out += [f"### [{e['seq']}] {indent}{e['from']} → **{e['agent']}** (input)", "", f"> {e['text']}", ""]
            elif k == "thought":
                out += [f"### [{e['seq']}] {indent}**{e['agent']}** · thought", "", e["text"], ""]
            elif k == "action":
                out += [f"### [{e['seq']}] {indent}**{e['agent']}** → tool `{e['server']}.{e['tool']}`", "",
                        "```json", json.dumps(e["args"], indent=2, ensure_ascii=False), "```", ""]
            elif k == "observation":
                body = json.dumps(e.get("result", e.get("error")), indent=2, ensure_ascii=False, default=str)
                if len(body) > MD_TRUNCATE:
                    body = body[:MD_TRUNCATE] + f"\n… ({len(body) - MD_TRUNCATE} more chars in trace.json)"
                label = "observation (ERROR)" if "error" in e else "observation"
                out += [f"### [{e['seq']}] {indent}**{e['agent']}** ← {label} from `{e['tool']}`", "", "```json", body, "```", ""]
            elif k == "message":
                out += [f"### [{e['seq']}] {indent}**{e['from']}** → **{e['to']}** (message_bot, intent={e['intent']})", "", f"> {e['text']}", ""]
            elif k == "reply":
                out += [f"### [{e['seq']}] {indent}**{e['from']}** → **{e['to']}** (reply)", "", f"> {e['text']}", ""]
            elif k == "final":
                out += [f"### [{e['seq']}] {indent}**{e['agent']}** · final answer", "", e["text"], ""]
            elif k == "admin":
                out += [f"### [{e['seq']}] human admin action", "", "- " + "\n- ".join(e["log"]), ""]
            elif k == "note":
                out += [f"> _{e['text']}_", ""]
        return "\n".join(out)


class Runtime:
    def __init__(self, trace: Trace, mcp_host: str = "127.0.0.1", agents: dict[str, AgentProfile] | None = None):
        self.trace = trace
        self.mcp_host = mcp_host
        self.agents = agents or AGENTS
        self.client = AsyncOpenAI(base_url=os.environ.get("MODEL_BASE_URL"), api_key=os.environ.get("MODEL_API_KEY", "none"))
        self.model = os.environ.get("MODEL_ID", "")
        self.sessions: dict[str, ClientSession] = {}
        self.tool_specs: dict[str, list[dict]] = {}
        self.tool_server: dict[str, dict[str, str]] = {}   # agent -> tool name -> server key
        self._stack = AsyncExitStack()

    async def __aenter__(self):
        await self._stack.__aenter__()
        needed = sorted({key for a in self.agents.values() for key in a.servers})
        for key in needed:
            port = int(os.environ.get(f"LABOPS_{key.upper()}_PORT", SERVERS[key]["port"]))
            url = f"http://{self.mcp_host}:{port}/mcp"
            read, write, *_ = await self._stack.enter_async_context(streamable_http_client(url))
            session = await self._stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            tools = (await session.list_tools()).tools
            self.sessions[key] = session
            self.tool_specs[key] = [{
                "type": "function",
                "function": {"name": t.name, "description": t.description or "", "parameters": t.input_schema},
            } for t in tools]
        for name, agent in self.agents.items():
            self.tool_server[name] = {spec["function"]["name"]: key for key in agent.servers for spec in self.tool_specs[key]}
        return self

    async def __aexit__(self, *exc):
        await self._stack.__aexit__(*exc)

    def tools_for(self, agent: AgentProfile) -> list[dict]:
        specs = [spec for key in agent.servers for spec in self.tool_specs[key]]
        if agent.may_message:
            specs.append(message_bot_spec(agent.may_message))
        return specs

    async def call_tool(self, agent_name: str, name: str, args: dict) -> dict:
        key = self.tool_server[agent_name].get(name)
        if key is None:
            return {"error": f"{agent_name} has no tool named {name}"}
        result = await self.sessions[key].call_tool(name, args)
        if result.is_error:
            return {"error": "".join(c.text for c in result.content if getattr(c, "type", "") == "text")}
        if result.structured_content is not None:
            return result.structured_content
        text = "".join(c.text for c in result.content if getattr(c, "type", "") == "text")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}

    async def run_agent(self, agent_name: str, text: str, sender: str = "admin", depth: int = 0) -> str:
        agent = self.agents[agent_name]
        self.trace.add(kind="input", agent=agent_name, **{"from": sender}, text=text, depth=depth)
        messages = [{"role": "system", "content": instructions_for(agent, "sync")},
                    {"role": "user", "content": text}]
        tools = self.tools_for(agent)
        for _ in range(MAX_STEPS):
            response = await self.client.chat.completions.create(
                model=self.model, messages=messages, tools=tools, tool_choice="auto")
            msg = response.choices[0].message
            reasoning = getattr(msg, "reasoning_content", None) or (msg.model_extra or {}).get("reasoning_content") \
                or (msg.model_extra or {}).get("reasoning")
            if reasoning:
                self.trace.add(kind="thought", agent=agent_name, text=str(reasoning), depth=depth, source="reasoning")
            if not msg.tool_calls:
                final = msg.content or ""
                self.trace.add(kind="final", agent=agent_name, text=final, depth=depth)
                return final
            if msg.content:
                self.trace.add(kind="thought", agent=agent_name, text=msg.content, depth=depth, source="content")
            messages.append({"role": "assistant", "content": msg.content or "",
                             "tool_calls": [{"id": tc.id, "type": "function",
                                             "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
                                            for tc in msg.tool_calls]})
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {"_raw": tc.function.arguments}
                if tc.function.name == "message_bot":
                    target = str(args.get("confirm_name", ""))
                    intent = str(args.get("intent", "request"))
                    body = str(args.get("text", ""))
                    self.trace.add(kind="message", **{"from": agent_name}, to=target, intent=intent, text=body, depth=depth)
                    if target not in agent.may_message or target not in self.agents:
                        result = {"error": f"{agent_name} may only message {', '.join(agent.may_message)}"}
                    elif depth >= MAX_DEPTH:
                        result = {"error": "message depth limit reached"}
                    else:
                        reply = await self.run_agent(target, body, sender=agent_name, depth=depth + 1)
                        self.trace.add(kind="reply", **{"from": target}, to=agent_name, text=reply, depth=depth)
                        result = {"reply": reply}
                else:
                    server = self.tool_server[agent_name].get(tc.function.name, "?")
                    self.trace.add(kind="action", agent=agent_name, server=server, tool=tc.function.name, args=args,
                                   call_id=tc.id, depth=depth)
                    started = time.perf_counter()
                    try:
                        result = await self.call_tool(agent_name, tc.function.name, args)
                    except Exception as exc:  # noqa: BLE001 - keep the run going, record the failure
                        result = {"error": f"{type(exc).__name__}: {exc}"}
                    key = "error" if isinstance(result, dict) and set(result) == {"error"} else "result"
                    self.trace.add(kind="observation", agent=agent_name, tool=tc.function.name, call_id=tc.id,
                                   depth=depth, ms=round((time.perf_counter() - started) * 1000, 1),
                                   **{key: result["error"] if key == "error" else result})
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, default=str)})
        limit = f"[{agent_name} stopped: step limit {MAX_STEPS} reached]"
        self.trace.add(kind="final", agent=agent_name, text=limit, depth=depth)
        return limit


async def run_scenario(scenario: dict, out_root: Path, mcp_host: str, reset: bool = True) -> Path:
    meta = {k: scenario.get(k) for k in ("name", "title", "description", "policies",
                                          "expected_outcome", "without_enforcement")}
    trace = Trace(meta)
    if reset:
        reset_state()
        tp = trace_path()
        tp.parent.mkdir(parents=True, exist_ok=True)
        tp.write_text("", encoding="utf-8")
        log = apply_setup(scenario["setup"])
        trace.add(kind="note", text="setup: " + "; ".join(log) if log else "setup: fresh seed state")
    async with Runtime(trace, mcp_host) as rt:
        for step in scenario["steps"]:
            if "admin" in step:
                trace.add(kind="admin", log=apply_admin_action(step["admin"]))
            else:
                await rt.run_agent(step["agent"], step["message"], sender=step.get("from", "admin"))
    out_dir = out_root / scenario["name"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "trace.json").write_text(json.dumps(trace.to_json(AGENTS), indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    (out_dir / "trace.md").write_text(trace.to_markdown(), encoding="utf-8")
    (out_dir / "scenario.json").write_text(json.dumps(scenario, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if trace_path().exists():
        shutil.copy(trace_path(), out_dir / "tool_calls.jsonl")
    if state_path().exists():
        shutil.copy(state_path(), out_dir / "final_state.json")
    return out_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", help="scenario JSON (scenarios/*.json)")
    parser.add_argument("--agent", choices=sorted(AGENTS), help="ad-hoc: which agent to kick off")
    parser.add_argument("--message", help="ad-hoc: the kickoff message")
    parser.add_argument("--name", default="adhoc", help="ad-hoc: trace folder name")
    parser.add_argument("--out", default=str(REPO_ROOT / "traces"), help="output root")
    parser.add_argument("--mcp-host", default=os.environ.get("LABOPS_MCP_HOST", "127.0.0.1"))
    parser.add_argument("--no-reset", action="store_true", help="keep the current lab state and trace file")
    args = parser.parse_args()

    for var in ("MODEL_BASE_URL", "MODEL_ID"):
        if not os.environ.get(var):
            parser.error(f"set {var} (and MODEL_API_KEY if the endpoint needs one)")
    if args.scenario:
        scenario = load_scenario(args.scenario)
    elif args.agent and args.message:
        scenario = {"name": args.name, "title": f"ad hoc: {args.agent}", "setup": {},
                    "steps": [{"agent": args.agent, "message": args.message}]}
    else:
        parser.error("give --scenario FILE, or --agent NAME --message TEXT")
    out_dir = asyncio.run(run_scenario(scenario, Path(args.out), args.mcp_host, reset=not args.no_reset))
    print(f"trace written to {out_dir}/trace.md (+ trace.json, tool_calls.jsonl, final_state.json)")


if __name__ == "__main__":
    main()
