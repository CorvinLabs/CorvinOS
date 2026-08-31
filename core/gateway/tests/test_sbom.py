"""Per-subtask E2E — ADR-0021 Phase 31.1 (SBOM-Generation).

Covers:
  * requirements.txt parser (versions + hashes + continuation lines + comments)
  * package-lock.json parser (lockfileVersion 2/3, integrity hashes)
  * NOTICE file parser (vendored hints)
  * plugin source-tree sha256 (excludes .venv, __pycache__, .pyc, .git)
  * build_sbom CycloneDX 1.5 shape (bomFormat, specVersion, components)
  * write_sbom + load_sbom round-trip + mode 0o600
  * sbom_components_summary (python_count, npm_count, vendored_count)
  * Audit-Allow-List for the 6 supply_chain.* events the module emits
  * EVENT_SEVERITY registry contains all 10 ADR-0021 event types
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "core" / "gateway"))
sys.path.insert(0, str(REPO / "operator" / "forge"))

from corvin_gateway import sbom as _sbom

PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    suffix = f" — {detail}" if detail else ""
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{suffix}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


# ---------------------------------------------------------------------------
# Section 1 — requirements.txt parser
# ---------------------------------------------------------------------------


def section_requirements_parser() -> None:
    print("\n[1/8] requirements.txt parser")
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "requirements.txt"

        # Simple pinned dep
        p.write_text("fastapi==0.136.0\n")
        deps = _sbom.parse_requirements_txt(p)
        t("simple pinned dep parsed",
          len(deps) == 1 and deps[0]["name"] == "fastapi"
          and deps[0]["version"] == "0.136.0",
          detail=str(deps))

        # With hashes + comments + blank lines
        p.write_text("""\
# requirements file
fastapi==0.136.0 --hash=sha256:abc123 \\
    --hash=sha256:def456
pydantic==2.5.3 --hash=sha256:cafebabe

# end
""")
        deps = _sbom.parse_requirements_txt(p)
        t("two deps via continuation",
          len(deps) == 2,
          detail=f"got {len(deps)}")
        if len(deps) == 2:
            fa = deps[0]
            t("fastapi has 2 hashes",
              len(fa["hashes"]) == 2,
              detail=f"got {len(fa['hashes'])}")

        # Unpinned (>=, ~=) is skipped
        p.write_text("requests>=2.28\nfastapi==0.136.0\n")
        deps = _sbom.parse_requirements_txt(p)
        t("unpinned dep skipped",
          len(deps) == 1 and deps[0]["name"] == "fastapi")

        # Missing file → empty list
        ne = Path(tmp) / "nonexistent.txt"
        t("missing file → empty list",
          _sbom.parse_requirements_txt(ne) == [])


# ---------------------------------------------------------------------------
# Section 2 — package-lock.json parser
# ---------------------------------------------------------------------------


def section_package_lock_parser() -> None:
    print("\n[2/8] package-lock.json parser")
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "package-lock.json"
        p.write_text(json.dumps({
            "name": "test-pkg",
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "test-pkg", "version": "1.0.0"},
                "node_modules/discord.js": {
                    "version": "14.18.0",
                    "integrity": "sha512-abc123def==",
                },
                "node_modules/@baileys/socket": {
                    "version": "6.4.0",
                    "integrity": "sha512-ghi789jkl==",
                },
            },
        }))
        deps = _sbom.parse_package_lock(p)
        t("two npm deps parsed (root skipped)",
          len(deps) == 2,
          detail=f"got {len(deps)}")
        names = {d["name"] for d in deps}
        t("discord.js present", "discord.js" in names)
        t("scoped pkg name preserved", "@baileys/socket" in names)
        for d in deps:
            if d["name"] == "discord.js":
                t("integrity hash extracted",
                  len(d["hashes"]) == 1
                  and d["hashes"][0]["alg"] == "SHA512")

        # Empty / malformed → empty list
        p.write_text("not json {")
        try:
            _sbom.parse_package_lock(p)
            t("malformed JSON raises", False)
        except _sbom.SBOMError:
            t("malformed JSON raises", True)


# ---------------------------------------------------------------------------
# Section 3 — NOTICE parser
# ---------------------------------------------------------------------------


def section_notice_parser() -> None:
    print("\n[3/8] NOTICE parser")
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "NOTICE"
        p.write_text("""\
Corvin NOTICE
# comments are ignored

Third-party components:
   pillow 10.0.0 (PIL fork)
   typing-extensions
""")
        items = _sbom.parse_notice_file(p)
        # "Corvin NOTICE", "pillow 10.0.0 (PIL fork)", "typing-extensions"
        # "Third-party components:" is skipped (ends with colon)
        t("non-comment non-heading lines captured",
          len(items) >= 2,
          detail=str(items))
        t("heading-with-colon skipped",
          "Third-party components:" not in items)


# ---------------------------------------------------------------------------
# Section 4 — plugin source sha256
# ---------------------------------------------------------------------------


def section_source_sha() -> None:
    print("\n[4/8] plugin source sha256")
    with tempfile.TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "fake-plugin"
        plugin.mkdir()
        (plugin / "main.py").write_text("print('hello')")
        (plugin / "lib").mkdir()
        (plugin / "lib" / "helper.py").write_text("x = 1")
        # Excluded files
        (plugin / "__pycache__").mkdir()
        (plugin / "__pycache__" / "main.cpython-312.pyc").write_bytes(b"\x00")
        (plugin / ".venv").mkdir()
        (plugin / ".venv" / "huge.txt").write_text("x" * 10000)

        sha1 = _sbom.plugin_source_sha256(plugin)
        t("sha256 is 64 hex chars",
          len(sha1) == 64 and all(c in "0123456789abcdef" for c in sha1))

        # Repeatable — same content, same sha
        sha2 = _sbom.plugin_source_sha256(plugin)
        t("sha256 is deterministic", sha1 == sha2)

        # Modify a file → different sha
        (plugin / "main.py").write_text("print('hello world')")
        sha3 = _sbom.plugin_source_sha256(plugin)
        t("sha256 changes on content change", sha1 != sha3)

        # Add a .pyc / .venv file → SAME sha (excluded)
        (plugin / "__pycache__" / "other.pyc").write_bytes(b"\xff")
        sha4 = _sbom.plugin_source_sha256(plugin)
        t("excluded files don't affect sha",
          sha3 == sha4,
          detail=f"{sha3[:12]} vs {sha4[:12]}")


# ---------------------------------------------------------------------------
# Section 5 — build_sbom shape (CycloneDX 1.5)
# ---------------------------------------------------------------------------


def section_build_shape() -> None:
    print("\n[5/8] build_sbom CycloneDX 1.5 shape")
    with tempfile.TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plg"
        plugin.mkdir()
        (plugin / "main.py").write_text("x = 1\n")
        (plugin / "requirements.txt").write_text(
            "fastapi==0.136.0 --hash=sha256:abc123\n")
        (plugin / "package-lock.json").write_text(json.dumps({
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "x", "version": "1"},
                "node_modules/discord.js": {
                    "version": "14.18.0",
                    "integrity": "sha512-xyz==",
                },
            },
        }))
        (plugin / "NOTICE").write_text("vendored: pillow\n")

        bom = _sbom.build_sbom(plugin, plugin_name="plg",
                                plugin_version="0.1.0")
        t("bomFormat = CycloneDX", bom["bomFormat"] == "CycloneDX")
        t("specVersion = 1.5", bom["specVersion"] == "1.5")
        t("serialNumber is urn:uuid",
          bom["serialNumber"].startswith("urn:uuid:"))
        t("metadata.component.name set",
          bom["metadata"]["component"]["name"] == "plg")
        t("metadata.tools[].name correct",
          bom["metadata"]["tools"][0]["name"] == "corvin_gateway_sbom")

        # Components: 1 python + 1 npm + 1 vendored
        comps = bom["components"]
        names = {c["name"] for c in comps}
        t("fastapi component present", "fastapi" in names)
        t("discord.js component present", "discord.js" in names)
        purls = [c.get("purl", "") for c in comps]
        t("python purl correct",
          any(p == "pkg:pypi/fastapi@0.136.0" for p in purls))
        t("npm purl correct",
          any(p.startswith("pkg:npm/discord.js@") for p in purls))

        # source-tree sha recorded
        props = bom["metadata"]["properties"]
        t("source_sha256 property recorded",
          any(p["name"] == "corvin:plugin_source_sha256" for p in props))

        # Validation errors
        try:
            _sbom.build_sbom(plugin, plugin_name="", plugin_version="1")
            t("empty plugin_name rejected", False)
        except _sbom.SBOMError:
            t("empty plugin_name rejected", True)


# ---------------------------------------------------------------------------
# Section 6 — write_sbom + load_sbom round-trip
# ---------------------------------------------------------------------------


def section_round_trip() -> None:
    print("\n[6/8] write_sbom + load_sbom round-trip")
    with tempfile.TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plg"
        plugin.mkdir()
        (plugin / "main.py").write_text("x = 1\n")
        (plugin / "requirements.txt").write_text("fastapi==0.136.0\n")
        out = Path(tmp) / "sbom.cdx.json"
        _sbom.write_sbom(plugin, out, plugin_name="plg", plugin_version="0.1")
        t("output file created", out.exists())
        mode = out.stat().st_mode & 0o777
        t("output mode 0o600",
          mode == 0o600, detail=oct(mode))

        loaded = _sbom.load_sbom(out)
        t("loaded matches schema",
          loaded["bomFormat"] == "CycloneDX"
          and loaded["specVersion"] == "1.5")

        # Summary
        py, npm, vend = _sbom.sbom_components_summary(loaded)
        t("summary returns (python, npm, vendored)",
          py == 1 and npm == 0 and vend == 0,
          detail=f"py={py}, npm={npm}, vend={vend}")

        # Bad SBOM
        bad = Path(tmp) / "bad.cdx.json"
        bad.write_text(json.dumps({"bomFormat": "OtherFormat"}))
        try:
            _sbom.load_sbom(bad)
            t("non-CycloneDX rejected", False)
        except _sbom.SBOMError:
            t("non-CycloneDX rejected", True)


# ---------------------------------------------------------------------------
# Section 7 — Audit emission allow-list
# ---------------------------------------------------------------------------


def section_audit_allow_list() -> None:
    print("\n[7/8] Audit emission + forbidden-field gate")
    # Forbidden-field rejection
    try:
        _sbom._validate_audit_details(
            "supply_chain.sbom_verified",
            {"bundle_sha256": "abc", "dep_count": 3,
             "cdx_version": "1.5", "exploit_text": "leaked!"},
        )
        t("forbidden field rejected", False)
    except _sbom.SBOMAuditFieldNotAllowed:
        t("forbidden field rejected", True)

    # Off-allowlist field rejection
    try:
        _sbom._validate_audit_details(
            "supply_chain.sbom_verified",
            {"bundle_sha256": "abc", "uninvited": "x"},
        )
        t("off-allowlist field rejected", False)
    except _sbom.SBOMAuditFieldNotAllowed:
        t("off-allowlist field rejected", True)

    # Unknown event type rejected
    try:
        _sbom._validate_audit_details(
            "supply_chain.bogus", {"bundle_sha256": "abc"},
        )
        t("unknown event_type rejected", False)
    except _sbom.SBOMAuditFieldNotAllowed:
        t("unknown event_type rejected", True)

    # Happy-path emit (lands in chain)
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CORVIN_HOME"] = tmp
        try:
            _sbom.emit_sbom_event("supply_chain.sbom_verified", {
                "bundle_sha256": "abc123",
                "dep_count": 5,
                "cdx_version": "1.5",
            })
            audit = (Path(tmp) / "tenants" / "_default" / "global" /
                     "forge" / "audit.jsonl")
            t("audit chain file created", audit.exists())
            if audit.exists():
                lines = audit.read_text().splitlines()
                rec = json.loads(lines[0])
                t("event_type recorded",
                  rec["event_type"] == "supply_chain.sbom_verified")
        finally:
            os.environ.pop("CORVIN_HOME", None)


# ---------------------------------------------------------------------------
# Section 8 — EVENT_SEVERITY registry
# ---------------------------------------------------------------------------


def section_event_registry() -> None:
    print("\n[8/8] EVENT_SEVERITY-Registry")
    from forge import security_events as se
    expected = {
        "supply_chain.sbom_verified":            "INFO",
        "supply_chain.sbom_missing":             "WARNING",
        "supply_chain.dep_hashes_updated":       "INFO",
        "supply_chain.dep_hash_mismatch":        "WARNING",
        "supply_chain.cve_detected":             "WARNING",
        "supply_chain.capability_drift":         "WARNING",
        "supply_chain.signature_rekor_verified": "INFO",
        "supply_chain.signature_chain_break":    "WARNING",
        "supply_chain.frozen_baseline_breach_attempted": "WARNING",
        "supply_chain.cve_check_skipped":        "WARNING",
    }
    for ev, sev in expected.items():
        t(f"{ev} = {sev}",
          se.EVENT_SEVERITY.get(ev) == sev,
          detail=se.EVENT_SEVERITY.get(ev, "<missing>"))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 60)
    print("test_sbom.py — ADR-0021 Phase 31.1 (SBOM-Generation)")
    print("=" * 60)
    section_requirements_parser()
    section_package_lock_parser()
    section_notice_parser()
    section_source_sha()
    section_build_shape()
    section_round_trip()
    section_audit_allow_list()
    section_event_registry()
    print()
    print(f"PASS={PASS}  FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
