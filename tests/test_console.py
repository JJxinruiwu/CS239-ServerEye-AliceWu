"""Tests for the ServerEye console (labops.console): the three pages and their JSON APIs."""
import pytest
from starlette.testclient import TestClient

from labops.console.app import make_app
from labops.sim.state import load_state, reset_state


@pytest.fixture
def client():
    reset_state()
    return TestClient(make_app())


def test_three_pages_are_served(client):
    for path in ("/portal", "/monitor", "/agents"):
        r = client.get(path)
        assert r.status_code == 200 and "ServerEye" in r.text, path


def test_portal_shows_only_the_students_own_requests_and_inbox(client):
    assert client.post("/api/portal/requests", json={"student": "cara", "software": "dc", "text": "for EE201C"}).status_code == 200
    assert client.post("/api/portal/requests", json={"student": "ben", "software": "xcelium", "text": "for EE216A"}).status_code == 200
    ben = client.get("/api/portal?student=ben").json()
    assert [r["software"] for r in ben["requests"]] == ["xcelium"]
    assert "cara" not in str(ben["requests"]) and "email" not in ben["requests"][0]
    status = {c["product"]: c["status"] for c in ben["catalog"]}
    assert status["vcs"] == "installed" and status["xcelium"] == "available" and status["innovus"] == "needs_license"


def test_portal_rejects_unknown_students_and_empty_requests(client):
    assert client.get("/api/portal?student=mallory").status_code == 404
    assert client.post("/api/portal/requests", json={"student": "ben", "software": "", "text": ""}).status_code == 400


def test_monitor_view_and_admin_actions(client):
    d = client.get("/api/monitor").json()
    assert d["verdict"]["status"] == "ok" and {x["name"] for x in d["daemons"]} >= {"lmgrd", "snpslmd"}
    vcs = next(f for f in d["features"] if f["feature"] == "VCSRuntime_Net")
    assert vcs["in_use"] == 2 and vcs["days_left"] == 12
    assert client.post("/api/admin", json={"action": "stop_daemon", "name": "cdslmd"}).status_code == 200
    assert [a["code"] for a in client.get("/api/monitor").json()["verdict"]["anomalies"]] == ["R1"]
    assert client.post("/api/admin", json={"action": "start_daemon", "name": "cdslmd"}).status_code == 200
    client.post("/api/portal/requests", json={"student": "dev", "software": "innovus", "text": "x"})
    with __import__("labops.sim.state", fromlist=["locked_state"]).locked_state() as st:
        st["requests"][0]["status"] = "awaiting_admin"
    assert client.post("/api/admin", json={"action": "approve", "request_id": "REQ-101"}).status_code == 200
    assert load_state()["requests"][0]["status"] == "approved"


def test_agents_replay_and_scenario_list(client):
    scenarios = client.get("/api/agents/scenarios").json()["scenarios"]
    assert len(scenarios) == 5
    recorded = [s["name"] for s in scenarios if s["recorded"]]
    if recorded:
        d = client.get(f"/api/agents/events?source={recorded[0]}").json()
        assert d["events"] and d["lanes"][0] == "admin"
    assert client.get("/api/agents/events?source=nope").status_code == 404


def test_agents_send_validates_before_reaching_rakazo(client):
    assert client.post("/api/agents/send", json={"agent": "Nobody", "text": "hi"}).status_code == 400
    assert client.post("/api/agents/send", json={"agent": "Gatekeeper", "text": "  "}).status_code == 400


def test_agents_reset_lab_restores_the_seed(client):
    client.post("/api/portal/requests", json={"student": "ben", "software": "dc", "text": "x"})
    assert load_state()["requests"]
    r = client.post("/api/agents/reset", json={"lab": True})
    assert r.status_code == 200 and load_state()["requests"] == []
