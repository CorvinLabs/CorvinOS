#!/usr/bin/env python3
"""A2-RESIDUAL (2026-07-20 refutation): the ``corvin-a2a`` CLI writers
``label-endpoint`` and ``migrate-attestation`` perform cross-process
read-modify-write on the same A2A config files the Console PATCH routes and the
bridge receiver rewrite. They MUST take ``a2a_friendship.config_file_lock`` so a
concurrent lock-holding writer's edit is never silently overwritten (advisory
flock only serialises between lock-takers).

flock attaches to the open file description, so a second ``open()`` handle
conflicts even within one process — which lets these tests prove the CLI writer
BLOCKS while another writer holds the lock, deterministically, with threads.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent
_SHARED = _SCRIPTS.parents[1] / "bridges" / "shared"
for _p in (str(_SCRIPTS), str(_SHARED)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import corvin_a2a  # noqa: E402
import a2a_friendship  # noqa: E402


@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    origins = tmp_path / "remote_origins"
    endpoints = tmp_path / "remote_endpoints"
    origins.mkdir()
    endpoints.mkdir()
    monkeypatch.setenv("REMOTE_ORIGINS_DIR", str(origins))
    monkeypatch.setenv("REMOTE_ENDPOINTS_DIR", str(endpoints))
    return origins, endpoints


def _write(path: Path, cfg: dict) -> None:
    path.write_text(json.dumps(cfg), encoding="utf-8")
    os.chmod(path, 0o600)


def test_label_endpoint_waits_for_lock_holder(dirs):
    _origins, endpoints = dirs
    _write(endpoints / "fr1.json", {
        "endpoint_id": "fr1", "url": "http://8.8.8.8/v1/a2a/receive",
        "hmac_key": "a" * 64, "recv_key": "b" * 64,
        "enabled": True, "state": "ACTIVE", "_friendship": True,
    })
    rc: list[int] = []

    def do_label():
        rc.append(corvin_a2a._cmd_label_endpoint(
            argparse.Namespace(endpoint_id="fr1", label="renamed-peer")))

    with a2a_friendship.config_file_lock(endpoints):
        t = threading.Thread(target=do_label, daemon=True)
        t.start()
        t.join(timeout=0.4)
        assert t.is_alive(), (
            "label-endpoint must block while another writer holds the "
            "endpoint-dir config file lock"
        )
    t.join(timeout=10)
    assert not t.is_alive()
    assert rc == [0]
    cfg = json.loads((endpoints / "fr1.json").read_text("utf-8"))
    assert cfg["label"] == "renamed-peer"


def test_migrate_attestation_waits_for_lock_holder(dirs):
    origins, _endpoints = dirs
    # Pre-M4 origin: missing require_network_attestation.
    _write(origins / "fr1.json", {
        "origin_id": "fr1", "hmac_key": "a" * 64, "recv_key": "b" * 64,
        "enabled": True, "state": "ACTIVE", "_friendship": True,
    })
    done: list[int] = []

    def do_migrate():
        done.append(corvin_a2a._cmd_migrate_attestation(
            argparse.Namespace(dry_run=False)))

    with a2a_friendship.config_file_lock(origins):
        t = threading.Thread(target=do_migrate, daemon=True)
        t.start()
        t.join(timeout=0.4)
        assert t.is_alive(), (
            "migrate-attestation must block while another writer holds the "
            "origins-dir config file lock"
        )
    t.join(timeout=10)
    assert not t.is_alive()
    assert done == [0]
    cfg = json.loads((origins / "fr1.json").read_text("utf-8"))
    assert cfg["require_network_attestation"] is True


def test_migrate_attestation_dry_run_takes_no_lock(dirs):
    """A dry-run writes nothing, so it must NOT block on the lock (a null
    context is used) — verifying the lock is scoped to real writes only."""
    origins, _endpoints = dirs
    _write(origins / "fr1.json", {
        "origin_id": "fr1", "hmac_key": "a" * 64, "recv_key": "b" * 64,
        "enabled": True, "state": "ACTIVE", "_friendship": True,
    })
    done: list[int] = []

    def do_migrate():
        done.append(corvin_a2a._cmd_migrate_attestation(
            argparse.Namespace(dry_run=True)))

    with a2a_friendship.config_file_lock(origins):
        t = threading.Thread(target=do_migrate, daemon=True)
        t.start()
        t.join(timeout=5)
        assert not t.is_alive(), "dry-run must not block on the config lock"
    assert done == [0]
    # Nothing was written.
    cfg = json.loads((origins / "fr1.json").read_text("utf-8"))
    assert "require_network_attestation" not in cfg


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


@pytest.mark.parametrize("bad_url", ["http://169.254.169.254", "http://127.0.0.1:9", "http://[::1]:80"])
def test_import_token_refuses_forbidden_peer_urls(dirs, bad_url):
    """Round-2 review: the CLI import stored an issuer-chosen metadata/loopback
    URL unchecked, and the connection then kept contacting it."""
    origins, endpoints = dirs
    _tok, token_str = a2a_friendship.create_friendship_token(url=bad_url, label="evil")
    rc = corvin_a2a._cmd_import_token(argparse.Namespace(
        token=token_str, url=None, overwrite=False, dry_run=False))
    assert rc == 1
    assert list(origins.glob("*.json")) == [] and list(endpoints.glob("*.json")) == []


def test_revoke_token_notifies_the_peer_and_deletes_under_the_lock(dirs, tmp_path, monkeypatch):
    """Round 3: the CLI revoke mirrored none of the console's fixes."""
    origins, endpoints = dirs
    monkeypatch.setenv("REMOTE_PENDING_FRIENDSHIPS_DIR", str(tmp_path / "pending"))
    kid = "kid-cli-revoke-1"
    for d, extra in ((origins, {"origin_id": kid}), (endpoints, {"endpoint_id": kid, "url": ""})):
        _write(d / f"{kid}.json", {"hmac_key": "ab" * 32, "recv_key": "cd" * 32,
                                   "_friendship": True, **extra})
    sent = []
    monkeypatch.setattr(a2a_friendship, "send_revoke_notice",
                        lambda k, **kw: sent.append(k) or {"ok": True})
    assert corvin_a2a._cmd_revoke_token(argparse.Namespace(kid=kid)) == 0
    assert sent == [kid]
    assert not (origins / f"{kid}.json").exists() and not (endpoints / f"{kid}.json").exists()
    assert corvin_a2a._cmd_revoke_token(argparse.Namespace(kid="../x")) == 2


def test_create_token_saves_the_pending_record(dirs, tmp_path, monkeypatch):
    """Round 4: without it the redeemer's ack was always refused (403)."""
    monkeypatch.setenv("REMOTE_PENDING_FRIENDSHIPS_DIR", str(tmp_path / "pending"))
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))  # never the live install
    monkeypatch.setenv("CORVIN_A2A_RELAY_URL", "off")
    assert corvin_a2a.main(["create-token", "--url", "http://10.0.0.5:8775",
                            "--label", "cli", "--ttl", "1d"]) == 0
    assert len(list((tmp_path / "pending").glob("*.json"))) == 1
