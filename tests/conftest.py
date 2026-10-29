"""pytest setup: import ``labops`` from ``src/`` and give every test its own simulator state."""
import os
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Point the simulator and the tool-call log at a temp dir so tests never touch src/lab."""
    monkeypatch.setenv("LABOPS_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("LABOPS_TRACE", str(tmp_path / "tool_calls.jsonl"))
    yield tmp_path


@pytest.fixture
def lab():
    """A LabServer on a fresh seed state, acting as the operator server."""
    from labops.sim.seed import make_seed
    from labops.sim.server import LabServer

    return LabServer(make_seed(), actor="operator-mcp")
