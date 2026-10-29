"""Define the four-bot team for Rakazo. Preview by default; --apply creates it.

Mirrors the course tutorial's ``examples/movie_night/app.py``:

    python -m labops.agents.rakazo_app                 # print instructions, no network
    python -m labops.agents.rakazo_app --apply         # log in, create bots + MCP assignments
    python -m labops.agents.rakazo_app --apply --send "Process the pending portal requests."
    python -m labops.agents.rakazo_app --update        # push edited instructions to existing bots
    python -m labops.agents.rakazo_app --auto-approve  # Phase 1: no per-call human review of lab tools

Environment (same names as the tutorial):
    RAKAZO_URL      e.g. http://127.0.0.1:5173
    RAKAZO_EMAIL    your Rakazo account
    RAKAZO_PASSWORD optional; prompted otherwise
    MODEL_PROVIDER  openai-compatible (default) or a provider already connected in the
                    Rakazo UI, e.g. openai-codex for a ChatGPT subscription sign-in
    MODEL_BASE_URL  OpenAI-compatible endpoint, e.g. https://api.openai.com/v1
                    (openai-compatible only)
    MODEL_ID        a tool-calling capable model
    MODEL_API_KEY   if the endpoint needs one (never put it in a file)
    MCP_HOST        host the Dockerised Rakazo uses to reach the MCP servers;
                    default host.docker.internal (use 127.0.0.1 for a native Rakazo)

Start the MCP servers first:  python -m labops.mcp_servers.serve
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import importlib
import os

from ..mcp_servers import SERVERS
from .profiles import AGENTS, AgentProfile, instructions_for
from .rakazo import BotProfile, MCPConnection, ModelConnection, Rakazo


def mcp_connection(key: str, host: str) -> MCPConnection:
    spec = SERVERS[key]
    port = int(os.environ.get(f"LABOPS_{key.upper()}_PORT", spec["port"]))
    return MCPConnection(name=spec["title"], slug=f"lab-{key}", endpoint=f"http://{host}:{port}/mcp")


def bot_profile(agent: AgentProfile, model: ModelConnection, host: str) -> BotProfile:
    return BotProfile(
        name=agent.name,
        model=model,
        mcp=tuple(mcp_connection(key, host) for key in agent.servers),
        instructions=instructions_for(agent),
    )


def profiles(model: ModelConnection, host: str = "host.docker.internal") -> list[BotProfile]:
    return [bot_profile(a, model, host) for a in AGENTS.values()]


def lab_tool_names() -> list[str]:
    """Rakazo names of every lab tool, ``mcp__lab-<server>__<tool>``, read from the server modules."""
    names = []
    for key, spec in SERVERS.items():
        server = importlib.import_module(spec["module"]).server
        names += [f"mcp__lab-{key}__{t.name}" for t in asyncio.run(server.list_tools())]
    return names


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="log in and create the four bots with their MCP assignments")
    parser.add_argument("--update", action="store_true",
                        help="push the current instructions and model to the existing bots instead of creating them")
    parser.add_argument("--auto-approve", action="store_true",
                        help="Phase 1: let every lab tool run without Rakazo's per-call human review")
    parser.add_argument("--send", metavar="TEXT", help="also send TEXT to a bot (requires --apply or --update)")
    parser.add_argument("--to", default="Gatekeeper", choices=sorted(AGENTS), help="which bot receives --send")
    args = parser.parse_args()
    if args.apply and args.update:
        parser.error("use --apply (create) or --update (existing bots), not both")
    if args.send and not (args.apply or args.update):
        parser.error("--send requires --apply or --update")

    host = os.environ.get("MCP_HOST", "host.docker.internal")
    if not (args.apply or args.update or args.auto_approve):
        print("Preview only. No network calls or changes.\n")
        for bot in profiles(ModelConnection("openai-compatible", "YOUR_MODEL", "YOUR_BASE_URL"), host):
            print(f"### {bot.name}\nTools: {', '.join(m.name + ' @ ' + m.endpoint for m in bot.mcp)}\n")
            print(bot.instructions + "\n")
        return

    provider = os.environ.get("MODEL_PROVIDER", "openai-compatible")
    required = ["RAKAZO_URL", "RAKAZO_EMAIL", "MODEL_ID"]
    if provider == "openai-compatible":
        required.append("MODEL_BASE_URL")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        parser.error("Set " + ", ".join(missing))
    base_url = os.environ.get("MODEL_BASE_URL", "") if provider == "openai-compatible" else ""
    model = ModelConnection(provider, os.environ["MODEL_ID"], base_url, os.environ.get("MODEL_API_KEY", ""))
    password = os.environ.get("RAKAZO_PASSWORD") or getpass.getpass("Rakazo password: ")
    with Rakazo(os.environ["RAKAZO_URL"], origin=os.environ.get("RAKAZO_ORIGIN")) as app:
        app.login(os.environ["RAKAZO_EMAIL"], password)
        if args.apply:
            bots = app.create_team(*profiles(model, host))
            print("Created " + ", ".join(bots) + " with their MCP assignments.")
        else:
            existing = {b["name"]: b["id"] for b in app.list_bots()}
            missing_bots = [a for a in AGENTS if a not in existing]
            if missing_bots:
                raise SystemExit(f"Not in this Rakazo workspace: {', '.join(missing_bots)}. Run --apply first.")
            bots = {a: existing[a] for a in AGENTS}
            if args.update:
                for bot in profiles(model, host):
                    app.update_bot(bots[bot.name], bot)
                print("Updated instructions and model of " + ", ".join(bots) + ".")
        if args.auto_approve:
            tools = lab_tool_names()
            for tool in tools:
                app.always_allow_tool(tool)
            print(f"Always allow: {len(tools)} lab tools (no per-call review).")
        if args.send:
            run = app.send(bots[args.to], args.send)
            print(f"Started run {run.get('runId', run)}. Open {args.to} in Rakazo to watch the conversation.")


if __name__ == "__main__":
    main()
