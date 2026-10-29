"""Smoke test: start the four MCP servers and the Monitor A2A service, discover the tools, call each once.

    python -m tests.smoke_mcp

Uses a throw-away state file and trace file so it never touches lab/state.json.
No LLM is involved; this exercises the tool layer exactly the way an agent
runtime (Rakazo or the local runner) will.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(ROOT))

from labops.mcp_servers import SERVERS  # noqa: E402

BASE_PORT = 18771

CALLS = {
    "portal": [
        ("get_software_catalog", {}),
        ("list_installed_software", {}),
        ("list_requests", {"status": "pending"}),
        ("get_request", {"request_id": "REQ-101"}),
        ("update_request", {"request_id": "REQ-101", "status": "validated", "note": "catalog ok"}),
        ("notify_student", {"student": "ben", "message": "Your request REQ-101 was validated."}),
    ],
    "vendor": [
        ("list_vendor_products", {"vendor": "synopsys"}),
        ("download_product", {"vendor": "synopsys", "product": "vcs", "version": "W-2024.09"}),
    ],
    "server": [
        ("lmstat", {"feature": "VCSRuntime_Net"}),
        ("create_snapshot", {"label": "smoke"}),
        ("install_product", {"path": "/scratch/downloads/synopsys-vcs-W-2024.09.run", "request_id": "REQ-101"}),
        ("verify_install", {"product": "vcs"}),
        ("health_check", {"event": "install", "product": "vcs", "version": "W-2024.09"}),
        ("run_shell", {"command": "lmstat"}),
    ],
    "license": [
        ("list_license_files", {}),
        ("list_features", {"vendor": "synopsys"}),
        ("read_license_file", {"vendor": "synopsys"}),
        ("verify_license", {"vendor": "synopsys"}),
        ("lmreread", {"vendor": "nosuchvendor"}),  # expected to fail: unknown vendor
        ("notify_admin", {"message": "VCSRuntime_Net expires in 12 days."}),
    ],
}


async def exercise(key: str, port: int) -> int:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    url = f"http://127.0.0.1:{port}/mcp"
    failures = 0
    async with streamable_http_client(url) as (read, write, *_):
        async with ClientSession(read, write) as client:
            await client.initialize()
            tools = {t.name: t for t in (await client.list_tools()).tools}
            print(f"\n== {key} ({url}) tools: {sorted(tools)}")
            for name, args in CALLS[key]:
                assert name in tools, f"{key}: tool {name} missing"
                result = await client.call_tool(name, args)
                if result.is_error:
                    text = result.content[0].text if result.content else ""
                    expected = name == "lmreread"
                    print(f"  {'ok (expected error)' if expected else 'ERROR'} {name}{args}: {text[:120]}")
                    failures += 0 if expected else 1
                else:
                    data = result.structured_content or json.loads(result.content[0].text)
                    print(f"  ok {name}{args} -> {json.dumps(data, default=str)[:140]}")
    return failures


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="labops-smoke-"))
    env = {**os.environ, "LABOPS_STATE": str(tmp / "state.json"), "LABOPS_TRACE": str(tmp / "tool_calls.jsonl")}

    # seed one request so the portal calls have something to read
    subprocess.run([sys.executable, "scripts/submit_request.py", "--student", "ben", "--software", "vcs",
                    "--text", "Please install VCS for my project."], cwd=ROOT, env=env, check=True)
    # the Monitor A2A service, so server.health_check has something to call
    env["LABOPS_MONITOR_URL"] = f"http://127.0.0.1:{BASE_PORT + 9}"

    procs = [subprocess.Popen([sys.executable, "-m", "labops.monitor.a2a_service", "--port", str(BASE_PORT + 9)],
                              cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
    for i, key in enumerate(SERVERS):
        port = BASE_PORT + i
        procs.append(subprocess.Popen([sys.executable, "-m", SERVERS[key]["module"], "--http", "--host", "127.0.0.1",
                                       "--port", str(port)], cwd=ROOT, env=env,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    time.sleep(2.5)
    failures = 0
    try:
        for i, key in enumerate(SERVERS):
            failures += asyncio.run(exercise(key, BASE_PORT + i))
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait(timeout=5)
    lines = (tmp / "tool_calls.jsonl").read_text().splitlines()
    print(f"\n{len(lines)} tool calls traced in {tmp / 'tool_calls.jsonl'}")
    print("PASS" if failures == 0 else f"FAIL ({failures} unexpected errors)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
