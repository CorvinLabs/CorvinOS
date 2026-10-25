"""Gap A-3 over a real process pair: CorvinTV settings survive a console restart.

Red on origin/main: ``video_producer_api._settings`` is a process-memory dict, so a second server
process answers the defaults again, and the change leaves no ``video_producer.settings_changed``
record. Green: the values live in the tenant's plugin registry record (the plugin's own
``tts_engine`` / ``max_video_length_minutes`` settings) and the change is on the tenant chain.
"""
from __future__ import annotations

import json
import time

from ._preconditions import require

require()

from . import harness  # noqa: E402
from .harness import Server  # noqa: E402

TENANT = "_default"


def _settings(srv, sess):
    r = harness.fetch(srv, sess, "/video/settings")
    assert r.status_code == 200, r.text[:300]
    return r.json()


def _wait_plugin(srv, sess, timeout=60.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = harness.fetch(srv, sess, "/video/settings")
        if r.status_code == 200:
            return r
        time.sleep(1.0)
    raise AssertionError(f"CorvinTV never became available: {r.status_code} {r.text[:200]}")


INSTALL = r"""
import sys
from corvin_console.routes import marketplace_resolve as _resolve
from corvin_plugins.state import PluginLifecycle
plugin_dir, manifest = _resolve.load_manifest("plugin:buildin-media-corvintv")
record = _resolve.record_from_manifest(manifest, plugin_dir=plugin_dir)
lc = PluginLifecycle(tenant_id=sys.argv[1], lifecycle_enabled=True)
lc.install(record, installed_by="sandbox")
lc.enable(record.plugin_id, consent_granted_by="sandbox")
print("installed", record.plugin_id)
"""


def _plant_plugin(sandbox) -> None:
    """The sandbox has no network: serve the sibling marketplace checkout through the cache path a
    fresh install would have synced into, then install + enable CorvinTV the way the host does."""
    cache = sandbox.corvin_home / "marketplace-cache"
    cache.mkdir(parents=True, exist_ok=True)
    link = cache / "Corvin-Marketplace"
    if not link.exists():
        link.symlink_to(harness.MAIN_ROOT.parent / "Corvin-Marketplace")
    if not (sandbox.tenant_home(TENANT) / "plugins" / "registry.yaml").exists():
        out = sandbox.run_child(INSTALL, TENANT)
        assert "installed" in out, out


def test_corvintv_settings_survive_a_console_restart_and_are_audited(sandbox, live_tripwire):
    _plant_plugin(sandbox)
    first = Server(sandbox, Server.CONSOLE, "tv1").start()
    try:
        sess = harness.mint_session(sandbox, TENANT)
        before = _wait_plugin(first, sess).json()
        assert (before["tts_engine"], before["max_duration_minutes"]) == ("auto", 60)

        put = harness.fetch(first, sess, "/video/settings", method="PUT",
                            json_body={"tts_engine": "gtts", "max_duration_minutes": 9})
        assert put.status_code == 200, put.text[:300]
        assert _settings(first, sess)["tts_engine"] == "gtts"
    finally:
        first.stop()

    second = Server(sandbox, Server.CONSOLE, "tv2").start()
    try:
        sess2 = harness.mint_session(sandbox, TENANT)
        again = _wait_plugin(second, sess2).json()
        assert (again["tts_engine"], again["max_duration_minutes"]) == ("gtts", 9), again
    finally:
        second.stop()

    # the plugin registry record is the one store, and the change is on the tenant chain
    registry = sandbox.tenant_home(TENANT) / "plugins" / "registry.yaml"
    text = registry.read_text(encoding="utf-8")
    assert "gtts" in text and "max_video_length_minutes: 9" in text, text[:600]
    chain = sandbox.tenant_home(TENANT) / "global" / "forge" / "audit.jsonl"
    events = [json.loads(ln) for ln in chain.read_text(encoding="utf-8").splitlines() if ln.strip()]
    changed = [e for e in events if e.get("event_type") == "video_producer.settings_changed"]
    assert len(changed) == 1, [e.get("event_type") for e in events][-15:]
    assert changed[0]["details"]["tts_engine"] == "gtts"
    assert changed[0]["details"]["previous_max_duration_minutes"] == 60
