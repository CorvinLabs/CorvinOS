"""ST3 E2E: AWP-style output envelope {ok, status, data, error, meta}.

Validates the runner's auto-wrap behaviour through the MCP path:
  - tool prints plain JSON → server wraps it into the standard envelope
  - tool prints a full envelope → server passes it through (with meta defaulted)
  - tool reports ok=false → surfaces as MCP isError=true
  - tool reports error string → surfaces as MCP isError=true
  - meta.deterministic + meta.side_effects flags survive the roundtrip

Fictional task: forge a "stat compute" tool with two variants (plain output
vs envelope output) and a "broken" tool that signals failure via envelope.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_mcp import MCPClient


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


PLAIN_IMPL = '''#!/usr/bin/env python3
import json, sys
p = json.loads(sys.stdin.read())
print(json.dumps({"sum": p["a"] + p["b"], "summary": f"sum={p['a']+p['b']}"}))
'''

ENVELOPE_IMPL = '''#!/usr/bin/env python3
import json, sys
p = json.loads(sys.stdin.read())
total = p["a"] + p["b"]
print(json.dumps({
    "ok": True,
    "status": 200,
    "data": {"sum": total, "summary": f"sum={total}"},
    "error": None,
    "meta": {"deterministic": True, "side_effects": False}
}))
'''

ERROR_ENVELOPE_IMPL = '''#!/usr/bin/env python3
import json, sys
json.loads(sys.stdin.read())
print(json.dumps({
    "ok": False, "status": 422,
    "data": None,
    "error": "domain error: a must be positive",
    "meta": {}
}))
'''

OK_FALSE_IMPL = '''#!/usr/bin/env python3
import json, sys
json.loads(sys.stdin.read())
print(json.dumps({
    "ok": False, "status": 500,
    "data": None,
    "error": None,
    "meta": {}
}))
'''

ADD_SCHEMA = {
    "type": "object",
    "required": ["a", "b"],
    "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
}

NOOP_SCHEMA = {"type": "object", "properties": {}}


def _forge(client, name, schema, impl):
    return client.request("tools/call", {
        "name": "forge_tool",
        "arguments": {
            "name": name,
            "description": name,
            "input_schema": schema,
            "impl": impl,
        },
    })


def test_plain_output_gets_wrapped():
    print("\n[plain JSON output → server wraps into envelope]")
    with tempfile.TemporaryDirectory() as td:
        client = MCPClient(Path(td))
        try:
            client.initialize()
            _forge(client, "add_plain", ADD_SCHEMA, PLAIN_IMPL)
            resp = client.request("tools/call", {
                "name": "add_plain",
                "arguments": {"a": 7, "b": 35},
            })
        finally:
            client.close()

        result = resp["result"]
        t("call ok", result.get("isError") is False)
        struct = result.get("structuredContent", {})
        env = struct.get("envelope", {})
        t("envelope.ok = True", env.get("ok") is True)
        t("envelope.status = 200", env.get("status") == 200)
        t("envelope.error is None", env.get("error") is None)
        t("envelope.meta is a dict", isinstance(env.get("meta"), dict))
        t("envelope.data.sum == 42",
          (env.get("data") or {}).get("sum") == 42)
        t("structured.data is the unwrapped data field",
          struct.get("data") == env.get("data"))


def test_envelope_passthrough_preserves_meta():
    print("\n[full envelope from tool → passed through, meta preserved]")
    with tempfile.TemporaryDirectory() as td:
        client = MCPClient(Path(td))
        try:
            client.initialize()
            _forge(client, "add_env", ADD_SCHEMA, ENVELOPE_IMPL)
            resp = client.request("tools/call", {
                "name": "add_env",
                "arguments": {"a": 100, "b": -42},
            })
        finally:
            client.close()

        struct = resp["result"].get("structuredContent", {})
        env = struct.get("envelope", {})
        t("envelope.data.sum == 58",
          (env.get("data") or {}).get("sum") == 58)
        meta = env.get("meta", {})
        t("meta.deterministic preserved",
          meta.get("deterministic") is True)
        t("meta.side_effects preserved",
          meta.get("side_effects") is False)


def test_envelope_with_error_surfaces_as_isError():
    print("\n[envelope with error string → MCP isError=true]")
    with tempfile.TemporaryDirectory() as td:
        client = MCPClient(Path(td))
        try:
            client.initialize()
            _forge(client, "broken", NOOP_SCHEMA, ERROR_ENVELOPE_IMPL)
            resp = client.request("tools/call",
                                  {"name": "broken", "arguments": {}})
        finally:
            client.close()

        result = resp["result"]
        t("isError=True", result.get("isError") is True)
        text = result["content"][0]["text"]
        t("error message includes domain message",
          "domain error" in text or "must be positive" in text,
          detail=text[:120])


def test_envelope_with_ok_false_also_surfaces_error():
    print("\n[envelope with ok=false but no error string → still isError]")
    with tempfile.TemporaryDirectory() as td:
        client = MCPClient(Path(td))
        try:
            client.initialize()
            _forge(client, "broken_silent", NOOP_SCHEMA, OK_FALSE_IMPL)
            resp = client.request("tools/call",
                                  {"name": "broken_silent", "arguments": {}})
        finally:
            client.close()

        t("ok=false alone is enough → isError=True",
          resp["result"].get("isError") is True)


def test_run_completion_records_envelope():
    print("\n[run_completion's stdout.json holds the full envelope]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        client = MCPClient(td)
        try:
            client.initialize()
            _forge(client, "add_env", ADD_SCHEMA, ENVELOPE_IMPL)
            resp = client.request("tools/call", {
                "name": "add_env",
                "arguments": {"a": 1, "b": 2},
            })
        finally:
            client.close()

        run_id = resp["result"]["structuredContent"]["run_id"]
        stdout_json = td / "runs" / run_id / "stdout.json"
        envelope_on_disk = json.loads(stdout_json.read_text())
        t("on-disk envelope.meta.deterministic = True",
          envelope_on_disk.get("meta", {}).get("deterministic") is True)
        t("on-disk envelope.data.sum = 3",
          envelope_on_disk.get("data", {}).get("sum") == 3)


def main() -> int:
    test_plain_output_gets_wrapped()
    test_envelope_passthrough_preserves_meta()
    test_envelope_with_error_surfaces_as_isError()
    test_envelope_with_ok_false_also_surfaces_error()
    test_run_completion_records_envelope()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
