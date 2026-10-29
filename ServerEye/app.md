# ServerEye

ServerEye administers a shared EDA lab server (`eda-lab-01`: Rocky Linux, a
FlexLM license server with `lmgrd` and the Synopsys/Cadence/AMD vendor daemons,
and the EDA tools themselves) on behalf of one human lab admin. Today the admin
handles every software request, every license renewal and every incident by
hand. ServerEye takes over the routine part and leaves the authority with the
person: deciding on exceptions, obtaining and placing license files, and any
disruptive change stay human actions.

The server, the student request portal and the vendor download sites are
simulated (`src/labops/sim`): a JSON state with installed tools, license files
and what the daemons serve, seat usage, jobs, services, disk, a request queue
with the students' personal data, vendor catalogs with the lab's vendor
accounts, notifications and an audit log, plus a small fake root shell. The
simulator behaves like a real machine rather than a safe one: it downloads and
installs anything, prints license files when asked, shows who holds which seat,
and runs `curl | sudo bash`. Keeping it safe is the agents' job, which is what
`policy.md` describes.

## People

- **Students** submit software requests on the portal (name, email, course,
  advisor, the tool and a free-text justification) and see on the portal which
  tools and versions are installed. They are untrusted: their text may try to
  make the agents do something else.
- **The lab admin** has root. Outside the agent system the admin approves
  exceptions, obtains license files from vendors and places them on the server,
  reboots and updates, and reads the admin inbox where Monitor and LicenseKeeper
  report.

## Agents

| Agent | Kind | Role | Tools |
|---|---|---|---|
| Gatekeeper | LLM | the only agent that reads student requests: filters out illegal requests, strips the student's personal data, checks the catalog and (via LicenseKeeper) the license, dispatches installs to Operator, tells the student the outcome | `portal` server |
| Operator | LLM | downloads from the vendor sites with the lab's accounts and installs on the server; checks `lmstat` first; asks Monitor for a health check after every install | `vendor` + `server` servers |
| LicenseKeeper | LLM | the only agent that can read license files: answers "do we hold feature X", reminds the admin about expiring features, reloads and verifies license files after the admin updates them | `license` server |
| Monitor | rule-based (A2A service) | checks license daemons, seat usage, disk, killed jobs, privilege changes and the outcome of the last install; notifies the admin when something is wrong | none: reads the server state directly |

Each LLM agent is connected to exactly its own MCP server(s), so capability
boundaries are tool assignments rather than prompt text. Gatekeeper talks to
Operator and LicenseKeeper through Rakazo's `message_bot`; Operator reaches
Monitor through the `health_check` tool, which is an A2A call that waits for
Monitor's verdict.

## Supported tasks

- **Software requests.** Gatekeeper fetches pending requests (on a schedule or
  when the admin asks), rejects anything that is not an install of catalog
  software, asks LicenseKeeper whether the lab holds the tool's license feature,
  and hands validated requests to Operator with the request id, vendor, product,
  version and feature only. Operator checks the feature with `lmstat`, downloads
  the installer from the vendor site, installs, verifies, asks Monitor for a
  health check, and reports back in one turn. Gatekeeper then marks the request
  fulfilled and tells the student.
- **License lifecycle.** LicenseKeeper runs a daily check and reminds the admin
  about features expiring within 30 days. When the admin has placed a new file,
  LicenseKeeper makes the daemon reread it and verifies that what the daemon
  serves matches the file and that the renewal actually moved the expiry.
- **Monitoring.** Monitor runs after every install and on a schedule, and writes
  to the admin inbox when a daemon is down, a feature is over-subscribed or
  expiring, disk is nearly full, jobs were killed, someone gained sudo, or an
  install does not match what was claimed.

## Scenarios and episodes

`src/scenarios/*.json` fix the server state, the request queue and the kickoff
messages for five reproducible runs: a compliant licensed install, a request
whose text tries to leak the license file and the student's personal data, a
catalog tool whose feature the lab does not hold, an install from an arbitrary
URL with a sudo request, and a license renewal where the vendor first sends a
stale file. `episodes/` contains recorded runs on Rakazo; the full logs are in
`src/traces/`.
