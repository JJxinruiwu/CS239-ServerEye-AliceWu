# ServerEye

Three LLM agents and one rule-based agent administer a simulated EDA lab server:
Gatekeeper (student requests), Operator (vendor downloads and installs),
LicenseKeeper (license files and daemons) and Monitor (health checks, rule-based,
reached over A2A). What the system does and must never do is in `ServerEye/`
(`app.md`, `policy.md`, `tools.md`, `agents/`, `episodes/`); `src/` is the
implementation.

```
 students ──▶ request portal ◀── Gatekeeper ──request──▶ Operator ──▶ vendor sites (lab accounts inside the MCP)
   (untrusted)   (portal MCP)        │ question              │     └──▶ eda-lab-01: lmstat, install, verify, shell
                                     ▼                       │ health_check (MCP tool = A2A client)
 admin ──▶ admin.py ──────▶ LicenseKeeper ──▶ admin inbox    ▼
   (root, approvals,          (license MCP)          Monitor (rule-based A2A service) ──▶ admin inbox
    license files)                                        ▲ scripts/monitor_routine.py (cron)
```

| Agent | Kind | MCP server (port) | Tools |
|---|---|---|---|
| Gatekeeper | LLM | `portal` (8771) | list_requests, get_request, get_software_catalog, list_installed_software, update_request, notify_student |
| Operator | LLM | `vendor` (8772), `server` (8773) | list_vendor_products, download_product; lmstat, install_product, verify_install, create_snapshot, health_check, run_shell |
| LicenseKeeper | LLM | `license` (8774) | list_license_files, list_features, read_license_file, lmreread, verify_license, notify_admin |
| Monitor | rule-based | A2A service (8780), no MCP | reads the server state; writes to the admin inbox |

The simulator is deliberately not safe: it downloads and installs anything,
prints license files on `cat`, lists seat holders, and runs `curl | sudo bash`.
Keeping it safe is the agents' job.

## Layout

```
src/labops/sim/           simulated server: state.py, seed.py (vendors, catalog, licenses, students), server.py, scenario.py
src/labops/mcp_servers/   portal.py, vendor.py, server.py, license.py, serve.py (launcher: 4 MCP servers + Monitor)
src/labops/monitor/       rules.py (Monitor's decision logic), a2a_service.py (A2A server), client.py (A2A client)
src/labops/agents/        profiles.py (roles + policies as prompts), rakazo_app.py, rakazo.py
src/labops/tracing.py     JSONL tool-call logger used by all servers and the Monitor service
src/labops/console/       web console: /portal (students), /monitor (admin), /agents (multi-agent timeline)
src/scenarios/            five reproducible scenarios (state, request queue, kickoff and admin steps)
src/scripts/              run_rakazo_scenario.py, export_rakazo_traces.py, render_description.py,
                          monitor_routine.py, reset_lab.py, submit_request.py, admin.py
src/traces/               recorded runs (one folder per scenario)
src/lab/                  runtime state of the simulator (state.json, git-ignored)
tests/                    pytest suite and MCP smoke test (see tests/README.md)
```

## Setup

Python 3.10 or newer, macOS or Linux (the servers use POSIX file locks).

```bash
cd src
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest ../tests -q          # unit tests (one starts the Monitor service locally)
python ../tests/smoke_mcp.py          # starts everything on test ports and calls every tool
```

All commands below run from `src/`.

No API keys are stored anywhere in the repository. Model and Rakazo credentials
come from environment variables or from a git-ignored `.env` file in `src/` or
the repository root (plain `KEY=VALUE` lines, read literally).

## Running on Rakazo (what the recorded episodes used)

Rakazo ("Grokbot" in the course) hosts the three LLM bots, gives each its MCP
server(s), and carries Gatekeeper's messages to Operator and LicenseKeeper. The
Monitor service runs on the host next to the MCP servers; Operator reaches it
through the `health_check` tool, which is an A2A client call that waits for the
verdict.

1. **Rakazo.** Install with the course tutorial's `setup-rakazo.sh` (Docker). Two
   things the tutorial does not mention:
   - Current Rakazo images refuse plain-HTTP MCP endpoints (`Connector URL must use
     HTTPS`). Set `MCP_ALLOW_PRIVATE_ENDPOINT=true` in `.rakazo-runtime/.env` and
     recreate the `api` and `worker` containers.
   - The tutorial's Compose patch may fail with `Unexpected upstream Compose layout`
     because `api:` also appears under `depends_on`; patch `extra_hosts:
     ["host.docker.internal:host-gateway"]` into the `api` and `worker` services by hand.
2. **Account and model.** Open http://localhost:5173, create an account, and connect
   a model. The recorded runs used a ChatGPT subscription (`openai-codex` provider,
   model `gpt-6-luna`); any provider with reliable tool calling works.
3. **Credentials.** Create `.env` (git-ignored):
   ```
   RAKAZO_URL=http://127.0.0.1:5173
   RAKAZO_EMAIL=you@example.edu
   RAKAZO_PASSWORD=...
   MODEL_PROVIDER=openai-codex        # or openai-compatible with MODEL_BASE_URL and MODEL_API_KEY
   MODEL_ID=gpt-6-luna
   ```
4. **Tool servers and Monitor** (terminal 1, keep running):
   ```bash
   python -m labops.mcp_servers.serve        # MCP on 0.0.0.0:8771-8774 (Docker reaches them), Monitor on 127.0.0.1:8780
   ```
5. **Create the team** (once per workspace) and let its tools run without Rakazo's
   per-call human review (otherwise every state-changing call stops for a click):
   ```bash
   set -a; source .env; set +a
   python -m labops.agents.rakazo_app --apply --auto-approve
   python -m labops.agents.rakazo_app --update        # later: push edited prompts to the bots
   ```
6. **Run a scenario and record its trace** (one command: resets the lab, clears the
   bots' threads, sends each kickoff, applies the admin steps, prints every tool
   call, exports the trace):
   ```bash
   python scripts/run_rakazo_scenario.py scenarios/01_compliant_install.json
   python scripts/run_rakazo_scenario.py scenarios/05_license_renewal.json
   ```
   Output: `src/traces/<scenario>/{trace.md, trace.json, tool_calls.jsonl, final_state.json,
   scenario.json, runtime.json, rakazo_thread_<Bot>.json}`. Watch the run live in the
   Rakazo UI; inspect the human side with `python scripts/admin.py list` / `inbox`.
7. **Scheduled Monitor check** (what a cron job would run): `python scripts/monitor_routine.py`.

Rakazo keeps the tool-call log and the simulator state on the host, so the
evidence layer (`tool_calls.jsonl`, `final_state.json`) is independent of the
framework.

## The web console

```bash
python -m labops.console.app              # http://127.0.0.1:8790/portal  /monitor  /agents
```

Three independent pages (no login; the portal has a "viewing as" student picker for the demo):

- **/portal** — the student view: installed tools and the catalog (installed / available /
  no lab license), a request form, and the student's own requests (with progress) and inbox.
  A student never sees another student's requests or messages.
- **/monitor** — the admin view: Monitor's live verdict and anomalies, lmgrd and vendor
  daemons (with stop/start as admin actions), every served feature with seat usage, holders
  and days to expiry, disk, jobs, sudoers, requests in flight (approve), the admin inbox,
  and a "Run health check" button that calls the Monitor A2A service.
- **/agents** — the multi-agent timeline: one lane per agent (Admin, Gatekeeper, Operator,
  LicenseKeeper, Monitor), every thought, tool call (expand for arguments and result),
  inter-agent message and A2A health check in time order, nothing folded. "live" merges the
  Rakazo threads with the tool-call log as the agents work; "replay" shows a recorded trace.
  "Run on Rakazo" starts a scenario (scripts/run_rakazo_scenario.py, resets the lab) and follows
  it live. The command bar at the bottom sends any message, as the admin, to Gatekeeper,
  Operator or LicenseKeeper on top of the current lab state (no reset): e.g. submit a request
  on /portal, then "process portal queue". "reset lab" and "clear bot threads" start over.

The console reads `src/lab/state.json`, the tool-call log and `src/traces/`; for the live view it
signs in to Rakazo once with the credentials from `.env`. It is not an agent and no agent
can reach it.

## The admin's side

`scripts/admin.py` is the human admin's console; nothing in it is an agent tool:

```bash
python scripts/admin.py list                      # requests, served licenses, daemons, installs, jobs
python scripts/admin.py inbox                     # what Monitor and LicenseKeeper reported, what students got
python scripts/admin.py approve-request REQ-101
python scripts/admin.py upload-license synopsys --feature VCSRuntime_Net:2024.09:365:5 --feature Design-Compiler:2023.12:365:2
python scripts/admin.py stop-daemon cdslmd        # simulate an incident for Monitor to find
```

## Regenerating the description

`ServerEye/agents/*.md`, `tools.md` and `episodes/*.md` are generated from the
code and the recorded traces so they cannot drift from the implementation:

```bash
python scripts/render_description.py
```

## External requirements

- An LLM endpoint with tool calling (hosted API, a subscription through Rakazo,
  or a local server such as Ollama); billed to the key or subscription owner.
- Docker for Rakazo.
- Nothing else: the EDA server, the license daemons, the request portal, the
  vendor download sites and the admin console are all simulated in `src/labops/sim`.
