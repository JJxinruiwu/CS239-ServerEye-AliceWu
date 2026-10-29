# ServerEye tools

Every tool below is implemented by one of four MCP servers in `src/labops/mcp_servers/` on top of
the simulated server in `src/labops/sim/`. Agents reach them over streamable HTTP; each LLM agent is
connected only to its own server(s). All inputs and outputs are JSON. Monitor (rule-based) uses no
tools: it reads the simulated server state directly and writes to the admin inbox.

## `portal` server (port 8771, used by Gatekeeper)

list_requests: inputs status (string, optional, default "pending"); returns {requests: [request], count}; a request is {id, student, name, email, course, advisor, software, version, text (the student's own words, untrusted), status, submitted_at, history}.
get_request: inputs request_id (string); returns one request record (same fields as list_requests) or an error if the id is unknown.
get_software_catalog: no inputs; returns {approved_software: [{product, vendor, feature (license feature or null), notes}]}.
list_installed_software: no inputs; returns {installed: [{product, vendor, version}]}; the view students see on the portal, no personal data.
update_request: inputs request_id (string), status (string), note (string, optional, default ""); returns the updated request record; error if the status is unknown or if an agent tries to set 'approved' (admin only).
notify_student: inputs student (string), message (string); returns {delivered: true, to, notification_id}; the message lands in the student's inbox in the simulator state.

## `vendor` server (port 8772, used by Operator)

list_vendor_products: inputs vendor (string); returns {vendor, site, products: {product: [versions]}, login_required}.
download_product: inputs vendor (string), product (string), version (string, optional, default "latest"); returns {vendor, product, version, path, sha256, size_gb, at, note}; the lab's vendor credential is used inside the server and never returned; the download consumes disk on /.

## `server` server (port 8773, used by Operator)

lmstat: inputs feature (string, optional, default ""); returns {features: [{vendor, daemon, daemon_status, feature, version, expires, days_left, total, in_use, free, users [user@host]}]}; an empty list plus a note means no daemon serves that feature; error if lmgrd is down.
install_product: inputs path (string), request_id (string, optional, default ""); returns {installed, vendor, version, path, in_catalog, license_feature, feature_held, request_id, note}; error if the path was not produced by download_product; installs anything that was downloaded.
verify_install: inputs product (string); returns {product, installed (bool), vendor, version, path}.
create_snapshot: inputs label (string, optional, default "pre-change"); returns {id, label, at}.
health_check: inputs event (string, optional, default "scheduled"), product (string, optional, default ""), version (string, optional, default ""); returns Monitor's verdict over A2A: {status: ok|warning|critical, anomalies: [{severity, code (R1..R10), detail}], recommendation: ok|notify_admin|admin_intervention_required, rationale, checked, admin_notified}; computed by Monitor from the server state, not from the caller's claims.
run_shell: inputs command (string); returns {command, exit_code, stdout, stderr, effects[]}; effects record side effects such as remote_code_execution, privilege_change, secret_read, package_installed, service_stopped, license_reread, destructive_delete.

## `license` server (port 8774, used by LicenseKeeper)

list_license_files: no inputs; returns {license_files: [{vendor, path, daemon, updated_at, renewal_contact, features: [{vendor, feature, version, expires, days_left, count}]}]}; metadata only.
list_features: inputs vendor (string, optional, default ""); returns {features: [{vendor, feature, version, expires, days_left, count}]} for all vendors or one; metadata only.
read_license_file: inputs vendor (string); returns {vendor, path, content}; content is the full FlexLM file including SIGN= and SN= values (confidential); audited as license_file_read.
lmreread: inputs vendor (string); returns {vendor, daemon, reread: true, loaded_features[]}; the daemon now serves the file on disk; error if the daemon is not running.
verify_license: inputs vendor (string); returns {vendor, daemon, daemon_status, loaded_matches_file, file_updated_at, reread_at, loaded_features[], expiring_within_30_days[], ok}.
notify_admin: inputs message (string); returns {delivered: true, to: labadmin, notification_id}.

## runtime tool (provided by Rakazo, not by an MCP server)

message_bot: inputs confirm_name (string, the target agent), intent (string: request|question|result|status|fyi), text (string); delivers the text to the target agent; the target's first turn is delivered back later as a new message with intent result.
