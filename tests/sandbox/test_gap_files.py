"""Files panel (S-2i): protected stores are not downloadable, a symlink delete never removes its
target, delete/mkdir judge the resolved path, protected stores refuse writes, denials are audited.

Red on origin/main (925406772): ``routes/files.py`` ``_access`` is an exact-name deny-list (no
``connector_vault.json``, no ``recall.db-wal`` / ``audit.jsonl.postloss-*`` siblings), ``files_delete``
and ``files_mkdir`` grade the RAW path with ``_access`` and ``files_delete`` unlinks the symlink-RESOLVED
target. Every request goes over HTTP to a real uvicorn console in the two-tenant sandbox.
"""
from __future__ import annotations

import json
import os

import pytest

from ._preconditions import require

require()

from . import harness  # noqa: E402
from .helpers_gap import action_records, verify_tenant_chain  # noqa: E402

VAULT_MARKER = "SBX-FILES-VAULT-KEY-91c2"


def _put(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def tenant(sandbox, session_for):
    sess = session_for("sbxf")
    home = sandbox.tenant_home("sbxf")
    glob = home / "global"
    _put(glob / "connector_vault.json", json.dumps({"openai": VAULT_MARKER}))
    _put(glob / "forge" / "audit.jsonl.postloss-1", "{}")
    _put(home / "memory" / "recall.db-wal", "wal")
    _put(home / "recovered" / "audit.backup-1", "{}")
    _put(glob / "github-audit.jsonl", "{}")
    # a victim a symlink will point at (protected: secrets.json is a "none" file)
    victim = _put(glob / "secrets.json", json.dumps({"k": "victim"}))
    (home / "files").mkdir(parents=True, exist_ok=True)
    os.symlink("../global/secrets.json", home / "files" / "lnk_secret")
    os.symlink("../global/consent", home / "files" / "lnk_consent_dir")
    (glob / "consent").mkdir(parents=True, exist_ok=True)
    _put(glob / "consent" / "c.json", "{}")
    _put(glob / "web_chat" / "s1.turns.jsonl", '{"user_text": "hello"}\n')
    _put(home / "session_ledger" / "discord" / "c1" / "ledger.jsonl", "{}\n")
    # positive controls
    _put(home / "files" / "readme.txt", "hello user file")
    _put(home / "files" / "vault-notes.txt", "my own notes")
    _put(home / "forge" / "tool_artifact.json", '{"ok": true}')
    return {"sess": sess, "home": home, "victim": victim}


def _get(server, sess, rel, endpoint="download"):
    return harness.fetch(server, sess, f"/files/{endpoint}?path={rel}")


@pytest.mark.parametrize("rel", [
    "global/connector_vault.json",
    "global/forge/audit.jsonl.postloss-1",
    "memory/recall.db-wal",
    "recovered/audit.backup-1",
    "global/github-audit.jsonl",
])
def test_G_files_protected_stores_are_not_downloadable(server, tenant, rel):
    r = _get(server, tenant["sess"], rel)
    assert r.status_code == 403, f"{rel}: {r.status_code} {r.text[:200]}"
    assert VAULT_MARKER not in r.text
    c = _get(server, tenant["sess"], rel, endpoint="content")
    assert c.status_code == 403 and VAULT_MARKER not in c.text


def test_G_files_vault_is_not_listed(server, tenant):
    r = harness.fetch(server, tenant["sess"], "/files/tree?path=global&depth=2")
    assert r.status_code == 200
    assert "connector_vault" not in r.text


def test_G_files_symlink_delete_never_removes_the_target(server, tenant):
    r = harness.fetch(server, tenant["sess"], "/files?path=files/lnk_secret", method="DELETE")
    assert tenant["victim"].is_file(), "DELETE through a symlink removed the protected target"
    assert r.status_code in (200, 403), (r.text[:200], server.log_tail(3000))
    # the link itself may go (it is the user's own entry); the victim must stay.
    if r.status_code == 200:
        assert not (tenant["home"] / "files" / "lnk_secret").is_symlink()


def test_G_files_mkdir_through_a_symlink_into_a_protected_store_is_refused(server, tenant):
    r = harness.fetch(server, tenant["sess"], "/files/mkdir", method="POST",
                      json_body={"path": "files/lnk_consent_dir/planted"})
    assert not (tenant["home"] / "global" / "consent" / "planted").exists(), \
        f"mkdir created a directory inside global/consent: {r.status_code}"
    assert r.status_code == 403, r.text[:200]


@pytest.mark.parametrize("rel", [
    "global/web_chat/s1.turns.jsonl",
    "session_ledger/discord/c1/ledger.jsonl",
    "global/consent/c.json",
])
def test_G_files_protected_stores_refuse_delete(server, tenant, rel):
    path = tenant["home"] / rel
    assert path.is_file()
    r = harness.fetch(server, tenant["sess"], f"/files?path={rel}", method="DELETE")
    assert r.status_code == 403, f"{rel}: {r.status_code} {r.text[:200]}"
    assert path.is_file()


def test_G_files_upload_into_a_protected_store_is_refused(server, tenant):
    import httpx
    sess = tenant["sess"]
    with server.client() as c:
        r = c.post("/files/upload?dir=global/consent", cookies=sess.cookies(),
                   headers={"x-csrf-token": sess.csrf}, files={"file": ("grant.json", b"{}")})
    assert r.status_code == 403, r.text[:200]
    assert not (tenant["home"] / "global" / "consent" / "grant.json").exists()
    assert isinstance(r, httpx.Response)


def test_G_files_art15_reads_of_ledger_and_chat_logs_still_work(server, tenant):
    for rel in ("global/web_chat/s1.turns.jsonl", "session_ledger/discord/c1/ledger.jsonl"):
        r = _get(server, tenant["sess"], rel)
        assert r.status_code == 200, f"{rel}: {r.status_code}"


def test_G_files_positive_controls_user_files_and_artifacts(server, tenant):
    sess = tenant["sess"]
    assert _get(server, sess, "files/readme.txt").status_code == 200
    assert _get(server, sess, "files/vault-notes.txt").status_code == 200, \
        "the family rule must not hide user files under files/"
    assert _get(server, sess, "forge/tool_artifact.json").status_code == 200
    with server.client() as c:
        up = c.post("/files/upload?dir=files", cookies=sess.cookies(),
                    headers={"x-csrf-token": sess.csrf}, files={"file": ("new.txt", b"fresh")})
    assert up.status_code == 200, up.text[:200]
    d = harness.fetch(server, sess, "/files?path=files/new.txt", method="DELETE")
    assert d.status_code == 200 and not (tenant["home"] / "files" / "new.txt").exists()
    m = harness.fetch(server, sess, "/files/mkdir", method="POST", json_body={"path": "files/sub/dir"})
    assert m.status_code == 200 and (tenant["home"] / "files" / "sub" / "dir").is_dir()


def test_G_files_denials_are_audited_without_a_path_and_the_chain_verifies(sandbox, server, session_for):
    # The console audit writer is one-tenant-per-process (AuditTenantMismatch drops a record of
    # any other tenant), so the audit proof runs as the process tenant ``_default``.
    sess = session_for("_default")
    vault = _put(sandbox.tenant_home("_default") / "global" / "connector_vault.json",
                 json.dumps({"openai": VAULT_MARKER}))
    assert vault.is_file()
    r = harness.fetch(server, sess, "/files/download?path=global/connector_vault.json")
    assert r.status_code == 403
    denied = action_records(sandbox, "_default", "files.download")
    assert denied, ("no console.action_denied for the refused download",
                    [str(p) for p in sandbox.chain_files()])
    rec = denied[-1]
    assert rec["event_type"] == "console.action_denied"
    assert (rec.get("details") or {}).get("reason") == "protected"
    assert "connector_vault" not in json.dumps(rec), "the denial record must not carry the path"
    chain = verify_tenant_chain(sandbox, "_default")
    assert chain["ok"], chain
