"""Agent definitions and runtimes.

- profiles.py     the four agents: role, Phase 1 policy-as-instructions, tool servers, who they may message
- rakazo.py       the course's thin wrapper around Rakazo's HTTP API (attributed copy)
- rakazo_app.py   create the team in Rakazo / send a request (mirrors the tutorial's movie-night app)
- local_runner.py a dependency-light runtime: OpenAI-compatible tool loop + message_bot, full traces
"""
