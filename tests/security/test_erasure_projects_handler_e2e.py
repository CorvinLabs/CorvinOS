"""T-0386 / ADR-2321 Decision 13 (REVIEW-0061 M10) - the L36 erasure layer ``L-projects``, through the real ErasureOrchestrator
with the real handler chain.

The projects store has no per-person key, so for any subject the layer deletes nothing and reports SKIPPED with its note; every
planted file and every project directory is byte-identical afterwards; the subject id appears in no log line.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
for _p in (_REPO / "corvin_operator" / "bridges" / "shared", _REPO / "corvin_operator" / "forge", _REPO / "core" / "console"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

SUBJECT = "web:sess-projects-erase-me"


def _tree(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "dir"
    return out


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(h))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    from forge import security_events as se
    monkeypatch.setattr(se, "_ANCHOR_KEY", None)
    monkeypatch.setattr(se, "_ANCHOR_KEY_LOADED", False)
    return h


def test_l_projects_reports_skipped_and_deletes_nothing(home, tmp_path, caplog):
    import erasure_handlers as eh
    from erasure_orchestrator import ErasureOrchestrator, ErasureRequest, LayerStatus, OverallStatus, make_forge_audit_writer
    from core.projects import registry

    # the projects store as the console writes it, plus every store ADR-2321..2323 adds, all naming the subject
    proj_store = home / "tenants" / "_default" / "projects"
    proj_store.mkdir(parents=True)
    managed, _o = registry.ensure_managed_root("_default")
    pdir = managed / "alpha"
    pdir.mkdir()
    (pdir / "project.yaml").write_text("schema: 1\nid: alpha\ntitle: Alpha\ntenant: _default\n")
    (pdir / "notes.md").write_text(f"written for {SUBJECT}\n")
    own = tmp_path / "code" / "beta"
    own.mkdir(parents=True)
    (own / "README.md").write_text(f"{SUBJECT}\n")
    registry.register("_default", "alpha", pdir, "adopted@1")
    registry.register("_default", "beta", own, "adopted@1")
    (proj_store / "picker.json").write_text(json.dumps({"recent": [str(own)], "user": SUBJECT}))
    (proj_store / "staging" / "imp-1").mkdir(parents=True)
    (proj_store / "staging" / "imp-1" / "bundle.json").write_text(json.dumps({"by": SUBJECT}))
    (proj_store / "exports" / "job-1").mkdir(parents=True)
    (proj_store / "exports" / "job-1" / "alpha.zip").write_bytes(b"PK\x03\x04" + SUBJECT.encode())
    trash = managed / ".trash" / "gamma-20261110T000000Z"
    trash.mkdir(parents=True)
    (trash / "trash.json").write_text(json.dumps({"id": "gamma", "title": SUBJECT}))
    roots = [proj_store, managed, own]
    before = [_tree(r) for r in roots]

    chain_ids = [h.layer_id for h in eh.real_handler_chain("_default")]
    assert "L-projects" in chain_ids, "the handler is registered in the real chain"
    audit = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    orch = ErasureOrchestrator(tenant_id="_default", trail_dir=home / "tenants" / "_default" / "global" / "erasure",
                               audit_writer=make_forge_audit_writer(audit))
    for h in eh.real_handler_chain("_default"):
        orch.register_handler(h)
    with caplog.at_level(logging.DEBUG):
        res = orch.execute(ErasureRequest(subject_id=SUBJECT, requester="dpo@example.com", tenant_id="_default"))
    layer = {r.layer_id: r for r in res.per_layer}["L-projects"]
    assert layer.status == LayerStatus.SKIPPED
    assert layer.count == 0
    assert layer.reason == eh.ProjectsMetadataHandler.NOTE
    assert "no per-person key" in layer.reason and "Projects panel" in layer.reason
    assert res.overall_status == OverallStatus.COMPLETED, [(r.layer_id, r.status) for r in res.per_layer if r.status != LayerStatus.SKIPPED]
    assert [_tree(r) for r in roots] == before, "the erasure changed a projects file or a project directory"
    assert SUBJECT not in caplog.text, "the subject id reached a log line"
    from forge import security_events as se
    ok, problems = se.verify_chain(audit)
    assert ok, problems


def test_the_handler_alone_never_reports_applied():
    import erasure_handlers as eh
    from erasure_orchestrator import LayerStatus
    for sid in ("u1", "web:x", "telegram:42"):
        r = eh.ProjectsMetadataHandler("_default").purge(sid, "er-test")
        assert r.status == LayerStatus.SKIPPED and r.count == 0
        assert sid not in r.reason


def _share_base():
    """The share suite's HTTP harness (a sandboxed console with two tenants), imported lazily so this file's two tests above keep
    their own light fixture."""
    for p in (_REPO / "tests" / "e2e", _REPO / "core" / "console" / "tests", _REPO):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    from tests.e2e import test_projects_share_e2e as share
    return share


def test_l_projects_leaves_real_import_staging_and_export_jobs_untouched():
    """T-0403 (REVIEW-0061 M10): an import staging and an export job produced through the REAL routes - not planted - are
    byte-identical after an erasure run through ErasureOrchestrator with the real chain, and L-projects reports SKIPPED. They are
    bounded by their TTLs (1 h, 24 h) and the host budget, never by Art. 17."""
    import unittest
    from unittest import mock

    share = _share_base()
    result: dict = {}

    class Run(share.ShareBase):
        def runTest(self):
            import erasure_handlers as eh
            from erasure_orchestrator import ErasureOrchestrator, ErasureRequest, LayerStatus, OverallStatus, make_forge_audit_writer
            with self.ctx() as c:
                pid, root = self.create(c, "Erase check")
                (root / "notes.md").write_text(f"written for {SUBJECT}\n")
                # an export job: the threshold lowered so a small project runs as a job and its file stays under exports/
                with mock.patch.object(c.svc, "EXPORT_SYNC_MAX_BYTES", 0):
                    r = self.export(c, [pid], parts={"git_history": False})
                self.assertEqual(r.status_code, 202, r.text[:300])
                job = r.json()["job_id"]
                deadline = time.monotonic() + 120
                while c.own.get(f"{share._URL}/-/exports/{job}").json()["state"] == "running" and time.monotonic() < deadline:
                    time.sleep(0.2)
                self.assertEqual(c.own.get(f"{share._URL}/-/exports/{job}").json()["state"], "done")
                # an import staging: a real bundle uploaded and validated
                data = share.make_bundle(self.tmp / "e.zip", {"omega": {"a.txt": SUBJECT.encode()}}).read_bytes()
                v = c.own.post(share._V, files={"file": ("e.zip", data, "application/zip")}, headers=c.h)
                self.assertEqual(v.status_code, 200, v.text[:300])
                store = c.home / "tenants" / "_default" / "projects"
                exports, staging = store / "exports", store / "import_staging"
                self.assertEqual([p.name for p in exports.iterdir()], [job])
                self.assertEqual([p.name for p in staging.iterdir()], [v.json()["staging_id"]])
                self.assertTrue(any(p.suffix == ".zip" for p in (exports / job).iterdir()))         # positive control: the files exist
                self.assertTrue((staging / v.json()["staging_id"] / "bundle.zip").is_file())
                roots = [exports, staging, root]
                before = [_tree(x) for x in roots]
                audit = c.home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
                orch = ErasureOrchestrator(tenant_id="_default", trail_dir=c.home / "tenants" / "_default" / "global" / "erasure",
                                           audit_writer=make_forge_audit_writer(audit))
                for h in eh.real_handler_chain("_default"):
                    orch.register_handler(h)
                res = orch.execute(ErasureRequest(subject_id=SUBJECT, requester="dpo@example.com", tenant_id="_default"))
                layer = {x.layer_id: x for x in res.per_layer}["L-projects"]
                self.assertEqual((layer.status, layer.count), (LayerStatus.SKIPPED, 0))
                self.assertEqual(res.overall_status, OverallStatus.COMPLETED,
                                 [(x.layer_id, x.status) for x in res.per_layer if x.status != LayerStatus.SKIPPED])
                self.assertEqual([_tree(x) for x in roots], before, "the erasure changed an export job, a staging or the project")
                # still usable afterwards: the job file downloads, the staging answers its report
                self.assertEqual(c.own.get(f"{share._URL}/-/exports/{job}/file").status_code, 200)
                self.assertEqual(c.own.get(f"{share._URL}/-/import/{v.json()['staging_id']}").status_code, 200)
                result["ok"] = True

    outcome = unittest.TextTestRunner(stream=open(os.devnull, "w"), verbosity=0).run(Run())
    assert outcome.wasSuccessful(), [str(e[1])[-1500:] for e in outcome.errors + outcome.failures]
    assert result.get("ok")
