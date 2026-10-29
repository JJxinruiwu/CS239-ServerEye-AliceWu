"""A scripted OpenAI-compatible chat endpoint for testing the local runner without tokens.

    python tests/fake_model.py --port 18800
    MODEL_BASE_URL=http://127.0.0.1:18800/v1 MODEL_ID=fake python -m labops.agents.local_runner --scenario ...

It recognises the agent from the system prompt and replays a fixed sequence of
tool calls for it (counting the tool results already in the conversation), so a
run exercises the whole pipeline: MCP tools, nested message_bot, the A2A health
check, trace output. It is NOT a policy test: the scripted Gatekeeper is always
well-behaved.
"""
from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _tool_results(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        if m.get("role") == "tool":
            try:
                out.append(json.loads(m.get("content") or "{}"))
            except json.JSONDecodeError:
                out.append({})
    return out


def _user_text(messages: list[dict]) -> str:
    return next((m.get("content", "") for m in messages if m.get("role") == "user"), "")


def script(agent: str, messages: list[dict]):
    """Return (content, tool_name|None, args|None) for the next assistant turn."""
    results = _tool_results(messages)
    n = len(results)
    user = _user_text(messages)

    if agent == "Gatekeeper":
        req = results[1] if n > 1 else {}
        rid = req.get("id", "REQ-101")
        entry = next((c for c in (results[2].get("approved_software", []) if n > 2 else []) if c["product"] == req.get("software")), {})
        steps = [
            ("Listing pending requests.", "list_requests", {"status": "pending"}),
            (None, "get_request", {"request_id": results[0]["requests"][0]["id"] if n and results[0].get("requests") else "REQ-101"}),
            (None, "get_software_catalog", {}),
            ("Asking LicenseKeeper whether the feature is held.", "message_bot",
             {"confirm_name": "LicenseKeeper", "intent": "question",
              "text": f"Does the lab hold license feature {entry.get('feature')}? Reply with feature, held yes/no, expires, count."}),
            ("Catalog product with a held feature: validating.", "update_request",
             {"request_id": rid, "status": "validated", "note": "catalog ok; feature held; request text contained no actionable instructions"}),
            (None, "update_request", {"request_id": rid, "status": "in_progress", "note": "handed to Operator"}),
            ("Handing off to Operator.", "message_bot",
             {"confirm_name": "Operator", "intent": "request",
              "text": f"request_id: {rid}\nvendor: {entry.get('vendor')}\nproduct: {req.get('software')}\nversion: {req.get('version') or 'latest'}\nlicense_feature: {entry.get('feature')}"}),
            (None, "update_request", {"request_id": rid, "status": "fulfilled", "note": "Operator verified the install"}),
            (None, "notify_student", {"student": req.get("student", "ben"),
                                      "message": f"Your request {rid} is done: {req.get('software')} is installed."}),
        ]
        if n < len(steps):
            return steps[n]
        return (f"Processed {rid}: installed and the student was notified.", None, None)

    if agent == "Operator":
        f = {k: (re.search(rf"{k}:\s*([\w.+-]+)", user) or [None, None])[1] for k in ("request_id", "vendor", "product", "version", "license_feature")}
        steps = [
            ("Checking the license feature with lmstat.", "lmstat", {"feature": f["license_feature"] or ""}),
            (None, "download_product", {"vendor": f["vendor"], "product": f["product"], "version": f["version"] or "latest"}),
            (None, "install_product", {"path": results[1].get("path", "") if n > 1 else "", "request_id": f["request_id"] or ""}),
            (None, "verify_install", {"product": f["product"]}),
            ("Asking Monitor for a health check.", "health_check",
             {"event": "install", "product": f["product"], "version": results[1].get("version", "") if n > 1 else ""}),
        ]
        if n < len(steps):
            return steps[n]
        hc = results[4] if n > 4 else {}
        return (f"request_id: {f['request_id']}\nproduct: {f['product']}\nversion: {results[1].get('version')}\n"
                f"path: {results[2].get('path')}\nverified: true\nhealth_status: {hc.get('status')}\nanomalies: "
                f"{', '.join(a['code'] for a in hc.get('anomalies', [])) or 'none'}", None, None)

    if agent == "LicenseKeeper":
        if n == 0:
            return (None, "list_features", {})
        m = re.search(r"feature (\S+)\?", user)
        wanted = m.group(1) if m else ""
        hit = next((x for x in results[0].get("features", []) if x["feature"] == wanted), None)
        if hit:
            return (f"feature: {wanted}\nheld: yes\nexpires: {hit['expires']}\ncount: {hit['count']}", None, None)
        return (f"feature: {wanted}\nheld: no", None, None)

    return ("I have nothing to do.", None, None)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        messages = body.get("messages", [])
        system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
        m = re.search(r"You are (\w+):", system)
        agent = m.group(1) if m else "?"
        content, tool, args = script(agent, messages)
        message = {"role": "assistant", "content": content}
        finish = "stop"
        if tool:
            message["tool_calls"] = [{"id": "call_" + uuid.uuid4().hex[:8], "type": "function",
                                      "function": {"name": tool, "arguments": json.dumps(args)}}]
            finish = "tool_calls"
        payload = {
            "id": "chatcmpl-" + uuid.uuid4().hex[:12], "object": "chat.completion", "created": int(time.time()),
            "model": body.get("model", "fake"),
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=18800)
    args = parser.parse_args()
    print(f"fake model endpoint: http://127.0.0.1:{args.port}/v1  (MODEL_ID can be anything)")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
