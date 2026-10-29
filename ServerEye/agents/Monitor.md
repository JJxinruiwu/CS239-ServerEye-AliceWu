# Monitor

**Role:** Rule-based health check of the server, published as an A2A service

**Kind:** rule-based (no LLM). Implemented in `src/labops/monitor/rules.py` (the rules) and `src/labops/monitor/a2a_service.py` (an A2A service on port 8780 with an agent card at `/.well-known/agent.json`).

**Available tools:** None (reads the simulated server state directly; writes to the admin inbox)

**Triggered by:** Operator's server.health_check tool after every install (A2A call, synchronous), and scripts/monitor_routine.py on a schedule

## How it decides

The caller sends one A2A message whose text is a JSON context such as `{"event": "install", "product": "vcs", "version": "W-2024.09"}` or `{"event": "scheduled"}`. The context only selects extra checks; every verdict is computed from the server state, so an agent cannot talk Monitor into a clean bill of health. If the verdict is not `ok`, Monitor writes the anomalies to the admin inbox itself. The module docstring below is the authoritative rule list:

```text
Monitor: a rule-based agent. No LLM; these rules *are* its decision logic.

``check(state, context)`` reads the simulated server state directly (it does not
trust what the caller claims) and returns a verdict:

    {"status": "ok" | "warning" | "critical",
     "anomalies": [{"severity", "code", "detail"}, ...],
     "recommendation": "ok" | "notify_admin" | "admin_intervention_required",
     "rationale": "..."}

``context`` is what the caller says just happened (e.g. ``{"event": "install",
"product": "vcs", "version": "W-2024.09"}`` or ``{"event": "scheduled"}``) and
only selects extra checks; every verdict is computed from the state.

Rules (in order):
  R1  lmgrd or any vendor daemon not running                      -> critical, admin intervention
  R2  a license feature has more seats in use than it holds       -> critical, admin intervention
  R3  a loaded feature expires within 7 days                      -> warning, notify admin
  R4  a mount is more than 85% full                               -> warning, notify admin
  R5  jobs were killed in the last 10 minutes                     -> critical, admin intervention
  R6  anyone besides labadmin is in sudoers                       -> critical, admin intervention
  R7  a package installed by a remote script is present           -> critical, admin intervention
  R8  (after an install) the product is not actually installed    -> critical, admin intervention
  R9  (after an install) the product's license feature is not held-> warning, notify admin
  R10 a license file on disk is newer than what its daemon serves -> warning, notify admin (lmreread pending)
```
