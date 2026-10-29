"""Monitor: the rule-based agent, published as an A2A service.

- rules.py        the decision logic (pure function over the simulated state)
- a2a_service.py  the A2A server (agent card + JSON-RPC) wrapping ``rules.check``
- client.py       ``health_check()``: the A2A client used by the ``server`` MCP tool and the cron script
"""

A2A_PORT = 8780
