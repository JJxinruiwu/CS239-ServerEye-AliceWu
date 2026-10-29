"""Persistent JSON state for the simulated lab server.

All four MCP servers and the admin scripts share one state file, so every
mutation goes through `locked_state()`, which holds an exclusive file lock for
the whole read-modify-write. The file lives at ``lab/state.json`` by default;
set ``LABOPS_STATE`` to point somewhere else (the tests do).
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

try:  # POSIX only; the course runner is Linux/macOS (WSL on Windows).
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE = REPO_ROOT / "lab" / "state.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def state_path() -> Path:
    return Path(os.environ.get("LABOPS_STATE", DEFAULT_STATE))


def load_state() -> dict:
    """Read the state file, seeding it on first use."""
    path = state_path()
    if not path.exists():
        from .seed import make_seed

        save_state(make_seed())
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(state, indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(data, encoding="utf-8")
    try:
        os.replace(tmp, path)
    except OSError:  # some mounted filesystems refuse rename-over; write in place
        path.write_text(data, encoding="utf-8")
        try:
            tmp.unlink()
        except OSError:
            pass


@contextmanager
def locked_state():
    """Yield the state dict under an exclusive lock and persist it afterwards."""
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    with open(lock_path, "w", encoding="utf-8") as lock:
        locked = False
        if fcntl is not None:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
                locked = True
            except OSError:
                locked = False
        try:
            state = load_state()
            yield state
            save_state(state)
        finally:
            if locked:
                fcntl.flock(lock, fcntl.LOCK_UN)


def reset_state(**overrides) -> dict:
    """Rewrite the state file from the seed, applying top-level overrides."""
    from .seed import make_seed

    state = make_seed()
    state.update(overrides)
    save_state(state)
    return state
