#!/usr/bin/env python3
"""The scheduled Monitor run: ask the Monitor A2A service for a health check and print the verdict.

    python scripts/monitor_routine.py                 # one scheduled check
    */15 * * * * cd /path/to/src && python scripts/monitor_routine.py   # as a cron line

The service reads the server state itself and notifies the admin when the
verdict is not ok; this script only triggers it (the same call Operator's
``health_check`` tool makes after an install).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from labops.monitor.client import health_check  # noqa: E402


def main() -> None:
    verdict = health_check({"event": "scheduled"})
    print(json.dumps(verdict, indent=2))
    raise SystemExit(0 if verdict.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
