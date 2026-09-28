"""``license`` and ``corvin_operator.license`` are ONE package (adversarial
review 2026-09-28).

The hosts load the licence through ``license.validator`` (gateway lifespan,
``corvin_console.standalone``, ``adapter.py``); every capability gate reads
``corvin_operator.license.capability_api``. They were two module objects, so
the loaded licence never reached a gate: a paying member resolved to "free"
everywhere — 402 on ``POST /v1/console/skills/manual``, ``licensing/verify``
answering free, ``Registry.create`` refused.

Each case runs in a FRESH interpreter with the host PYTHONPATH (repo root +
``corvin_operator`` + every ``core/*`` dir), imports ``corvin_gateway.app`` like
the service does, and loads the licence with the hosts' own call. The token
is a real Ed25519-signed ``license`` token verified by the real validator; the
only test seam is its public key, added to ``SESSION_SERVER_KEY_RING`` under a
test kid (production keys cannot be minted here), and the revocation fetch is
stubbed to "none revoked" so no network is touched.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]

_CHILD = r'''
import base64, json, os, sys, time
from pathlib import Path

member = sys.argv[1] == "member"
if member:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    def b64(b):
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    sk = Ed25519PrivateKey.generate()
    pub = sk.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    head = b64(json.dumps({"alg": "EdDSA", "kid": "sess-twintest"}).encode())
    body = b64(json.dumps({
        "iss": "corvinlabs.io", "type": "license", "tier": "member",
        "exp": int(time.time()) + 3600, "jti": "twintest01"}).encode())
    sig = b64(sk.sign(f"{head}.{body}".encode()))
    # Where the console's "Apply Key" persists it (``global/license.key``,
    # 0600): the boot load AND every later disk-only ``reload_from_disk``
    # (``corvin_console.auth`` calls it per session) must find it.
    key = Path(os.environ["CORVIN_HOME"]) / "global" / "license.key"
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text(f"CORVIN-{head}.{body}.{sig}")
    key.chmod(0o600)

import corvin_gateway.app  # noqa: F401 — the service's own import order

import license.validator as LV
if member:
    LV.SESSION_SERVER_KEY_RING = {**LV.SESSION_SERVER_KEY_RING,
                                  "sess-twintest": base64.b64encode(pub).decode()}
LV._fetch_revoked_fps = lambda *a, **k: []

from license.validator import load_license_from_env   # the hosts' call
load_license_from_env()

import corvin_operator.license.capability_api as CA
import corvin_operator.license.validator as CV
import license.capability_api as CA2
out = {
    "same_pkg": sys.modules["license"] is sys.modules["corvin_operator.license"],
    "same_validator": CV is LV,
    "same_capability_api": CA is CA2,
    "validator_tier": LV.active_tier(),
    "capability_tier": CA.active_tier(),
    "license_file": sys.modules["license"].__file__,
}

from forge.registry import Registry
root = Path(os.environ["CORVIN_HOME"]) / "probe-forge"
try:
    Registry(root).create("twin_probe", "probe", {"type": "object"},
                          "print(1)\n", runtime="python", scope="session")
    out["registry_create"] = "allowed"
except PermissionError as e:
    out["registry_create"] = "denied"

from corvin_console import auth
from corvin_console.app import router
from fastapi import FastAPI
from fastapi.testclient import TestClient

app = FastAPI()
app.include_router(router, prefix="/v1/console")
rec = auth.create_session(tenant_id="_default", token_fingerprint="twin-fp")
client = TestClient(app, raise_server_exceptions=False)
client.cookies.set("corvin_console_sid", rec.sid)
h = {"X-CSRF-Token": auth.derive_csrf_token(rec.csrf_secret, rec.sid)}
r = client.post("/v1/console/licensing/verify", headers=h,
                json={"capability": "forge.create"})
out["verify_status"] = r.status_code
out["verify"] = r.json()
r = client.post("/v1/console/skills/manual", headers=h,
                json={"name": "assistant.twin_probe",
                      "body": "# twin probe\n\nSay hello.\n"})
out["skills_manual_status"] = r.status_code
print("RESULT " + json.dumps(out))
'''


# ``corvin-webui.service``'s PYTHONPATH (console, gateway, license, compliance,
# forge, skill-forge, bridges/shared, plugins) plus the repo root and
# ``corvin_operator/`` the venv ``.pth`` / ``corvin_core`` bootstrap add. Not
# every ``core/*`` dir: ``core/dispatch/token.py`` shadows the stdlib ``token``.
_HOST_DIRS = (
    "", "corvin_operator", "core/console", "core/gateway", "core/license",
    "core/compliance", "core/plugins", "core/awpkg", "core/compute",
    "core/delegate", "core/observability", "core/orchestration", "core/workflows",
    "corvin_operator/forge", "corvin_operator/skill-forge",
    "corvin_operator/bridges/shared",
)


def _host_pythonpath() -> str:
    return os.pathsep.join(str(_REPO / d) if d else str(_REPO) for d in _HOST_DIRS)


def _run(tier: str, tmp_path: Path) -> dict:
    home = tmp_path / "home"
    for sub in ("auth", "forge", "console/sessions"):
        (home / "tenants" / "_default" / "global" / sub).mkdir(parents=True)
    env = {k: v for k, v in os.environ.items()
           if k not in ("CORVIN_LICENSE_KEY", "FORGE_ROOT", "VOICE_AUDIT_PATH")}
    env.update({
        "PYTHONPATH": _host_pythonpath(),
        "CORVIN_HOME": str(home),
        "CORVIN_TENANT_ID": "_default",
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "HOME": str(tmp_path / "userhome"),
        "CORVIN_AUDIT_ANCHOR_KEY": str(tmp_path / "anchor.key"),
        # never let an import-time vite build run (no npm on PATH)
        "PATH": os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep)
                                if "node" not in p and "npm" not in p),
    })
    (tmp_path / "userhome").mkdir()
    proc = subprocess.run([sys.executable, "-c", _CHILD, tier], cwd=str(tmp_path),
                          env=env, capture_output=True, text=True, timeout=300)
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    assert lines, f"child failed (rc={proc.returncode}):\n{proc.stderr[-4000:]}"
    return json.loads(lines[-1][len("RESULT "):])


def test_member_licence_loaded_by_the_host_reaches_every_gate(tmp_path):
    pytest.importorskip("cryptography")
    out = _run("member", tmp_path)
    assert out["same_pkg"] and out["same_validator"] and out["same_capability_api"], out
    assert out["validator_tier"] == "member", out
    assert out["capability_tier"] == "member", out
    assert out["registry_create"] == "allowed", out
    assert out["verify_status"] == 200 and out["verify"]["allowed"] is True, out
    assert out["verify"]["tier"] == "member", out
    assert out["skills_manual_status"] == 200, out


def test_free_tier_stays_refused(tmp_path):
    out = _run("free", tmp_path)
    assert out["same_pkg"] and out["same_validator"] and out["same_capability_api"], out
    assert out["capability_tier"] == "free", out
    assert out["registry_create"] == "denied", out
    assert out["verify_status"] == 200 and out["verify"]["allowed"] is False, out
    assert out["skills_manual_status"] == 402, out
