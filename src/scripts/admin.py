#!/usr/bin/env python3
"""The human lab admin's console. These actions are deliberately NOT agent tools:
agents can ask for them, only a person can perform them.

    python scripts/admin.py list                              # requests, licenses, daemons, jobs
    python scripts/admin.py inbox                             # messages to the admin and to students
    python scripts/admin.py approve-request REQ-102
    python scripts/admin.py upload-license synopsys --feature VCSRuntime_Net:2024.09:365:5 [--feature ...]
    python scripts/admin.py stop-daemon cdslmd                # simulate a daemon dying
    python scripts/admin.py maintenance on|off
    python scripts/admin.py reboot
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from labops.sim.scenario import _features  # noqa: E402
from labops.sim.server import LabServer  # noqa: E402
from labops.sim.state import load_state, locked_state  # noqa: E402


def parse_feature(spec: str) -> dict:
    """feature:version:expires_in_days:count"""
    feature, version, days, count = spec.split(":")
    return {"feature": feature, "version": version, "expires_in_days": int(days), "count": int(count)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("inbox")
    p = sub.add_parser("approve-request"); p.add_argument("request_id")
    p = sub.add_parser("upload-license"); p.add_argument("vendor"); p.add_argument("--feature", action="append", required=True)
    p = sub.add_parser("stop-daemon"); p.add_argument("name")
    p = sub.add_parser("maintenance"); p.add_argument("state", choices=["on", "off"])
    sub.add_parser("reboot")
    args = parser.parse_args()

    if args.cmd == "list":
        st = load_state()
        print("requests:")
        for r in st["requests"]:
            print(f"  {r['id']}  {r['status']:<15} {r['student']:<6} {r['software']} {r['version']}")
        print("licenses (what the daemons serve):")
        for vendor, feats in st["license_server"]["loaded"].items():
            for f in feats:
                print(f"  {vendor:<9} {f['feature']:<24} v{f['version']:<8} exp {f['expires']}  seats {f['count']}")
        print(f"license daemons: lmgrd={st['license_server']['lmgrd']} " +
              " ".join(f"{d}={s}" for d, s in st["license_server"]["daemons"].items()))
        print(f"installed: " + ", ".join(f"{p} {i['version']}" for p, i in st["installed"].items()))
        print(f"active jobs: {len(st['jobs'])}   sudoers: {', '.join(st['sudoers'])}   maintenance: {st['maintenance_window']['active']}")
        return
    if args.cmd == "inbox":
        for n in load_state()["notifications"]:
            print(f"--- #{n['id']} to {n['to']} via {n['channel']} from {n['from']} at {n['at']}\n{n['message']}\n")
        return
    with locked_state() as st:
        srv = LabServer(st, actor="admin")
        if args.cmd == "approve-request":
            print(json.dumps(srv.approve_request(args.request_id), indent=2))
        elif args.cmd == "upload-license":
            print(json.dumps(srv.upload_license_file(args.vendor, _features([parse_feature(f) for f in args.feature])), indent=2))
        elif args.cmd == "stop-daemon":
            print(json.dumps(srv.stop_daemon(args.name), indent=2))
        elif args.cmd == "maintenance":
            print(json.dumps(srv.set_maintenance_window(args.state == "on"), indent=2))
        elif args.cmd == "reboot":
            print(json.dumps(srv.reboot(), indent=2))


if __name__ == "__main__":
    main()
