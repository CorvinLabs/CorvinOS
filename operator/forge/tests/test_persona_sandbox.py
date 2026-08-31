"""S2 — Persona-aware sandbox: real E2E with a local HTTP server.

Spawns a local 127.0.0.1 HTTP server, forges a tool that urlopen()s it,
and runs it twice — once under FORGE_PERSONA=browser (network: allow per
bundle policy) and once under FORGE_PERSONA=coder (default deny).

Expected:
  browser → tool returns {ok: true, body_len: ...}, the body matches.
  coder   → tool returns {ok: false, error: "..."} because the bwrap
            sandbox unshared the network namespace.

Skips with a clear marker when bwrap is missing — without bwrap the
sandbox falls back to rlimits-only and the network test would trivially
pass under both personas (no namespace isolation), defeating the point.

Run as: python3 operator/forge/tests/test_persona_sandbox.py
"""
from __future__ import annotations

import http.server
import json
import os
import socketserver
import sys
import tempfile
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "forge"))

from forge.policy import Policy  # noqa: E402
from forge.registry import Registry  # noqa: E402
from forge.runner import run_tool  # noqa: E402
from forge.sandbox import have_bwrap  # noqa: E402


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


NET_TOOL_IMPL = '''#!/usr/bin/env python3
import json, sys, urllib.request, urllib.error
p = json.loads(sys.stdin.read())
try:
    with urllib.request.urlopen(p["url"], timeout=2) as r:
        body = r.read().decode()
    print(json.dumps({"ok": True, "body_len": len(body), "body": body}))
except Exception as e:
    print(json.dumps({"ok": False, "error": type(e).__name__ + ": " + str(e)}))
'''

NET_TOOL_SCHEMA = {
    "type": "object",
    "required": ["url"],
    "properties": {"url": {"type": "string"}},
}

EXPECTED_BODY = b"hello forge"


class _Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — http.server convention
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(EXPECTED_BODY)))
        self.end_headers()
        self.wfile.write(EXPECTED_BODY)

    def log_message(self, *_args, **_kwargs):  # quiet
        return


def _start_http_stub() -> tuple[socketserver.TCPServer, str]:
    """Returns (server, base_url). Caller must shutdown()."""
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    port = httpd.server_address[1]
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    return httpd, f"http://127.0.0.1:{port}/"


def main() -> int:
    if not have_bwrap():
        print("SKIP: bwrap not on PATH — persona-aware sandbox needs a real "
              "namespace; rlimits-only fallback can't enforce network "
              "isolation.")
        return 0

    print("[persona-aware sandbox: browser allow vs coder deny]")
    httpd, url = _start_http_stub()
    saved = os.environ.get("FORGE_PERSONA")
    saved_home = os.environ.get("CORVIN_HOME")
    try:
        with tempfile.TemporaryDirectory() as td:
            # Layer-16 v2 — sandbox the audit chain so the new
            # tool.network_share event lands in td, not in the user's home.
            os.environ["CORVIN_HOME"] = td
            reg = Registry(Path(td))
            reg.create("web.fetch", "fetch URL via urllib",
                       NET_TOOL_SCHEMA, NET_TOOL_IMPL)
            # Policy.load picks up bundle defaults — browser+research:allow.
            policy = Policy.load(Path(td))
            t("policy.network_for_persona('browser') == True",
              policy.network_for_persona("browser") is True)
            t("policy.network_for_persona('coder') == False",
              policy.network_for_persona("coder") is False)

            # ---- browser persona: bundle default = loopback DENY --------
            # Layer-16 v2 D — even with --share-net, the sitecustomize
            # shim refuses connect() to 127.0.0.0/8. The test stub binds
            # 127.0.0.1, so the urlopen() inside the tool must surface a
            # ConnectionRefusedError.
            t("policy.deny_loopback_for_persona('browser') == True (default)",
              policy.deny_loopback_for_persona("browser") is True)
            os.environ["FORGE_PERSONA"] = "browser"
            r_b_deny = run_tool(reg, "web.fetch", {"url": url},
                                permission_mode="yes", policy=policy)
            t("browser default: outer envelope ok=True (tool ran)",
              r_b_deny.ok,
              detail=f"error={getattr(r_b_deny, 'error', None)!r}")
            inner_bd = r_b_deny.data if isinstance(r_b_deny.data, dict) else {}
            t("browser default: inner ok=False (loopback blocked)",
              inner_bd.get("ok") is False,
              detail=f"data={inner_bd!r}")
            t("browser default: error mentions loopback-deny / refused",
              isinstance(inner_bd.get("error"), str)
              and ("loopback" in inner_bd["error"].lower()
                   or "refused" in inner_bd["error"].lower()),
              detail=f"error={inner_bd.get('error')!r}")

            # ---- browser persona with explicit loopback: allow opt-in --
            # Workspace policy lifts the loopback deny, so 127.0.0.1
            # becomes reachable for tests that genuinely need it.
            wp_loop = Path(td) / "policy.json"
            wp_loop.write_text(json.dumps({
                "persona_sandbox_overrides": {
                    "browser": {"network": "allow", "loopback": "allow"}
                }
            }))
            policy_loop = Policy.load(Path(td))
            t("workspace loopback:allow lifts deny",
              policy_loop.deny_loopback_for_persona("browser") is False)
            r_b = run_tool(reg, "web.fetch", {"url": url},
                           permission_mode="yes", policy=policy_loop)
            t("browser loopback-allow: outer envelope ok=True",
              r_b.ok,
              detail=f"error={getattr(r_b, 'error', None)!r}")
            inner_b = r_b.data if isinstance(r_b.data, dict) else {}
            t("browser loopback-allow: inner ok=True",
              inner_b.get("ok") is True,
              detail=f"data={inner_b!r}")
            t("browser loopback-allow: body matches stub",
              inner_b.get("body") == EXPECTED_BODY.decode(),
              detail=f"body={inner_b.get('body')!r}")
            t("browser loopback-allow: body_len matches",
              inner_b.get("body_len") == len(EXPECTED_BODY))
            wp_loop.unlink()

            # ---- coder persona: should fail with network error ----------
            os.environ["FORGE_PERSONA"] = "coder"
            r_c = run_tool(reg, "web.fetch", {"url": url},
                           permission_mode="yes", policy=policy)
            t("coder: outer envelope ok=True (tool ran, just failed inside)",
              r_c.ok,
              detail=f"error={getattr(r_c, 'error', None)!r}")
            inner_c = r_c.data if isinstance(r_c.data, dict) else {}
            t("coder: inner ok=False (network blocked)",
              inner_c.get("ok") is False,
              detail=f"data={inner_c!r}")
            t("coder: error mentions network / connect / unreachable",
              isinstance(inner_c.get("error"), str)
              and any(kw in inner_c["error"].lower()
                      for kw in ("network", "connect", "unreachable",
                                 "refused", "url", "errno")),
              detail=f"error={inner_c.get('error')!r}")

            # ---- explicit policy override: workspace tightens browser back to deny ---
            wp = Path(td) / "policy.json"
            wp.write_text(json.dumps({
                "persona_sandbox_overrides": {"browser": {"network": "deny"}}
            }))
            policy_tight = Policy.load(Path(td))
            t("workspace override flips browser to deny",
              policy_tight.network_for_persona("browser") is False)

            # ---- caller_persona kwarg overrides FORGE_PERSONA env -----------
            # Phase-1 hardening: the runner trusts the explicit kwarg over
            # the env. We need workspace loopback:allow so kwarg=browser
            # can reach the 127.0.0.1 stub (Layer-16 v2 D denies loopback
            # by default, but the kwarg-trust property is independent of
            # that and we want to test it isolated).
            wp.unlink()  # restore bundle defaults
            wp_kw = Path(td) / "policy.json"
            wp_kw.write_text(json.dumps({
                "persona_sandbox_overrides": {
                    "browser": {"network": "allow", "loopback": "allow"}
                }
            }))
            policy_kw = Policy.load(Path(td))
            os.environ["FORGE_PERSONA"] = "coder"
            r_kw = run_tool(reg, "web.fetch", {"url": url},
                            permission_mode="yes", policy=policy_kw,
                            caller_persona="browser")
            inner_kw = r_kw.data if isinstance(r_kw.data, dict) else {}
            t("caller_persona kwarg='browser' overrides env='coder' (network ok)",
              inner_kw.get("ok") is True,
              detail=f"data={inner_kw!r}")

            # And the inverse: env=browser, kwarg=coder → network must fail.
            os.environ["FORGE_PERSONA"] = "browser"
            r_kw2 = run_tool(reg, "web.fetch", {"url": url},
                             permission_mode="yes", policy=policy_kw,
                             caller_persona="coder")
            inner_kw2 = r_kw2.data if isinstance(r_kw2.data, dict) else {}
            t("caller_persona kwarg='coder' overrides env='browser' (network blocked)",
              inner_kw2.get("ok") is False,
              detail=f"data={inner_kw2!r}")
            wp_kw.unlink()

            # ---- Layer-16 v2 — tool.network_share audit visibility ------
            # Browser runs above shared the host network namespace. The
            # runner must have emitted a tool.network_share event for each
            # such run. Coder runs (deny) must NOT have emitted one.
            audit_file = Path(td) / "global" / "forge" / "audit.jsonl"
            t("audit file exists after network-allowing runs",
              audit_file.exists(), detail=str(audit_file))
            if audit_file.exists():
                events = []
                for line in audit_file.read_text().splitlines():
                    if line.strip():
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
                ns_events = [e for e in events
                             if e.get("event_type") == "tool.network_share"]
                t("at least one tool.network_share event emitted",
                  len(ns_events) >= 1,
                  detail=f"got {len(ns_events)} of "
                         f"{[e.get('event_type') for e in events]}")
                if ns_events:
                    personas = [e.get("details", {}).get("persona")
                                for e in ns_events]
                    t("network_share events all carry browser persona",
                      all(p == "browser" for p in personas),
                      detail=f"personas={personas}")
                    sandboxes = [e.get("details", {}).get("sandbox")
                                 for e in ns_events]
                    t("network_share events sandbox label "
                      "(bwrap+net or bwrap+net-noloop)",
                      all(s in ("bwrap+net", "bwrap+net-noloop")
                          for s in sandboxes),
                      detail=f"sandboxes={sandboxes}")
                    # Layer-16 v2 D — at least one run must have flipped
                    # to bwrap+net-noloop (the default deny path), and at
                    # least one must have stayed bwrap+net (opt-in loop).
                    t("network_share has both noloop + loop variants",
                      "bwrap+net-noloop" in sandboxes
                      and "bwrap+net" in sandboxes,
                      detail=f"sandboxes={sandboxes}")
                    deny_flags = [e.get("details", {}).get("deny_loopback")
                                  for e in ns_events]
                    t("network_share details carry deny_loopback bool",
                      all(isinstance(f, bool) for f in deny_flags),
                      detail=f"deny_loopback={deny_flags}")

    finally:
        httpd.shutdown()
        httpd.server_close()
        if saved is None:
            os.environ.pop("FORGE_PERSONA", None)
        else:
            os.environ["FORGE_PERSONA"] = saved
        if saved_home is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = saved_home

    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
