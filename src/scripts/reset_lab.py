#!/usr/bin/env python3
"""Reset the simulated lab server to its seed state (and optionally apply a scenario's setup).

    python scripts/reset_lab.py                      # fresh server, empty queue
    python scripts/reset_lab.py --maintenance on     # fresh server inside a maintenance window
    python scripts/reset_lab.py --scenario scenarios/01_compliant_install.json

With --scenario the file's "setup" block is applied: maintenance window,
pre-submitted requests, admin approvals, and extra state overrides. The local
runner does the same before every run, so traces are reproducible.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from labops.sim.scenario import apply_setup, load_scenario  # noqa: E402
from labops.sim.state import reset_state, state_path  # noqa: E402
from labops.tracing import trace_path  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--maintenance", choices=["on", "off"])
    parser.add_argument("--scenario", help="scenario JSON whose setup block to apply")
    parser.add_argument("--keep-trace", action="store_true", help="do not truncate traces/raw/tool_calls.jsonl")
    args = parser.parse_args()

    reset_state()
    print(f"state reset -> {state_path()}")
    if not args.keep_trace:
        tp = trace_path()
        tp.parent.mkdir(parents=True, exist_ok=True)
        tp.write_text("", encoding="utf-8")
        print(f"trace truncated -> {tp}")
    if args.maintenance:
        for line in apply_setup({"maintenance_window": args.maintenance == "on"}):
            print(line)
    if args.scenario:
        for line in apply_setup(load_scenario(args.scenario)["setup"]):
            print(line)


if __name__ == "__main__":
    main()
