"""Forge Bundle validator (ADR-2229 Phase 1).

Every rejection test builds a REAL ZIP that differs from a passing bundle in
exactly one defect, and the passing bundle is asserted first
(`test_valid_bundle_passes`) — a validator that rejects everything would fail
that positive control instead of passing every negative test vacuously.
"""
from __future__ import annotations

import hashlib
import io
import json
import stat
import zipfile

import pytest

from core.forge_bundle import BundleRejected, validate_bundle
from core.forge_bundle import validate as validate_mod

# ── builders ─────────────────────────────────────────────────────────────────


def _zip(entries: dict[str, bytes], compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _skill_zip() -> bytes:
    return _zip({
        "summarize/skill.json": b'{"name": "summarize", "version": "1.0.0"}',
        "summarize/src/skill.py": b"def run(text):\n    return text[:100]\n",
    })


def _artifact(kind: str, aid: str, version: str, files: dict[str, bytes], requires=None) -> dict:
    prefix = f"artifacts/{kind}/{aid}@{version}/"
    art = {
        "kind": kind,
        "id": aid,
        "version": version,
        "files": [
            {"path": prefix + name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
            for name, data in files.items()
        ],
    }
    if requires is not None:
        art["requires"] = requires
    return art


def _default_artifacts() -> list[tuple[dict, dict[str, bytes]]]:
    specs = [
        ("skill", "summarize", "1.0.0", {"summarize-1.0.0.zip": _skill_zip()}, None),
        ("tool", "csv.count", "0.2.0", {
            "spec.json": b'{"name": "csv.count", "runtime": "python"}',
            # Ordinary code that the prose detectors would flag; must pass.
            "impl.py": b'def run(req):\n    token = req.headers["x-token"]\n    return len(token)\n',
        }, [{"kind": "skill", "id": "summarize", "version": "1.0.0"}]),
        ("layer", "acme.audit-l34", "1.0.0", {
            "manifest.json": json.dumps({"id": "acme.audit-l34", "version": "1.0.0",
                                         "targets": [{"layer_id": "L34"}]}).encode(),
        }, [{"kind": "layer", "id": "base.rule"}]),
        ("plugin", "acme-audit-sink", "0.5.0", {"plugin.zip": _zip({"plugin.json": b'{"id": "acme-audit-sink"}'})},
         [{"kind": "tool", "id": "csv.count"}]),
    ]
    out = []
    for kind, aid, version, files, requires in specs:
        art = _artifact(kind, aid, version, files, requires)
        out.append((art, {f["path"]: files[f["path"].rsplit("/", 1)[1]] for f in art["files"]}))
    return out


def make_bundle(*, envelope_patch=None, artifacts=None, extra: dict[str, bytes] | None = None,
                drop: tuple[str, ...] = (), raw_envelope: bytes | None = None,
                compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    arts = artifacts if artifacts is not None else _default_artifacts()
    envelope = {
        "format": "corvin.forge-bundle",
        "format_version": 1,
        "id": "acme-automation",
        "version": "2.0.0",
        "created_at": "2026-10-06T12:00:00Z",
        "description": "test bundle",
        "artifacts": [a for a, _ in arts],
    }
    if envelope_patch:
        envelope_patch(envelope)
    entries: dict[str, bytes] = {
        "forge-bundle.json": raw_envelope if raw_envelope is not None else json.dumps(envelope).encode()
    }
    for _, files in arts:
        entries.update(files)
    entries.update(extra or {})
    for name in drop:
        entries.pop(name)
    return _zip(entries, compression)


def _rejected(data: bytes, stage: str, *, known=None, match: str | None = None) -> BundleRejected:
    with pytest.raises(BundleRejected) as exc:
        validate_bundle(data, known=known)
    assert exc.value.stage == stage, exc.value
    if match:
        assert match in exc.value.reason, exc.value.reason
    return exc.value


KNOWN = {"layer": {"base.rule": {"1.0.0"}}}

# ── positive control ─────────────────────────────────────────────────────────


def test_valid_bundle_passes():
    report = validate_bundle(make_bundle())
    assert [a.kind for a in report.envelope.artifacts] == ["skill", "tool", "layer", "plugin"]
    assert report.origin_verified is False
    # No inventory given: the one requirement outside the bundle is reported, not silently passed.
    assert [(r.kind, r.id) for r in report.unchecked_references] == [("layer", "base.rule")]
    assert report.unscanned_files == ()
    assert report.total_uncompressed_bytes > 0


def test_valid_bundle_with_inventory_passes():
    report = validate_bundle(make_bundle(), known=KNOWN)
    assert report.unchecked_references == ()


# ── container ────────────────────────────────────────────────────────────────


def test_not_a_zip():
    _rejected(b"PK\x03\x04 not really", "container", match="not a readable ZIP")


def test_non_bytes_input():
    with pytest.raises(BundleRejected):
        validate_bundle("a string")  # type: ignore[arg-type]


def test_archive_too_large(monkeypatch):
    monkeypatch.setattr(validate_mod, "LIMITS", {**validate_mod.LIMITS, "max_compressed_bytes": 100})
    _rejected(make_bundle(), "container", match="archive larger")


@pytest.mark.parametrize("name", [
    "artifacts/tool/x@1.0.0/../../../etc/passwd",
    "/etc/passwd",
    "artifacts\\tool\\evil.py",
    "C:/Windows/evil.py",
    "artifacts//double.py",
    "artifacts/tool/./x.py",
    "artifacts/tool/ctl\x01.py",
])
def test_unsafe_entry_names(name):
    _rejected(make_bundle(extra={name: b"x"}), "container")


def test_symlink_entry():
    data = make_bundle()
    buf = io.BytesIO(data)
    with zipfile.ZipFile(buf, "a") as zf:
        info = zipfile.ZipInfo("artifacts/tool/link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")
    _rejected(buf.getvalue(), "container", match="symlink")


def test_case_insensitive_duplicate():
    _rejected(make_bundle(extra={"artifacts/tool/A.py": b"1", "artifacts/tool/a.py": b"2"}),
              "container", match="duplicate")


def test_entry_outside_artifacts_dir():
    _rejected(make_bundle(extra={"README.md": b"hi"}), "container", match="outside")


def test_compression_bomb():
    _rejected(make_bundle(extra={"artifacts/tool/zeros.bin": b"\0" * (8 * 1024 * 1024)}),
              "container", match="compression ratio")


def test_small_highly_compressible_file_is_not_a_bomb():
    # Below ratio_check_min_bytes the ratio guard does not apply. The file is
    # unlisted, so integrity (a later stage) is what rejects it.
    _rejected(make_bundle(extra={"artifacts/tool/zeros.bin": b"\0" * 10_000}), "integrity")


def test_too_many_entries(monkeypatch):
    monkeypatch.setattr(validate_mod, "LIMITS", {**validate_mod.LIMITS, "max_entries": 3})
    _rejected(make_bundle(), "container", match="entries")


# ── envelope ─────────────────────────────────────────────────────────────────


def test_envelope_missing():
    _rejected(make_bundle(drop=("forge-bundle.json",)), "envelope", match="missing")


def test_envelope_not_json():
    _rejected(make_bundle(raw_envelope=b"{nope"), "envelope", match="not UTF-8 JSON")


@pytest.mark.parametrize("patch,match", [
    (lambda e: e.update(format="something.else"), "format"),
    (lambda e: e.update(format_version=2), "format_version"),
    (lambda e: e.update(format_version=True), "format_version"),
    (lambda e: e.update(surprise=1), "unknown keys"),
    (lambda e: e.pop("artifacts"), "missing keys"),
    (lambda e: e.update(id="../escape"), "envelope.id"),
    (lambda e: e.update(version="2.0"), "envelope.version"),
    (lambda e: e.update(artifacts=[]), "non-empty"),
    (lambda e: e["artifacts"][0].update(kind="theme"), "kind"),
    (lambda e: e["artifacts"][0].update(id="a/b"), ".id"),
    (lambda e: e["artifacts"][0]["files"][0].update(path="artifacts/tool/other@1.0.0/x"), ".path"),
    (lambda e: e["artifacts"][0]["files"][0].update(sha256="ABC"), ".sha256"),
    (lambda e: e["artifacts"][0]["files"][0].update(size=-1), ".size"),
    (lambda e: e["artifacts"][1]["requires"][0].update(kind="nope"), "kind"),
    (lambda e: e["artifacts"].append(dict(e["artifacts"][0])), "duplicate skill"),
])
def test_envelope_schema_defects(patch, match):
    _rejected(make_bundle(envelope_patch=patch), "envelope", match=match)


# ── integrity ────────────────────────────────────────────────────────────────


def test_sha256_mismatch():
    arts = _default_artifacts()
    art, files = arts[1]
    path = art["files"][1]["path"]
    files[path] = files[path].replace(b"len", b"LEN")  # same size, different bytes
    _rejected(make_bundle(artifacts=arts), "integrity", match="sha256 mismatch")


def test_size_mismatch():
    def patch(e):
        e["artifacts"][1]["files"][0]["size"] += 1
    _rejected(make_bundle(envelope_patch=patch), "integrity", match="size")


def test_unlisted_file():
    _rejected(make_bundle(extra={"artifacts/tool/csv.count@0.2.0/backdoor.py": b"import os"}),
              "integrity", match="not declared")


def test_declared_file_missing():
    arts = _default_artifacts()
    path = arts[0][0]["files"][0]["path"]
    _rejected(make_bundle(artifacts=arts, drop=(path,)), "integrity", match="missing")


# ── references ───────────────────────────────────────────────────────────────


def test_intra_bundle_version_mismatch():
    def patch(e):
        e["artifacts"][1]["requires"][0]["version"] = "9.9.9"
    _rejected(make_bundle(envelope_patch=patch), "references", match="bundle carries 1.0.0")


def test_dependency_cycle():
    def patch(e):
        e["artifacts"][0]["requires"] = [{"kind": "plugin", "id": "acme-audit-sink"}]
    # skill -> plugin -> tool -> skill
    _rejected(make_bundle(envelope_patch=patch), "references", match="cycle")


def test_self_dependency_is_a_cycle():
    def patch(e):
        e["artifacts"][0]["requires"] = [{"kind": "skill", "id": "summarize"}]
    _rejected(make_bundle(envelope_patch=patch), "references", match="cycle")


# ── staleness ────────────────────────────────────────────────────────────────


def test_missing_external_requirement():
    _rejected(make_bundle(), "staleness", known={"layer": {}}, match="layer base.rule")


def test_external_version_not_installed():
    def patch(e):
        e["artifacts"][2]["requires"][0]["version"] = "2.0.0"
    _rejected(make_bundle(envelope_patch=patch), "staleness", known=KNOWN, match="base.rule@2.0.0")


# ── secrets ──────────────────────────────────────────────────────────────────


def _with_tool_impl(impl: bytes) -> bytes:
    arts = _default_artifacts()
    art = _artifact("tool", "csv.count", "0.2.0",
                    {"spec.json": b'{"name": "csv.count"}', "impl.py": impl},
                    [{"kind": "skill", "id": "summarize", "version": "1.0.0"}])
    files = {f["path"]: (b'{"name": "csv.count"}' if f["path"].endswith("spec.json") else impl)
             for f in art["files"]}
    arts[1] = (art, files)
    return make_bundle(artifacts=arts)


def test_aws_key_in_tool_impl():
    err = _rejected(_with_tool_impl(b'KEY = "AKIAABCDEFGHIJKLMNOP"\n'), "secrets", match="aws_access_key")
    assert "AKIA" not in err.reason  # names the detector, never the value


def test_private_key_inside_nested_skill_zip():
    arts = _default_artifacts()
    inner = _zip({"summarize/skill.json": b"{}",
                  "summarize/key.pem": b"-----BEGIN RSA PRIVATE KEY-----\nMIIE\n"})
    art = _artifact("skill", "summarize", "1.0.0", {"summarize-1.0.0.zip": inner})
    arts[0] = (art, {art["files"][0]["path"]: inner})
    _rejected(make_bundle(artifacts=arts), "secrets", match="private_key_block")


def test_secret_in_envelope_description():
    _rejected(make_bundle(envelope_patch=lambda e: e.update(description="key ghp_abcdefghijklmnopqrstuvwxyz0123")),
              "secrets", match="github_token")


def test_zip_nested_two_deep_is_rejected():
    arts = _default_artifacts()
    inner = _zip({"deeper.zip": _zip({"x.txt": b"x"})})
    art = _artifact("plugin", "acme-audit-sink", "0.5.0", {"plugin.zip": inner}, [{"kind": "tool", "id": "csv.count"}])
    arts[3] = (art, {art["files"][0]["path"]: inner})
    _rejected(make_bundle(artifacts=arts), "secrets", match="nested deeper")


def test_unsafe_name_inside_nested_zip():
    arts = _default_artifacts()
    inner = _zip({"../../evil.py": b"x"})
    art = _artifact("plugin", "acme-audit-sink", "0.5.0", {"plugin.zip": inner}, [{"kind": "tool", "id": "csv.count"}])
    arts[3] = (art, {art["files"][0]["path"]: inner})
    _rejected(make_bundle(artifacts=arts), "secrets", match="path traversal")


def test_binary_payload_is_reported_unscanned_not_silently_clean():
    arts = _default_artifacts()
    blob = bytes(range(256)) * 4
    art = _artifact("tool", "csv.count", "0.2.0", {"spec.json": b"{}", "icon.png": blob},
                    [{"kind": "skill", "id": "summarize", "version": "1.0.0"}])
    arts[1] = (art, {f["path"]: (b"{}" if f["path"].endswith("spec.json") else blob) for f in art["files"]})
    report = validate_bundle(make_bundle(artifacts=arts))
    assert report.unscanned_files == ("artifacts/tool/csv.count@0.2.0/icon.png",)


# ── remaining defensive branches ─────────────────────────────────────────────

from core.forge_bundle import envelope as envelope_mod  # noqa: E402


@pytest.mark.parametrize("patch,match", [
    (lambda e: e["artifacts"].__setitem__(0, ["not", "an", "object"]), "must be an object"),
    (lambda e: e["artifacts"][0].update(files=[]), "non-empty list"),
    (lambda e: e["artifacts"][1].update(requires="skill"), "must be a list"),
    (lambda e: e.update(created_at=123), "created_at"),
    (lambda e: e.update(description="x" * 2001), "description"),
    (lambda e: e["artifacts"][1]["files"].append(dict(e["artifacts"][1]["files"][0])), "listed twice"),
])
def test_more_envelope_defects(patch, match):
    stage = "integrity" if match == "listed twice" else "envelope"
    _rejected(make_bundle(envelope_patch=patch), stage, match=match)


@pytest.mark.parametrize("limit,value,match", [
    ("max_files_per_artifact", 1, "files"),
    ("max_requires_per_artifact", 0, "requires"),
    ("max_artifacts", 3, "more than 3 artifacts"),
])
def test_envelope_count_limits(monkeypatch, limit, value, match):
    monkeypatch.setattr(envelope_mod, "LIMITS", {**envelope_mod.LIMITS, limit: value})
    _rejected(make_bundle(), "envelope", match=match)


def test_entry_name_too_long():
    _rejected(make_bundle(extra={"artifacts/" + "a" * 600: b"x"}), "container", match="longer than 512")


def test_uncompressed_total_limit(monkeypatch):
    monkeypatch.setattr(validate_mod, "LIMITS", {**validate_mod.LIMITS, "max_uncompressed_bytes": 100})
    _rejected(make_bundle(), "container", match="uncompressed size")


def test_envelope_too_large(monkeypatch):
    monkeypatch.setattr(validate_mod, "LIMITS", {**validate_mod.LIMITS, "max_envelope_bytes": 10})
    _rejected(make_bundle(), "envelope", match="larger than 10")


def test_directory_entries_are_allowed():
    data = make_bundle()
    buf = io.BytesIO(data)
    with zipfile.ZipFile(buf, "a") as zf:
        zf.writestr(zipfile.ZipInfo("artifacts/tool/"), b"")
    validate_bundle(buf.getvalue())


def test_encrypted_entry():
    data = bytearray(make_bundle())
    # Set the "encrypted" bit in the first central-directory header; zipfile
    # reads flag_bits from there.
    cd = data.find(b"PK\x01\x02")
    assert cd > 0
    data[cd + 8] |= 0x1
    _rejected(bytes(data), "container", match="encrypted")


def test_corrupted_payload_fails_crc():
    marker = b"UNIQUE-MARKER-0123456789"
    data = bytearray(_with_tool_impl(b"# " + marker + b"\n"))
    # _with_tool_impl deflates; rebuild stored so the payload bytes are addressable.
    zin = zipfile.ZipFile(io.BytesIO(bytes(data)))
    data = bytearray(_zip({n: zin.read(n) for n in zin.namelist()}, zipfile.ZIP_STORED))
    pos = data.find(marker)
    assert pos > 0
    data[pos] ^= 0xFF  # same size, CRC no longer matches the central directory
    _rejected(bytes(data), "integrity", match="cannot read")


def test_diamond_dependency_is_not_a_cycle():
    def patch(e):
        # plugin -> tool -> skill and plugin -> skill: skill is reached twice.
        e["artifacts"][3]["requires"].append({"kind": "skill", "id": "summarize"})
    validate_bundle(make_bundle(envelope_patch=patch))


def test_secret_scan_failure_is_treated_as_sensitive(monkeypatch):
    from core.pii.sensitive import PIIDetectionFailedClosed

    def boom(_text):
        raise PIIDetectionFailedClosed("regex engine error")
    monkeypatch.setattr(validate_mod, "detect_sensitive_types", boom)
    _rejected(make_bundle(), "secrets", match="scan failed")


def test_declared_zip_that_is_not_a_zip():
    arts = _default_artifacts()
    art = _artifact("plugin", "acme-audit-sink", "0.5.0", {"plugin.zip": b"not a zip at all"},
                    [{"kind": "tool", "id": "csv.count"}])
    arts[3] = (art, {art["files"][0]["path"]: b"not a zip at all"})
    _rejected(make_bundle(artifacts=arts), "secrets", match="not a readable ZIP")
