"""ADR-2294: the organisation label and GDPR role of a paired endpoint.

Covers the shared module every writer and reader uses (``endpoint_role.py``): value validation, the
"never guess" read side, the outbound rule (classified data to an endpoint with role ``unset`` is
refused, public data is not), the operator writer (audit-first, no free text in the chain) and a
structural ratchet that no other production file writes the role.

The real classifier runs; the chain writer is a capturing stand-in in the writer tests (the console and
federation E2E tests use the real chain).
"""
from __future__ import annotations

import ast
import json
import os
import stat
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
_REPO = _HERE.parents[2]

import endpoint_role as er  # noqa: E402


# ── values ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cfg", [
    {},
    {"gdpr_role": ""},
    {"gdpr_role": None},
    {"gdpr_role": 3},
    {"gdpr_role": "data processor"},
    {"gdpr_role": "Processor"},
    # nothing is inferred from what the peer or the URL says about itself
    {"label": "Acme Processor GmbH", "url": "https://processor.example.com/v1/a2a/receive"},
    {"organisation": "Acme Processing Ltd"},
])
def test_role_of_is_unset_unless_an_operator_stored_a_valid_role(cfg):
    assert er.role_of(cfg) == "unset"


@pytest.mark.parametrize("role", ["controller", "processor", "joint", "unset"])
def test_role_of_returns_a_stored_valid_role(role):
    assert er.role_of({"gdpr_role": role}) == role


def test_roles_are_a_closed_set_with_unset_as_default():
    assert er.GDPR_ROLES == ("controller", "processor", "joint", "unset")
    assert er.ROLE_UNSET == "unset"
    assert er.REASON_ROLE_UNSET == "endpoint_role_unset"


@pytest.mark.parametrize("value,expected", [
    ("Acme GmbH", "Acme GmbH"),
    ("  Acme GmbH  ", "Acme GmbH"),
    ("AT&T Labs", "AT&T Labs"),
    ("Müller & Söhne KG", "Müller & Söhne KG"),
    ("x" * 80, "x" * 80),
])
def test_valid_organisation_is_normalised(value, expected):
    assert er.normalise_organisation(value) == expected


@pytest.mark.parametrize("value", [
    "", "   ", "x" * 81, None, 7,
    "<b>Acme</b>", "Acme <script>", "[Acme](https://evil)", "`Acme`", "Acme {x}",
    "Acme\nGmbH", "Acme\x00", "Acme‮HbmG", "Acme\x1b[31m",
])
def test_invalid_organisation_is_refused(value):
    with pytest.raises(er.EndpointRoleError):
        er.normalise_organisation(value)


@pytest.mark.parametrize("value", ["", "Processor", "PROCESSOR", "data_processor", None, 1, "processor "])
def test_invalid_role_is_refused(value):
    with pytest.raises(er.EndpointRoleError):
        er.normalise_role(value)


def test_organisation_of_never_returns_an_invalid_stored_value():
    assert er.organisation_of({"organisation": "Acme GmbH"}) == "Acme GmbH"
    assert er.organisation_of({"organisation": "<b>x</b>"}) == ""
    assert er.organisation_of({}) == ""


# ── the outbound rule ─────────────────────────────────────────────────────

SECRET_TEXT = "token sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789 please review"


@pytest.mark.parametrize("text,grade", [
    ("[class:internal] quarterly numbers", "INTERNAL"),
    ("[class:confidential] customer list", "CONFIDENTIAL"),
    (SECRET_TEXT, "SECRET"),
])
def test_classified_text_to_an_unset_endpoint_is_refused(text, grade):
    assert er.outbound_refusal({"endpoint_id": "ep"}, text) == ("endpoint_role_unset", grade)
    assert er.outbound_refusal({"endpoint_id": "ep", "gdpr_role": "unset"}, text) == ("endpoint_role_unset", grade)


def test_public_text_to_an_unset_endpoint_is_not_refused():
    assert er.outbound_refusal({"endpoint_id": "ep"}, "Please review this public press release.") == (None, "PUBLIC")
    assert er.outbound_refusal({"endpoint_id": "ep"}, "") == (None, "PUBLIC")


@pytest.mark.parametrize("role", ["controller", "processor", "joint"])
def test_a_stated_role_lets_classified_text_through_this_rule(role):
    assert er.outbound_refusal({"gdpr_role": role}, "[class:confidential] customer list") == (None, "CONFIDENTIAL")


def test_classifier_failure_refuses_only_where_the_rule_could_apply(monkeypatch):
    import data_classification

    def boom(*a, **k):
        raise RuntimeError("classifier exploded")

    monkeypatch.setattr(data_classification, "classify_task", boom)
    assert er.outbound_refusal({}, "anything") == ("endpoint_role_unset", "")
    assert er.outbound_refusal({"gdpr_role": "processor"}, "anything") == (None, "")


def test_refusal_text_tells_the_operator_what_to_set_and_where():
    text = er.refusal_text()
    for needle in ("GDPR role", "organisation", "controller", "processor", "joint",
                   "console: Peers > <partner> > Role", "`corvin-a2a set-role"):
        assert needle in text, needle


# ── the operator writer ───────────────────────────────────────────────────

class _Emit:
    def __init__(self, fail: bool = False):
        self.calls: list[tuple[str, dict]] = []
        self.fail = fail

    def __call__(self, event, *, tenant_id, **details):
        if self.fail:
            raise RuntimeError("chain unavailable")
        self.calls.append((event, {"tenant_id": tenant_id, **details}))
        return "h" * 64


def _endpoint(d: Path, eid: str = "peer-b", **extra) -> Path:
    cfg = {"endpoint_id": eid, "url": "https://peer.example.com/v1/a2a/receive", "hmac_key": "a" * 64,
           "recv_key": "b" * 64, "instance_id": "iid-peer-b", "enabled": True, **extra}
    p = d / f"{eid}.json"
    p.write_text(json.dumps(cfg))
    p.chmod(0o600)
    return p


def test_set_endpoint_role_writes_both_fields_audit_first_without_free_text(tmp_path):
    path = _endpoint(tmp_path, label="Peer label")
    emit = _Emit()
    out = er.set_endpoint_role("peer-b", organisation="  Acme Analytics GmbH ", gdpr_role="processor",
                               actor="console:fp1234abcd", tenant_id="_default", endpoints_dir=tmp_path, emit=emit)
    assert out == {"endpoint_id": "peer-b", "organisation": "Acme Analytics GmbH", "gdpr_role": "processor"}
    cfg = json.loads(path.read_text())
    assert (cfg["organisation"], cfg["gdpr_role"]) == ("Acme Analytics GmbH", "processor")
    assert cfg["hmac_key"] == "a" * 64 and cfg["label"] == "Peer label" and cfg["enabled"] is True
    if not sys.platform.startswith("win"):
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert emit.calls == [("federation.endpoint_role_set", {
        "tenant_id": "_default", "endpoint_id": "peer-b", "gdpr_role": "processor", "actor": "console:fp1234abcd"})]
    assert "Acme" not in json.dumps(emit.calls), "the organisation text never enters the chain"


def test_audit_failure_writes_nothing(tmp_path):
    path = _endpoint(tmp_path)
    before = path.read_text()
    with pytest.raises(er.EndpointRoleError):
        er.set_endpoint_role("peer-b", organisation="Acme", gdpr_role="joint", actor="cli",
                             tenant_id="_default", endpoints_dir=tmp_path, emit=_Emit(fail=True))
    assert path.read_text() == before


def test_invalid_values_write_and_audit_nothing(tmp_path):
    path = _endpoint(tmp_path)
    before = path.read_text()
    emit = _Emit()
    for kw in ({"organisation": "", "gdpr_role": "processor"}, {"organisation": "Acme", "gdpr_role": "data_processor"},
               {"organisation": "<i>Acme</i>", "gdpr_role": "processor"}):
        with pytest.raises(er.EndpointRoleError):
            er.set_endpoint_role("peer-b", actor="cli", tenant_id="_default", endpoints_dir=tmp_path, emit=emit, **kw)
    with pytest.raises(er.EndpointRoleError):
        er.set_endpoint_role("peer-b", organisation="Acme", gdpr_role="joint", actor="anyone at all",
                             tenant_id="_default", endpoints_dir=tmp_path, emit=emit)
    assert path.read_text() == before and emit.calls == []


@pytest.mark.parametrize("eid", ["../x", "a/b", ".hidden", "C:x", ""])
def test_unsafe_endpoint_id_is_refused(tmp_path, eid):
    with pytest.raises(er.EndpointRoleError):
        er.set_endpoint_role(eid, organisation="Acme", gdpr_role="joint", actor="cli", tenant_id="_default",
                             endpoints_dir=tmp_path, emit=_Emit())


def test_unknown_endpoint_is_reported_as_not_found(tmp_path):
    with pytest.raises(er.EndpointNotFound):
        er.set_endpoint_role("nope", organisation="Acme", gdpr_role="joint", actor="cli", tenant_id="_default",
                             endpoints_dir=tmp_path, emit=_Emit())


def test_operator_can_reset_a_role_to_unset(tmp_path):
    path = _endpoint(tmp_path, organisation="Acme", gdpr_role="processor")
    er.set_endpoint_role("peer-b", organisation="Acme", gdpr_role="unset", actor="cli", tenant_id="_default",
                         endpoints_dir=tmp_path, emit=_Emit())
    assert er.role_of(json.loads(path.read_text())) == "unset"


def test_read_endpoint_roles_lists_every_endpoint_with_its_stated_role(tmp_path):
    _endpoint(tmp_path, "peer-a", organisation="Acme GmbH", gdpr_role="controller")
    _endpoint(tmp_path, "peer-b")
    (tmp_path / "broken.json").write_text("{not json")
    roles = er.read_endpoint_roles(tmp_path)
    assert roles["peer-a"] == ("Acme GmbH", "controller")
    assert roles["peer-b"] == ("", "unset")
    assert roles["broken"] == ("", "unset")


# ── only the operator writer writes the role ─────────────────────────────

# Production files that may WRITE the ``gdpr_role`` key (``x["gdpr_role"] = ...``, ``x.setdefault(
# "gdpr_role", ...)``, ``x.update(gdpr_role=...)`` or a dict literal with that key), with the reason.
# Everything else that writes endpoint records (pairing, re-pairing, the receiver's reconnect handler,
# the relay, the CLI's other commands) never does, so a peer-driven write cannot set or change it.
_ROLE_KEY_ALLOWED = {
    "corvin_operator/bridges/shared/endpoint_role.py": "the one writer",
    "core/console/corvin_console/routes/remote_trigger_log.py": "read-only listing dict (GET endpoints)",
    "corvin_operator/voice/scripts/corvin_a2a.py": "read-only listing dict (list-endpoints)",
}


def _writes_role_key(tree: ast.AST) -> bool:
    def is_key(n: ast.AST | None) -> bool:
        return isinstance(n, ast.Constant) and n.value == "gdpr_role"

    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store) and is_key(node.slice):
            return True
        if isinstance(node, ast.Dict) and any(is_key(k) for k in node.keys):
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "setdefault" and node.args and is_key(node.args[0]):
                return True
            if node.func.attr == "update" and any(kw.arg == "gdpr_role" for kw in node.keywords):
                return True
    return False


def test_the_ratchet_detects_each_write_shape():
    for src in ('cfg["gdpr_role"] = "processor"', 'x = {"gdpr_role": r}', 'cfg.setdefault("gdpr_role", r)',
                'cfg.update(gdpr_role=r)'):
        assert _writes_role_key(ast.parse(src)), src
    for src in ('r = cfg.get("gdpr_role")', 'ALLOWED = frozenset({"gdpr_role"})', 'v = cfg["gdpr_role"]'):
        assert not _writes_role_key(ast.parse(src)), src


def test_only_operator_paths_write_the_role_key():
    writers: set[str] = set()
    scanned = 0
    for root in ("core", "corvin_operator"):
        for path in (_REPO / root).rglob("*.py"):
            rel = path.relative_to(_REPO).as_posix()
            name = path.name
            if "/tests/" in f"/{rel}" or name.startswith("test_") or name == "conftest.py" or "node_modules" in rel:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            scanned += 1
            if _writes_role_key(tree):
                writers.add(rel)
    assert scanned > 500, "positive control: the scan must reach the production tree"
    assert "corvin_operator/bridges/shared/endpoint_role.py" in writers, "positive control: the writer is seen"
    offenders = sorted(writers - set(_ROLE_KEY_ALLOWED))
    assert not offenders, f"files writing 'gdpr_role' outside the operator paths: {offenders}"
    for rel in _ROLE_KEY_ALLOWED:
        assert (_REPO / rel).is_file(), f"allowlisted file is gone: {rel}"


def test_refusal_text_names_the_class_and_the_way_out():
    for grade in ("INTERNAL", "CONFIDENTIAL", "SECRET"):
        text = er.refusal_text(grade)
        assert grade in text and "role" in text.lower()
        assert text in er.refusal_texts()
    assert er.refusal_text("<script>") == er.refusal_text()      # never an arbitrary string
    assert er.refusal_text() in er.refusal_texts()


def test_outbound_refusal_grade_for_long_digit_runs_is_reported_not_hidden():
    # Known limit: the shared detector grades ordinary long digit runs; the text must still say which class.
    reason, grade = er.outbound_refusal({}, "build 20261027123456")
    if reason:
        assert grade and grade in er.refusal_text(grade)
