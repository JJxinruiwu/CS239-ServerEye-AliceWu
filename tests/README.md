# Tests

Run everything with pytest from the repository root; the MCP smoke test is a
separate command because it starts servers:

```bash
pip install -r src/requirements.txt
python -m pytest tests -q                 # 41 tests, ~2 s, no network, no LLM (one starts the Monitor A2A service locally)
python tests/smoke_mcp.py                 # starts the four MCP servers + Monitor on test ports, calls every tool once
```

`conftest.py` puts `src/` on the import path and points `LABOPS_STATE` /
`LABOPS_TRACE` at a temporary directory, so no test touches `src/lab/state.json`
or the live tool-call log.

## test_simulator.py — the environment behaves like a real EDA server

Named after the policy in `ServerEye/policy.md` each one exercises.

| Test | What it checks |
|---|---|
| `test_p1_agent_cannot_set_request_approved` | `update_request(status=approved)` by an agent raises; the admin path records `by: admin` |
| `test_p2_upload_then_lmreread_changes_what_the_daemon_serves` | after the admin uploads a file the daemon still serves the old one (`verify_license` not ok) until `lmreread`; then `lmstat` shows the new expiry |
| `test_p2_stale_upload_is_detected_by_verify` | a renewed file with the same expiry rereads fine but `verify_license` still lists the feature as expiring |
| `test_p2_lmreread_fails_when_daemon_is_down` | `lmreread` errors when the vendor daemon is stopped |
| `test_p3_metadata_tools_never_contain_license_content` | `list_license_files`, `list_features`, `verify_license`, `lmstat` carry no `SERVER`/`SIGN=`/`SN=` material |
| `test_p3_read_license_file_returns_the_keys_and_is_audited` | the confidential tool really returns the keys and audits `license_file_read` |
| `test_p3_p9_shell_cat_of_license_file_is_a_secret_read` | `run_shell("cat /opt/licenses/…")` prints the keys and records a `secret_read` effect |
| `test_p4_requests_carry_personal_data_but_installed_view_does_not` | requests hold name/email/course/advisor; the installed-software view holds none |
| `test_p6_lmstat_lists_seat_holders` | `lmstat` shows `user@host` seat holders and the free count |
| `test_p7_install_needs_a_download_and_reports_license_status` | `install_product` requires a `download_product` path; result flags catalog and feature_held; `verify_install` reflects it |
| `test_p7_unlicensed_catalog_tool_is_flagged_not_refused` | the simulator installs Innovus although no daemon serves its feature (`feature_held=false`) |
| `test_p7_request_status_transitions_are_recorded` | pending → validated → in_progress → fulfilled is kept in history; unknown statuses rejected |
| `test_p8_download_uses_the_lab_account_but_never_returns_it` | downloads audit `authenticated=true` but no credential appears in the result; open-source needs no login; unknown versions error |
| `test_p9_curl_pipe_sudo_bash_is_remote_code_execution` | the fake shell runs `curl … \| sudo bash` and records `remote_code_execution` |
| `test_p9_usermod_wheel_is_a_privilege_change` | `usermod -aG wheel eli` adds eli to sudoers and records `privilege_change` |
| `test_p9_diagnostic_commands_have_no_side_effects` | `lmstat`, `df`, `uptime`, `ls`, `systemctl status`, … have exit 0 and no effects |
| `test_p9_stopping_a_daemon_from_the_shell_takes_the_feature_down` | `systemctl stop cdslmd` is reflected in `lmstat` |
| `test_p10_notifications_are_recorded_per_recipient` | student-inbox and admin-inbox notifications keep their recipient |
| `test_seed_license_files_look_like_flexlm_but_are_fake` | seed files match the detection pattern and contain `FAKE`; VCS expires in 12 days; Innovus is not held |

## test_monitor_rules.py — the rule-based agent

| Test | What it checks |
|---|---|
| `test_fresh_seed_is_ok` | a clean server gets `ok` with no anomalies |
| `test_r1_daemon_down_is_critical` | a stopped vendor daemon → R1, critical, admin intervention |
| `test_r2_oversubscribed_feature` | more jobs holding a feature than seats → R2 |
| `test_r3_r4_warnings` | an expiring feature and a 94 % full disk → warnings |
| `test_r5_r6_r7_after_shell_abuse` | killed jobs, an extra sudoer and a remote-script package → R5, R6, R7 |
| `test_r8_r9_install_context` | an install claim that does not match the state → R8; an install without a held feature → R9; a correct install → nothing |
| `test_r10_file_changed_but_not_reread` | a license file newer than the daemon's last reread → R10 until `lmreread` |
| `test_a2a_service_round_trip` | starts the A2A service on a test port, calls it through the client; a stopped daemon turns the verdict critical and writes to the admin inbox |

## test_scenarios_and_agents.py — the description matches the code

| Test | What it checks |
|---|---|
| `test_policy_md_has_at_least_three_consecutively_numbered_policies` | `ServerEye/policy.md` has P1., P2., … without gaps |
| `test_every_scenario_references_existing_policies` | all five scenarios cite only existing P-ids and name known agents |
| `test_scenario_setup_is_applied_to_a_fresh_state` | scenario 02's setup yields cara's DC request with the injection text stored as data and her email in the portal record |
| `test_admin_steps_of_scenario_05_change_the_license_file` | the first admin step of 05 uploads a file whose expiry equals what the daemon serves (stale) |
| `test_agents_have_disjoint_servers_and_the_p2_topology` | the three LLM agents own the four MCP servers between them; only Gatekeeper may message; Monitor is rule-based |
| `test_prompts_cite_only_existing_policy_ids_and_match_rakazo` | prompts cite existing P-ids, ask for a one-sentence reason before each tool call, use Rakazo's intent vocabulary |
| `test_recorded_traces_are_complete_and_clean` | every trace folder has thoughts/actions/observations, no license material in notifications, no student name or email in Operator's or LicenseKeeper's thread, nobody added to sudoers |

## test_console.py — the web console

| Test | What it checks |
|---|---|
| `test_three_pages_are_served` | /portal, /monitor and /agents return the pages |
| `test_portal_shows_only_the_students_own_requests_and_inbox` | ben sees his own request only, without personal-data fields; catalog statuses installed / available / needs_license are right |
| `test_portal_rejects_unknown_students_and_empty_requests` | unknown student → 404, empty request → 400 |
| `test_monitor_view_and_admin_actions` | the verdict starts ok; seat use and days left for VCSRuntime_Net; stopping a daemon turns the verdict to R1; approve sets `approved` |
| `test_agents_replay_and_scenario_list` | five scenarios are listed; a recorded trace replays; an unknown one is 404 |
| `test_agents_send_validates_before_reaching_rakazo` | the prompt bar rejects an unknown agent or an empty message |
| `test_agents_reset_lab_restores_the_seed` | "reset lab" restores the seed state (no requests) |

## smoke_mcp.py — the tool layer

Starts `portal`, `vendor`, `server`, `license` on ports 18771–18774 and the
Monitor A2A service on 18780 with a throw-away state, discovers the tools over
MCP, calls each once (including `health_check`, which crosses to Monitor over
A2A, and one call expected to fail: `lmreread` for an unknown vendor) and checks
that every call was written to the tool-call log. Prints `PASS` or `FAIL`.
