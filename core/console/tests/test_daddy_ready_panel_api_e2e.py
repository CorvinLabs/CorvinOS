"""DaddyReady panel backend, end to end through the real console routes.

Installs the real private plugin (sibling CorvinOS-Apps checkout) into a SANDBOX tenant, points the plugin's config and
data at temp directories holding SYNTHETIC data (invented notes, a generated test video), and drives every route over
HTTP: auth + enable gating, overview, knowledge list/search/entry, episodes, brief, Range video, settings read/save,
locked settings, audit records. No real data about any person is read or written.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_OPERATOR = _REPO / "corvin_operator"
for _p in [str(_OPERATOR), str(_OPERATOR / "license"), str(_OPERATOR / "forge"), str(_REPO / "core" / "console"),
           str(_OPERATOR / "bridges" / "shared"), str(_REPO / "core" / "plugins")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

_APPS_PLUGINS = _REPO.parent / "CorvinOS-Apps" / "plugins"
_PLUGIN_SRC = _APPS_PLUGINS / "contributor" / "orchestration" / "daddy_ready" / "src"
_MKT_BUILDIN = _REPO.parent / "Corvin-Marketplace" / "plugins" / "buildin"
_BASE = "/v1/console/api/v1/marketplace/plugins"
_API = "/v1/console/plugins/daddy-ready"
_INDEX_ID = "plugin:contributor-orchestration-daddy_ready"
_PURGED = ("corvin_console", "corvin_gateway", "forge")
_NOTES = ["Synthetic note one about stacking cups", "Synthetic note two about a new sound"]


def _cli(env: dict, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    full = {"PATH": os.environ["PATH"], "PYTHONPATH": str(_PLUGIN_SRC), **env}
    return subprocess.run([sys.executable, "-m", "daddy_ready", *args], input=stdin, capture_output=True, text=True,
                          env=full, timeout=60)


@contextmanager
def _sandbox(tmp: Path):
    home = tmp / "home"
    for sub in ("auth", "forge", "console/sessions"):
        (home / "tenants" / "_default" / "global" / sub).mkdir(parents=True)
    xdg_cfg, xdg_data = tmp / "xdg-config", tmp / "xdg-data"
    cfgdir = xdg_cfg / "daddy-ready" / "default"
    cfgdir.mkdir(parents=True)
    pics = tmp / "pics"
    pics.mkdir()
    (pics / "a.txt").write_text("synthetic file")
    (cfgdir / "config.yaml").write_text(
        "version: 1\nuser: default\nchild: {name: Testchild, birth_date: 2025-03-06}\n"
        "sources:\n  - key: pics\n    type: local_folder\n    options: {path: %s}\n" % pics)
    (cfgdir / "config.yaml").chmod(0o600)
    keys = ("CORVIN_HOME", "CORVIN_TENANT_ID", "VOICE_AUDIT_PATH", "CORVIN_MARKETPLACE_ROOT",
            "CORVIN_PRIVATE_PLUGIN_ROOTS", "XDG_CONFIG_HOME", "XDG_DATA_HOME")
    prev = {k: os.environ.get(k) for k in keys}
    os.environ.update({"CORVIN_HOME": str(home), "CORVIN_TENANT_ID": "_default",
                       "VOICE_AUDIT_PATH": str(home / "audit.jsonl"), "CORVIN_MARKETPLACE_ROOT": str(_MKT_BUILDIN),
                       "CORVIN_PRIVATE_PLUGIN_ROOTS": str(_APPS_PLUGINS), "XDG_CONFIG_HOME": str(xdg_cfg),
                       "XDG_DATA_HOME": str(xdg_data)})
    saved = {k: v for k, v in sys.modules.items() if k.startswith(_PURGED)}
    try:
        for k in list(sys.modules):
            if k.startswith(_PURGED):
                del sys.modules[k]
        from corvin_console import auth as _auth
        from corvin_console.app import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        rec = _auth.create_session(tenant_id="_default", token_fingerprint="test-fp")
        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", rec.sid)
        yield client, {"X-CSRF-Token": _auth.derive_csrf_token(rec.csrf_secret, rec.sid)}, home, xdg_data / "daddy-ready" / "default"
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for k in list(sys.modules):
            if k.startswith(_PURGED):
                del sys.modules[k]
        sys.modules.update(saved)


@unittest.skipUnless(_PLUGIN_SRC.is_dir() and _MKT_BUILDIN.is_dir() and shutil.which("ffmpeg"),
                     "needs the sibling CorvinOS-Apps and Corvin-Marketplace checkouts and ffmpeg")
class TestDaddyReadyPanelApi(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _enable(self, client, hdr):
        r = client.post(f"{_BASE}/{_INDEX_ID}/install", json={"wait": True, "enable_after_install": True,
                                                                  "consent_granted": True}, headers=hdr)
        self.assertEqual(r.json().get("status"), "completed", r.text)
        self.assertTrue(r.json().get("enabled"), r.text)

    def _seed(self, data_env):
        env = {"HOME": str(self.tmp), "XDG_CONFIG_HOME": str(self.tmp / "xdg-config"),
               "XDG_DATA_HOME": str(self.tmp / "xdg-data")}
        for n in _NOTES:
            self.assertEqual(_cli(env, "chat", "note", n).returncode, 0)
        self.assertEqual(_cli(env, "plan", "--day", "2099-02-03").returncode, 0)
        ep = data_env / "episodes" / "2099-02-03"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=2:size=160x120:rate=10",
                        "-f", "lavfi", "-i", "sine=duration=2", "-shortest", "-c:v", "libx264", "-c:a", "aac",
                        str(ep / "episode.mp4")], check=True, timeout=120)

    def test_gating_before_enable_and_without_a_session(self):
        with _sandbox(self.tmp) as (client, hdr, _home, _data):
            self.assertEqual(client.get(f"{_API}/overview").status_code, 404)          # not installed
            anon = client.__class__(client.app, raise_server_exceptions=False)
            self.assertEqual(anon.get(f"{_API}/overview").status_code, 401)            # no session
            self._enable(client, hdr)
            self.assertEqual(client.get(f"{_API}/overview").status_code, 200)
            client.post("/v1/console/plugins/daddy_ready/disable", headers=hdr)
            self.assertEqual(client.get(f"{_API}/overview").status_code, 404)          # disabled again

    def test_every_read_route_serves_the_synthetic_data(self):
        with _sandbox(self.tmp) as (client, hdr, _home, data):
            self._enable(client, hdr)
            self._seed(data)
            ov = client.get(f"{_API}/overview").json()
            self.assertEqual(ov["child_name"], "Testchild")
            self.assertEqual(ov["counts"]["observation"], 2)
            kb = client.get(f"{_API}/kb", params={"type": "observation"}).json()
            self.assertEqual(kb["total"], 2)
            hit = client.get(f"{_API}/kb", params={"q": "SOUND"}).json()
            self.assertEqual(hit["total"], 1)
            entry = client.get(f"{_API}/kb/{hit['items'][0]['id']}").json()
            self.assertEqual(entry["body"], _NOTES[1])
            self.assertEqual(client.get(f"{_API}/kb/OBS-000000000000").status_code, 404)
            self.assertEqual(client.get(f"{_API}/kb/..%2F..%2Fx").status_code, 404)
            eps = client.get(f"{_API}/episodes").json()
            day = next(e for e in eps["episodes"] if e["day"] == "2099-02-03")
            self.assertTrue(day["has_brief"] and day["has_video"] and day["duration_s"] > 1)
            self.assertIn("Morgen-Episode", client.get(f"{_API}/episodes/2099-02-03/brief").json()["markdown"])
            self.assertEqual(client.get(f"{_API}/episodes/2099-2-3/brief").status_code, 422)

    def test_video_is_served_with_range_support(self):
        with _sandbox(self.tmp) as (client, hdr, _home, data):
            self._enable(client, hdr)
            self._seed(data)
            full = client.get(f"{_API}/episodes/2099-02-03/video")
            self.assertEqual(full.status_code, 200)
            self.assertEqual(full.headers["content-type"], "video/mp4")
            self.assertEqual(full.content, (data / "episodes" / "2099-02-03" / "episode.mp4").read_bytes())
            part = client.get(f"{_API}/episodes/2099-02-03/video", headers={"Range": "bytes=0-99"})
            self.assertEqual(part.status_code, 206)
            self.assertEqual(len(part.content), 100)
            self.assertEqual(client.get(f"{_API}/episodes/2099-02-04/video").status_code, 404)
            self.assertIn("no-store", full.headers["cache-control"])

    def test_settings_read_save_validate_and_lock(self):
        with _sandbox(self.tmp) as (client, hdr, _home, _data):
            self._enable(client, hdr)
            doc = client.get(f"{_API}/settings").json()
            self.assertIn("panel", doc["blocks"])
            self.assertIn("local_folder", doc["source_types"])
            cfg = doc["config"]
            cfg["episode"] = {"target_minutes": 3}
            ok = client.put(f"{_API}/settings", json=cfg, headers=hdr)
            self.assertEqual(ok.status_code, 200, ok.text)
            self.assertEqual(client.get(f"{_API}/settings").json()["config"]["episode"]["target_minutes"], 3)
            bad = client.put(f"{_API}/settings", json={**cfg, "episode": {"target_minutes": 99}}, headers=hdr)
            self.assertEqual(bad.status_code, 422)
            self.assertIn("target_minutes", bad.json()["detail"])
            locked = client.put(f"{_API}/settings", json={**cfg, "plugin_dirs": ["/tmp"]}, headers=hdr)
            self.assertEqual(locked.status_code, 422)
            self.assertIn("runs code", locked.json()["detail"])
            self.assertEqual(client.put(f"{_API}/settings", json=cfg).status_code, 403)       # no CSRF token
            self.assertEqual(client.get(f"{_API}/settings").json()["config"]["episode"]["target_minutes"], 3)

    def test_graph_and_render_routes(self):
        with _sandbox(self.tmp) as (client, hdr, _home, data):
            self._enable(client, hdr)
            self._seed(data)
            g = client.get(f"{_API}/graph").json()
            self.assertTrue(any(n["type"] == "episode" for n in g["nodes"]))
            self.assertNotIn(str(self.tmp), json.dumps(g))                       # no paths leave the plugin
            self.assertEqual(client.post(f"{_API}/episodes/2099-02-03/render").status_code, 403)   # CSRF
            r = client.post(f"{_API}/episodes/2099-02-03/render", headers=hdr)
            self.assertEqual(r.status_code, 202, r.text)                          # background, never blocking
            self.assertEqual(client.post(f"{_API}/episodes/2099-2-3/render", headers=hdr).status_code, 422)

    def test_audit_records_carry_the_operation_only(self):
        with _sandbox(self.tmp) as (client, hdr, home, data):
            self._enable(client, hdr)
            self._seed(data)
            hit = client.get(f"{_API}/kb", params={"q": "cups"}).json()
            client.get(f"{_API}/kb/{hit['items'][0]['id']}")
            cfg = client.get(f"{_API}/settings").json()["config"]
            client.put(f"{_API}/settings", json=cfg, headers=hdr)
            chain = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
            lines = [json.loads(l) for l in chain.read_text().splitlines() if "daddy_ready." in l]
            events = [l.get("event") or l.get("event_type") for l in lines]
            self.assertIn("daddy_ready.panel_read", events)
            self.assertIn("daddy_ready.settings_saved", events)
            blob = json.dumps(lines)
            for secret in ("cups", "Synthetic", "stacking", hit["items"][0]["id"]):
                self.assertNotIn(secret, blob)


if __name__ == "__main__":
    unittest.main()
