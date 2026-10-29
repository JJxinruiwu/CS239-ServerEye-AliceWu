"""Start the four MCP servers (streamable HTTP) and the Monitor A2A service.

    python -m labops.mcp_servers.serve            # MCP on 0.0.0.0:8771-8774, Monitor A2A on 127.0.0.1:8780
    python -m labops.mcp_servers.serve --host 127.0.0.1
    python -m labops.mcp_servers.serve --only portal license

Each server is its own process. Ctrl-C stops them all.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time

from ..monitor import A2A_PORT
from . import SERVERS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=os.environ.get("LABOPS_MCP_HOST", "0.0.0.0"))
    parser.add_argument("--only", nargs="*", choices=sorted(SERVERS) + ["monitor"], help="start a subset")
    args = parser.parse_args()

    keys = args.only or list(SERVERS) + ["monitor"]
    procs: dict[str, subprocess.Popen] = {}
    for key in keys:
        if key == "monitor":
            cmd = [sys.executable, "-m", "labops.monitor.a2a_service", "--host", "127.0.0.1", "--port", str(A2A_PORT)]
            procs[key] = subprocess.Popen(cmd)
            print(f"[monitor ] http://127.0.0.1:{A2A_PORT}/   (Monitor A2A service, rule-based)", flush=True)
            continue
        spec = SERVERS[key]
        port = int(os.environ.get(f"LABOPS_{key.upper()}_PORT", spec["port"]))
        cmd = [sys.executable, "-m", spec["module"], "--http", "--host", args.host, "--port", str(port)]
        procs[key] = subprocess.Popen(cmd)
        print(f"[{key:<8}] http://{args.host}:{port}/mcp   ({spec['title']})", flush=True)
    print("Press Ctrl-C to stop.", flush=True)

    def shutdown(*_):
        for p in procs.values():
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
        for p in procs.values():
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    while True:
        for key, p in procs.items():
            if p.poll() is not None:
                print(f"[{key}] exited with code {p.returncode}", file=sys.stderr)
                shutdown()
        time.sleep(1)


if __name__ == "__main__":
    main()
