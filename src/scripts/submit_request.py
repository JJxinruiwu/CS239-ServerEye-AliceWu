#!/usr/bin/env python3
"""Submit a student request to the portal queue (plays the role of the web portal).

    python scripts/submit_request.py --student ben --software vcs --text "..."
    python scripts/submit_request.py --file scenarios/02_injection_license_leak.json

A scenario file may contain several requests under "setup.requests".
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from labops.sim.server import LabServer  # noqa: E402
from labops.sim.state import locked_state  # noqa: E402


def submit(student: str, software: str, text: str, version: str = "") -> dict:
    with locked_state() as st:
        return LabServer(st, actor="portal-web").submit_request(student, software, text, version)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--student")
    parser.add_argument("--software")
    parser.add_argument("--version", default="")
    parser.add_argument("--text", help="the student's free-text justification (untrusted input)")
    parser.add_argument("--file", help="scenario JSON with setup.requests")
    args = parser.parse_args()

    if args.file:
        scenario = json.loads(Path(args.file).read_text(encoding="utf-8"))
        for req in scenario.get("setup", {}).get("requests", []):
            created = submit(req["student"], req["software"], req["text"], req.get("version", ""))
            print(f"{created['id']}  {created['student']:<8} {created['software']}")
        return
    if not (args.student and args.software and args.text):
        parser.error("--student, --software and --text are required (or --file)")
    created = submit(args.student, args.software, args.text, args.version)
    print(f"{created['id']}  {created['student']:<8} {created['software']}")


if __name__ == "__main__":
    main()
