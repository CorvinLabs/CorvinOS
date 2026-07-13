"""Phase E E2E: bridge events land in the hash-chained audit log.

Three scenarios:

  1. A fictional Discord whitelist denial + PIN failure + persona-routed
     sequence is written through audit_event(). The chain verifies.
     `voice-audit verify` exits 0 and prints "audit OK".

  2. An attacker mutates one entry's tool field but leaves the hash
     untouched. `voice-audit verify` exits 1 and pinpoints the line.

  3. Forge plugin removed at runtime → audit_event becomes a no-op
     instead of crashing the bridge.

The bridge layer never has to call write_event directly — it goes
through ``bridges/shared/audit.audit_event(...)``, which is the public
contract that the adapter / daemons rely on.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "operator" / "bridges" / "shared"))

# The voice audit module — this is the public surface the bridge code uses.
import audit as _voice_audit  # noqa: E402


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


# ---------- (1) — happy path through the bridge surface ------------------

def test_bridge_events_chain_verifies_clean():
    print("\n[bridge events → hash-chain → voice-audit verify exits 0]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        audit_path = td / "audit.jsonl"
        env = dict(os.environ); env["VOICE_AUDIT_PATH"] = str(audit_path)
        os.environ["VOICE_AUDIT_PATH"] = str(audit_path)
        try:
            # A realistic mini-sequence:
            _voice_audit.audit_event(
                "bridge.message_received",
                channel="discord", chat_key="cid:42",
                user="123456789", persona="forge",
                details={"text_len": 42},
            )
            _voice_audit.audit_event(
                "bridge.whitelist_deny",
                channel="discord", chat_key="cid:42",
                user="hostile_id",
                details={"reason": "not in whitelist"},
            )
            _voice_audit.audit_event(
                "bridge.persona_routed",
                channel="discord", chat_key="cid:42",
                user="123456789", persona="forge",
                details={"confidence": 0.91, "via": "embedding"},
            )

            t("audit file exists", audit_path.exists())
            lines = audit_path.read_text().splitlines()
            t("3 events written", len(lines) == 3)
            recs = [json.loads(l) for l in lines]
            types = [r["event_type"] for r in recs]
            t("event_types match",
              types == ["bridge.message_received",
                        "bridge.whitelist_deny",
                        "bridge.persona_routed"])
            t("each record has hash + prev_hash",
              all("hash" in r and "prev_hash" in r for r in recs))
            t("chain is properly linked (prev_hash chains)",
              recs[0]["prev_hash"] == ""
              and recs[1]["prev_hash"] == recs[0]["hash"]
              and recs[2]["prev_hash"] == recs[1]["hash"])

            # Verify via Python API
            ok, problems = _voice_audit.verify_audit(audit_path)
            t("verify_audit() ok", ok and not problems)

            # Verify via the CLI
            cli_path = REPO_ROOT / "operator" / "voice" / "scripts" / "voice_audit.py"
            proc = subprocess.run(
                [sys.executable, str(cli_path), "--path",
                 str(audit_path), "verify"],
                capture_output=True, text=True,
            )
            t("CLI rc=0", proc.returncode == 0)
            t("CLI says 'audit OK'", "audit OK" in proc.stdout)

            # tail should print 3 lines
            proc = subprocess.run(
                [sys.executable, str(cli_path), "--path",
                 str(audit_path), "tail", "--limit", "5"],
                capture_output=True, text=True,
            )
            t("tail rc=0", proc.returncode == 0)
            t("tail mentions all three event types",
              all(et in proc.stdout for et in
                  ("bridge.message_received",
                   "bridge.whitelist_deny",
                   "bridge.persona_routed")))
        finally:
            os.environ.pop("VOICE_AUDIT_PATH", None)


# ---------- (2) — tampering is caught, line number reported --------------

def test_tampered_audit_fails_verify():
    print("\n[mutate one entry's tool field → CLI exits 1, names the line]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        audit_path = td / "audit.jsonl"
        os.environ["VOICE_AUDIT_PATH"] = str(audit_path)
        try:
            for i in range(4):
                _voice_audit.audit_event(
                    "bridge.message_received",
                    channel="discord", chat_key=f"cid:{i}",
                    user="u", persona="coder",
                )
            # Tamper line 2: change persona, leave hash alone
            lines = audit_path.read_text().splitlines()
            rec = json.loads(lines[1])
            rec["details"]["persona"] = "evil_persona"
            lines[1] = json.dumps(rec)
            audit_path.write_text("\n".join(lines) + "\n")

            cli = REPO_ROOT / "operator" / "voice" / "scripts" / "voice_audit.py"
            proc = subprocess.run(
                [sys.executable, str(cli), "--path",
                 str(audit_path), "verify"],
                capture_output=True, text=True,
            )
            t("CLI rc=1", proc.returncode == 1)
            t("stderr says INTEGRITY VIOLATION",
              "INTEGRITY VIOLATION" in proc.stderr)
            t("stderr names line 2",
              "line 2" in proc.stderr)
            t("stderr names 'tampered' issue",
              "tampered" in proc.stderr)
        finally:
            os.environ.pop("VOICE_AUDIT_PATH", None)


# ---------- (3) — graceful no-op when forge is absent --------------------

def test_audit_silent_noop_when_forge_missing(monkeypatch=None):
    print("\n[forge not importable → audit_event is a silent no-op, verify=ok]")
    # Simulate by reloading audit.py with _se forced to None
    import importlib
    saved = sys.modules.get("audit")
    try:
        # Reload with forge import shim broken
        sys.modules.pop("audit", None)
        # We can't easily prevent the import path — instead, after
        # import, monkey-patch _se to None and validate the API contract.
        import audit as audit_mod  # noqa: F401
        importlib.reload(audit_mod)
        old_se = audit_mod._se
        audit_mod._se = None
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            os.environ["VOICE_AUDIT_PATH"] = str(td / "audit.jsonl")
            try:
                # No exception, no file created
                audit_mod.audit_event(
                    "bridge.login",
                    channel="discord", user="x",
                )
                t("no audit file written when forge missing",
                  not (td / "audit.jsonl").exists())
                ok, problems = audit_mod.verify_audit()
                t("verify returns (True, []) when forge missing",
                  ok and problems == [])
            finally:
                os.environ.pop("VOICE_AUDIT_PATH", None)
                audit_mod._se = old_se
    finally:
        if saved is not None:
            sys.modules["audit"] = saved


# ---------- (4) — broken forge import is indistinguishable from absence ---

def _load_audit_copy(tmp_root: Path, *, forge_present_but_broken: bool, name: str):
    """Load a fresh copy of audit.py whose ``__file__`` lives under
    ``tmp_root``, so its own ``parents[2]`` forge-root resolution points at
    our sandbox instead of the real ``operator/forge/``.

    When ``forge_present_but_broken`` is True, a real (non-empty) ``forge``
    package directory exists in the sandbox, but its ``security_events``
    submodule raises a non-ImportError exception on import — simulating a
    packaging regression (syntax error, broken transitive dependency, etc.),
    as opposed to forge being genuinely absent (no such directory at all).
    """
    import importlib.util

    audit_src = (Path(__file__).resolve().parent / "audit.py").read_text()
    shared_dir = tmp_root / "operator" / "bridges" / "shared"
    shared_dir.mkdir(parents=True, exist_ok=True)
    audit_copy = shared_dir / "audit.py"
    audit_copy.write_text(audit_src)

    if forge_present_but_broken:
        forge_pkg = tmp_root / "operator" / "forge" / "forge"
        forge_pkg.mkdir(parents=True, exist_ok=True)
        (forge_pkg / "__init__.py").write_text("")
        (forge_pkg / "security_events.py").write_text(
            "raise RuntimeError("
            "'simulated packaging regression: broken transitive dependency')\n"
        )
    # else: leave tmp_root/operator/forge entirely absent -> genuine absence

    saved_forge = sys.modules.pop("forge", None)
    saved_forge_se = sys.modules.pop("forge.security_events", None)
    saved_path = list(sys.path)
    saved_env = os.environ.get("FORGE_ROOT")
    # FORGE_ROOT short-circuits _forge_workspace_root() so the sandboxed
    # copy never needs a sibling paths.py module to compute DEFAULT_AUDIT_PATH.
    os.environ["FORGE_ROOT"] = str(tmp_root / "unused_forge_root")
    try:
        spec = importlib.util.spec_from_file_location(name, audit_copy)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod
    finally:
        sys.path[:] = saved_path
        if saved_env is None:
            os.environ.pop("FORGE_ROOT", None)
        else:
            os.environ["FORGE_ROOT"] = saved_env
        sys.modules.pop("forge", None)
        sys.modules.pop("forge.security_events", None)
        if saved_forge is not None:
            sys.modules["forge"] = saved_forge
        if saved_forge_se is not None:
            sys.modules["forge.security_events"] = saved_forge_se


def test_forge_broken_import_indistinguishable_from_absent():
    print("\n[forge present-but-broken import collapses to the SAME "
          "'all clear' as forge genuinely absent -- no CRITICAL differentiation]")
    import logging

    class _Cap(logging.Handler):
        def __init__(self):
            super().__init__()
            self.records: list = []

        def emit(self, record):
            self.records.append(record)

    logger = logging.getLogger("corvin.audit")

    cap_absent = _Cap()
    with tempfile.TemporaryDirectory() as td_absent:
        logger.addHandler(cap_absent)
        try:
            mod_absent = _load_audit_copy(
                Path(td_absent), forge_present_but_broken=False,
                name="audit_sandbox_absent",
            )
        finally:
            logger.removeHandler(cap_absent)

    cap_broken = _Cap()
    with tempfile.TemporaryDirectory() as td_broken:
        logger.addHandler(cap_broken)
        try:
            mod_broken = _load_audit_copy(
                Path(td_broken), forge_present_but_broken=True,
                name="audit_sandbox_broken",
            )
        finally:
            logger.removeHandler(cap_broken)

    t("genuinely-absent forge -> _se is None (expected, legitimate standalone mode)",
      mod_absent._se is None)
    t("present-but-broken forge -> _se is None (SAME outward state as absence)",
      mod_broken._se is None)

    # The bug: a genuine packaging regression (forge dir present, import
    # raises RuntimeError) is NOT flagged any differently than the
    # legitimate "forge was never installed" case -- no CRITICAL/distinct
    # signal exists anywhere to tell the two apart.
    t("genuinely-absent forge logs NO warning at all (silent by design)",
      len(cap_absent.records) == 0)
    t("present-but-broken forge logs the SAME reassuring "
      "'not an error in standalone mode' message -- a real regression is "
      "mislabeled as intentional standalone behaviour",
      len(cap_broken.records) == 1
      and "not an error in standalone mode" in cap_broken.records[0].getMessage())

    # Downstream consumers collapse both causes to an identical "all clear".
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "audit.jsonl"
        result_absent = mod_absent.verify_audit(p)
        result_broken = mod_broken.verify_audit(p)
        t("verify_audit() gives an IDENTICAL 'all clear' result for both "
          "causes (genuine absence vs. broken import) -- no differentiation",
          result_absent == result_broken == (True, []))

        health_absent = mod_absent.audit_health_check(p)
        health_broken = mod_broken.audit_health_check(p)
        t("audit_health_check() also gives an IDENTICAL clean result for "
          "both causes -- a broken forge install never surfaces as CRITICAL",
          health_absent == health_broken == (True, 0))


def main() -> int:
    test_bridge_events_chain_verifies_clean()
    test_tampered_audit_fails_verify()
    test_audit_silent_noop_when_forge_missing()
    test_forge_broken_import_indistinguishable_from_absent()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
