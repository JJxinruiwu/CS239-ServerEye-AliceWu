"""ServerEye console: three web views over the simulated lab.

    python -m labops.console.app            # http://127.0.0.1:8790/portal, /monitor, /agents

- /portal   the student portal: pick a student, see installed software and the catalog,
            submit a request, follow your own requests and inbox (nobody else's).
- /monitor  the admin's view: license daemons, seat usage and expiry per feature, disk,
            jobs, Monitor's verdicts and the admin inbox; admin actions (approve, start/stop
            a daemon, run a health check).
- /agents   the multi-agent timeline: one lane per agent, every thought, tool call (with
            arguments and result) and inter-agent message in time order, live from Rakazo
            and the tool-call log, or replayed from a recorded trace; start a scenario.

The console reads ``lab/state.json`` and ``traces/`` directly; it is a view and an admin
console, not an agent, and it is not reachable by any agent.
"""

CONSOLE_PORT = 8790
