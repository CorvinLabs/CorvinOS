"""ST6 E2E: x-redact masks secrets in the run manifest.

Validates that:
  - schema fields with x-redact: true are written as "<redacted>" in
    run_manifest.json
  - the *real* secret still reaches the tool's stdin (so the tool can use it)
  - the cache key is computed from the real payload, so identical real
    inputs still cache-hit even though the manifest hides them

Fictional task: a "fetch with bearer" tool that takes an api_key and
echoes the first/last char of it back as a (very fake) "auth probe".
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


# Tool prints the first+last char of api_key so we can prove it received
# the real value (not the redacted placeholder). It does NOT echo the full
# key (that would be a real-world leak).
PROBE_IMPL = '''#!/usr/bin/env python3
import json, sys
p = json.loads(sys.stdin.read())
k = p["api_key"]
probe = (k[0] + k[-1]) if len(k) >= 2 else k
print(json.dumps({
    "ok": True, "status": 200,
    "data": {"len": len(k), "probe": probe},
    "error": None,
    "meta": {"deterministic": True}
}))
'''

PROBE_SCHEMA = {
    "type": "object",
    "required": ["api_key", "endpoint"],
    "properties": {
        "api_key":  {"type": "string", "x-redact": True},
        "endpoint": {"type": "string"},
    },
}


def _forge(client, name, schema, impl, *, meta=None):
    args = {"name": name, "description": name,
            "input_schema": schema, "impl": impl}
    if meta is not None:
        args["meta"] = meta
    return client.request("tools/call",
                          {"name": "forge_tool", "arguments": args})


def test_secret_is_redacted_in_manifest_but_reaches_tool():
    print("\n[secret redacted in manifest, real value reaches the tool]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        client = MCPClient(td)
        try:
            client.initialize()
            _forge(client, "probe", PROBE_SCHEMA, PROBE_IMPL,
                   meta={"deterministic": True})
            secret = "sk-deadbeef-cafe-1234"
            resp = client.request("tools/call", {
                "name": "probe",
                "arguments": {"api_key": secret, "endpoint": "/v1/me"},
            })
        finally:
            client.close()

        result = resp["result"]
        t("call ok", result.get("isError") is False)
        struct = result.get("structuredContent", {})
        run_id = struct["run_id"]
        # Tool got the real key (probe = first+last char + length match)
        data = struct["data"]
        t("tool received real api_key (probe = first+last char)",
          data.get("probe") == secret[0] + secret[-1] and
          data.get("len") == len(secret),
          detail=f"probe={data.get('probe')} len={data.get('len')}")

        # Manifest on disk: api_key is redacted, endpoint is NOT
        manifest = json.loads(
            (td / "runs" / run_id / "run_manifest.json").read_text()
        )
        t("manifest.input.api_key == '<redacted>'",
          manifest["input"].get("api_key") == "<redacted>")
        t("manifest.input.endpoint = real value (not redacted)",
          manifest["input"].get("endpoint") == "/v1/me")
        # The secret is NOT in the manifest text anywhere
        manifest_text = (td / "runs" / run_id / "run_manifest.json").read_text()
        t("secret string not present anywhere in manifest text",
          secret not in manifest_text)


def test_redact_does_not_break_cache():
    print("\n[redact + deterministic: identical real inputs still cache-hit]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        client = MCPClient(td)
        try:
            client.initialize()
            _forge(client, "probe", PROBE_SCHEMA, PROBE_IMPL,
                   meta={"deterministic": True})
            args = {"api_key": "sk-the-same-secret", "endpoint": "/x"}
            r1 = client.request("tools/call",
                                {"name": "probe", "arguments": args})
            r2 = client.request("tools/call",
                                {"name": "probe", "arguments": args})
        finally:
            client.close()

        s1 = r1["result"]["structuredContent"]
        s2 = r2["result"]["structuredContent"]
        t("first call: real sandbox",
          s1.get("sandbox") in ("bwrap", "rlimits"))
        t("second call: cache hit", s2.get("sandbox") == "cache",
          detail=f"sandbox={s2.get('sandbox')}")
        # both runs produced same data
        t("same data both calls",
          s1["data"] == s2["data"])


def test_changed_secret_means_cache_miss():
    print("\n[different secret → different cache key → real run again]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        client = MCPClient(td)
        try:
            client.initialize()
            _forge(client, "probe", PROBE_SCHEMA, PROBE_IMPL,
                   meta={"deterministic": True})
            r1 = client.request("tools/call", {
                "name": "probe",
                "arguments": {"api_key": "sk-aaaa", "endpoint": "/x"},
            })
            r2 = client.request("tools/call", {
                "name": "probe",
                "arguments": {"api_key": "sk-bbbb", "endpoint": "/x"},
            })
        finally:
            client.close()

        s1 = r1["result"]["structuredContent"]
        s2 = r2["result"]["structuredContent"]
        t("first call: real sandbox",
          s1.get("sandbox") in ("bwrap", "rlimits"))
        t("second call (diff secret): real sandbox, NOT cache",
          s2.get("sandbox") in ("bwrap", "rlimits"),
          detail=f"sandbox={s2.get('sandbox')}")
        # Different probes proves the tool actually received the different keys
        t("different probes prove tool got different real values",
          s1["data"]["probe"] != s2["data"]["probe"])


def main() -> int:
    test_secret_is_redacted_in_manifest_but_reaches_tool()
    test_redact_does_not_break_cache()
    test_changed_secret_means_cache_miss()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
