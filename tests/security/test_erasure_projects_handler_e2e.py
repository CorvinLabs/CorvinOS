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
