"""Layer 16 v3 — Secret-Injection (capability-style).

Per-subtask E2E for the vault → runner → bwrap path. Spawns real bwrap
where available, real tool subprocess, real audit chain. Mock-free
except for the vault file itself (which IS the vault).

Run as: python3 operator/forge/tests/test_secret_injection.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "forge"))
# ADR-0153 M3 — the additive instance_sig audit decoration lives in
# security_events.py and imports ``instance_identity`` from the shared
# package. Under the bare ``cd operator/forge && python3 tests/...``
# invocation that dir is NOT on sys.path, so the signing was skipped
# silently (logged "instance_sig not added — ... not importable"). Put
# the shared dir on the path here so the instance_sig path is exercised
# for real against a self-provisioned ephemeral key (CORVIN_HOME is
# sandboxed to a tmp dir below, so the Ed25519 key is created there).
sys.path.insert(0, str(REPO / "operator" / "bridges" / "shared"))

from forge import secret_vault as sv  # noqa: E402
from forge.policy import Policy  # noqa: E402
from forge.registry import Registry  # noqa: E402
from forge.runner import (  # noqa: E402
    SecretACLDenied,
    SecretMissing,
    run_tool,
)
from forge.sandbox import have_bwrap  # noqa: E402

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


# A tool that emits the value of a declared env-var as a hash, plus a
# best-effort env-leak attempt to verify stdout-redaction. The hash is
# what the test asserts against (we know what value we put in the vault,
# so we know what hash to expect). The leak attempt should be redacted.
SECRET_TOOL_IMPL = '''#!/usr/bin/env python3
import hashlib, json, os, sys
p = json.loads(sys.stdin.read())
key = p.get("env_key", "TEST_API_KEY")
val = os.environ.get(key, "")
# This is the legitimate use: hash the secret value for fingerprinting.
fp = hashlib.sha256(val.encode()).hexdigest()[:16] if val else ""
# This is the accidental-leak we want redacted. A buggy tool that
# prints env for debugging would produce something like this:
leak_line = f"DEBUG: {key}={val}"
print(json.dumps({
    "ok": True,
    "fingerprint": fp,
    "value_present": bool(val),
    "value_length": len(val),
    "leak_line": leak_line,
}))
'''

SECRET_TOOL_SCHEMA = {
    "type": "object",
    "properties": {"env_key": {"type": "string"}},
}


def _expected_fingerprint(value: str) -> str:
    import hashlib
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _read_audit(audit_path: Path) -> list[dict]:
    if not audit_path.exists():
        return []
    out = []
    for line in audit_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def case_pure_unit_validation() -> None:
    print("\n[case_pure_unit_validation: secret_vault helpers]")
    t("is_valid_key('OPENAI_API_KEY')", sv.is_valid_key("OPENAI_API_KEY"))
    t("is_valid_key('_PRIVATE')", sv.is_valid_key("_PRIVATE"))
    t("not is_valid_key('lower')", not sv.is_valid_key("lower"))
    t("not is_valid_key('1LEAD_DIGIT')", not sv.is_valid_key("1LEAD_DIGIT"))
    t("not is_valid_key('HAS-DASH')", not sv.is_valid_key("HAS-DASH"))
    t("not is_valid_key('HAS.DOT')", not sv.is_valid_key("HAS.DOT"))
    t("not is_valid_key('')", not sv.is_valid_key(""))
    t("not is_valid_key('A' * 100)", not sv.is_valid_key("A" * 100))

    t("validate_secret_refs([]) == []", sv.validate_secret_refs([]) == [])
    t("validate_secret_refs(None) == []", sv.validate_secret_refs(None) == [])
    out = sv.validate_secret_refs(["A_KEY", "Z_KEY", "A_KEY"])
    t("validate dedupes + sorts", out == ["A_KEY", "Z_KEY"], detail=f"got {out}")

    raised = False
    try:
        sv.validate_secret_refs(["bad-name"])
    except sv.SecretRefError:
        raised = True
    t("validate raises on bad name", raised)

    raised = False
    try:
        sv.validate_secret_refs(["K%d" % i for i in range(20)])
    except sv.SecretRefError:
        raised = True
    t("validate caps at 16 entries", raised)


def case_redact_helper() -> None:
    print("\n[case_redact_helper]")
    t("redact_values short value skipped",
      sv.redact_values("hello sk", ["sk"]) == "hello sk")
    t("redact_values long value replaced",
      sv.redact_values("hello sk-abc12345xyz world",
                       ["sk-abc12345xyz"])
      == "hello <redacted> world")
    # Longest-first matters for nested substrings: short value is contained
    # in long value. Both must clear REDACT_MIN_VALUE_LEN (8 chars).
    short = "AAAAAAAA"           # 8 chars (just at cap)
    long_ = "AAAAAAAA-XYZ-TAIL"  # contains short
    out = sv.redact_values(long_, [short, long_])
    t("redact_values longest-first wins",
      out == "<redacted>",
      detail=f"got {out!r} (expected single <redacted>, not nested)")
    t("redact_values empty values returns input unchanged",
      sv.redact_values("anything", []) == "anything")


def case_vault_load() -> None:
    print("\n[case_vault_load: file mode + JSON shape]")
    with tempfile.TemporaryDirectory() as td:
        vault_path = Path(td) / "vault.json"

        # Missing file → empty dict, no error.
        t("missing vault returns {}",
          sv.load_vault(vault_path) == {})

        # Wrong mode (group readable) is rejected.
        vault_path.write_text(json.dumps({"K": "v"}))
        os.chmod(vault_path, 0o644)
        raised = False
        try:
            sv.load_vault(vault_path)
        except sv.VaultError as exc:
            raised = "too permissive" in str(exc)
        t("0644 vault rejected with mode error", raised)

        # Correct mode loads.
        os.chmod(vault_path, 0o600)
        loaded = sv.load_vault(vault_path)
        t("0600 vault loads", loaded == {"K": "v"})

        # Malformed JSON.
        vault_path.write_text("{not json")
        os.chmod(vault_path, 0o600)
        raised = False
        try:
            sv.load_vault(vault_path)
        except sv.VaultError:
            raised = True
        t("malformed JSON raises VaultError", raised)

        # Non-string value.
        vault_path.write_text(json.dumps({"K": 42}))
        os.chmod(vault_path, 0o600)
        raised = False
        try:
            sv.load_vault(vault_path)
        except sv.VaultError as exc:
            raised = "must be a string" in str(exc)
        t("non-string value raises VaultError", raised)

        # Invalid key shape.
        vault_path.write_text(json.dumps({"bad-key": "v"}))
        os.chmod(vault_path, 0o600)
        raised = False
        try:
            sv.load_vault(vault_path)
        except sv.VaultError:
            raised = True
        t("invalid key shape raises VaultError", raised)


def case_registry_validation() -> None:
    print("\n[case_registry_validation: meta.secrets at create-time]")
    with tempfile.TemporaryDirectory() as td:
        os.environ["CORVIN_HOME"] = td
        try:
            reg = Registry(Path(td))
            # Valid case.
            spec = reg.create(
                "test.ok", "valid", SECRET_TOOL_SCHEMA, SECRET_TOOL_IMPL,
                meta={"secrets": ["TEST_API_KEY"]},
            )
            t("valid meta.secrets accepted",
              spec.meta.get("secrets") == ["TEST_API_KEY"])

            # Invalid name.
            raised = False
            try:
                reg.create(
                    "test.bad", "x", SECRET_TOOL_SCHEMA, SECRET_TOOL_IMPL,
                    meta={"secrets": ["lowercase"]},
                )
            except ValueError as exc:
                raised = "meta.secrets invalid" in str(exc)
            t("invalid secret name rejected at create", raised)

            # Non-list type.
            raised = False
            try:
                reg.create(
                    "test.bad2", "x", SECRET_TOOL_SCHEMA, SECRET_TOOL_IMPL,
                    meta={"secrets": "TEST_API_KEY"},  # not a list
                )
            except ValueError:
                raised = True
            t("non-list secrets rejected", raised)
        finally:
            os.environ.pop("CORVIN_HOME", None)


def case_policy_acl() -> None:
    print("\n[case_policy_acl: persona allow-list resolution]")
    p = Policy(persona_secret_allow={
        "research": ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"],
        "browser":  [],
    })
    t("research has 2 keys",
      p.secrets_for_persona("research") == [
          "OPENAI_API_KEY", "ANTHROPIC_API_KEY"
      ])
    t("browser has 0 keys", p.secrets_for_persona("browser") == [])
    t("unknown persona has 0 keys", p.secrets_for_persona("nope") == [])
    t("empty persona has 0 keys", p.secrets_for_persona("") == [])

    ok, denied = p.secret_check("research", ["OPENAI_API_KEY"])
    t("research allowed for OPENAI_API_KEY", ok and not denied)

    ok, denied = p.secret_check("research", ["OPENAI_API_KEY", "OTHER"])
    t("research denied for OTHER",
      not ok and denied == ["OTHER"])

    ok, denied = p.secret_check("browser", ["OPENAI_API_KEY"])
    t("browser denied for everything",
      not ok and denied == ["OPENAI_API_KEY"])

    ok, _ = p.secret_check("research", [])
    t("empty request always allowed", ok)


def case_policy_load_merge() -> None:
    print("\n[case_policy_load_merge: workspace policy.json merging]")
    with tempfile.TemporaryDirectory() as td:
        wp = Path(td) / "policy.json"
        wp.write_text(json.dumps({
            "persona_secret_allow": {
                "research": ["OPENAI_API_KEY"],
            }
        }))
        loaded = Policy.load(Path(td))
        t("workspace persona_secret_allow loaded",
          loaded.persona_secret_allow.get("research") == ["OPENAI_API_KEY"])

        # Bad shape (not a dict) is silently dropped.
        wp.write_text(json.dumps({"persona_secret_allow": "garbage"}))
        loaded2 = Policy.load(Path(td))
        t("malformed persona_secret_allow falls back to empty",
          loaded2.persona_secret_allow == {})


def case_runner_e2e() -> None:
    print("\n[case_runner_e2e: full vault → bwrap → tool → assertions]")
    if not have_bwrap():
        print("  SKIP: bwrap not on PATH — secret-injection sandbox needs "
              "real namespace; bwrap-less rlimits-only fallback would "
              "still work but the audit-chain assertions fire identically "
              "either way, so we just lose the namespace-isolation guarantee.")
        return

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        # Sandbox CORVIN_HOME so the audit chain lands in td.
        saved_home = os.environ.get("CORVIN_HOME")
        saved_persona = os.environ.get("FORGE_PERSONA")
        saved_vault = os.environ.get("CORVIN_SECRET_VAULT")
        os.environ["CORVIN_HOME"] = str(td_path)

        # Build a vault file in td (out-of-tree, mode 0600).
        vault_file = td_path / "vault.json"
        secret_value = "sk-supersecret-DEADBEEF-cafe-1234567890"
        vault_file.write_text(json.dumps({
            "TEST_API_KEY":      secret_value,
            "UNUSED_KEY":        "should-not-leak",
        }))
        os.chmod(vault_file, 0o600)
        os.environ["CORVIN_SECRET_VAULT"] = str(vault_file)

        # Workspace policy: research allowed, coder denied, plus a wildcard
        # persona "test_persona" gets one key.
        wp = td_path / "policy.json"
        wp.write_text(json.dumps({
            "persona_secret_allow": {
                "test_persona":   ["TEST_API_KEY"],
                "research":       ["TEST_API_KEY"],
                "browser":        [],
            }
        }))
        policy = Policy.load(td_path)

        try:
            reg = Registry(td_path)
            reg.create(
                "test.secret_use",
                "echoes hash of declared env secret",
                SECRET_TOOL_SCHEMA, SECRET_TOOL_IMPL,
                meta={"secrets": ["TEST_API_KEY"]},
            )

            # ---- happy path: allowed persona, key in vault -----------
            os.environ["FORGE_PERSONA"] = "test_persona"
            r = run_tool(reg, "test.secret_use", {"env_key": "TEST_API_KEY"},
                         permission_mode="yes", policy=policy)
            t("happy path: outer ok=True", r.ok)
            inner = r.data if isinstance(r.data, dict) else {}
            t("happy path: tool saw secret value (value_present=True)",
              inner.get("value_present") is True,
              detail=f"data={inner!r}")
            t("happy path: fingerprint matches expected",
              inner.get("fingerprint") == _expected_fingerprint(secret_value),
              detail=f"got {inner.get('fingerprint')!r}, expected "
                     f"{_expected_fingerprint(secret_value)!r}")
            t("happy path: value_length matches",
              inner.get("value_length") == len(secret_value))

            # ---- redaction: leak_line must NOT contain the value -----
            t("redaction: leak_line does not contain raw secret",
              secret_value not in inner.get("leak_line", ""),
              detail=f"leak_line={inner.get('leak_line')!r}")
            # The leak_line must carry a redaction marker proving the raw
            # secret was scrubbed. Production redacts stdout in TWO passes:
            # a byte-level pre-JSON pass (runner.py, the R1 full_stdout.bin
            # fix) emits ``[REDACTED]``, and the struct-level recursive pass
            # (secret_vault.redact_values) emits ``<redacted>``. The
            # byte-level pass runs first, so the observable placeholder here
            # is ``[REDACTED]``. Accept either canonical marker — the
            # security property is "raw secret absent, replaced by a marker",
            # not the exact placeholder string.
            _leak = inner.get("leak_line", "")
            t("redaction: leak_line contains a redaction placeholder",
              ("<redacted>" in _leak) or ("[REDACTED]" in _leak),
              detail=f"leak_line={_leak!r}")

            # ---- audit: tool.secrets_injected event written ----------
            audit_path = td_path / "global" / "forge" / "audit.jsonl"
            events = _read_audit(audit_path)
            inj_events = [e for e in events
                          if e.get("event_type") == "tool.secrets_injected"]
            t("audit: tool.secrets_injected event present",
              len(inj_events) >= 1,
              detail=f"got {len(inj_events)} of {len(events)} events")
            if inj_events:
                d = inj_events[-1].get("details", {})
                t("audit: secrets_used carries name only",
                  d.get("secrets_used") == ["TEST_API_KEY"],
                  detail=f"details={d!r}")
                t("audit: persona recorded",
                  d.get("persona") == "test_persona")
                # Most important: the value MUST NOT appear anywhere.
                full_audit_text = audit_path.read_text()
                t("audit: secret value never appears in chain",
                  secret_value not in full_audit_text)
                # ADR-0153 M3 — with instance_identity importable (sys.path
                # set above) and a sandboxed CORVIN_HOME, the additive
                # Ed25519 instance_sig decoration must actually fire on
                # the written event. This guards against the silent-skip
                # regression where the shared dir was off sys.path.
                ev = inj_events[-1]
                t("audit: ADR-0153 instance_sig decoration present",
                  isinstance(ev.get("instance_sig"), str)
                  and len(ev["instance_sig"]) > 0
                  and isinstance(ev.get("instance_id"), str)
                  and len(ev["instance_id"]) > 0,
                  detail=f"instance_id={ev.get('instance_id')!r} "
                         f"instance_sig_len="
                         f"{len(ev.get('instance_sig') or '')}")

            # ---- ACL deny: browser persona has [] allow-list ---------
            os.environ["FORGE_PERSONA"] = "browser"
            raised = False
            try:
                run_tool(reg, "test.secret_use", {"env_key": "TEST_API_KEY"},
                         permission_mode="yes", policy=policy)
            except SecretACLDenied as exc:
                raised = "browser" in str(exc) and "TEST_API_KEY" in str(exc)
            t("ACL: browser persona denied", raised)

            # ACL deny is audited.
            events = _read_audit(audit_path)
            denied_events = [e for e in events
                             if e.get("event_type")
                             == "acl.persona_secret_denied"]
            t("audit: acl.persona_secret_denied event written",
              len(denied_events) >= 1)

            # ---- ACL deny: unknown persona (no entry → fail-closed) --
            os.environ["FORGE_PERSONA"] = "stranger"
            raised = False
            try:
                run_tool(reg, "test.secret_use", {"env_key": "TEST_API_KEY"},
                         permission_mode="yes", policy=policy)
            except SecretACLDenied:
                raised = True
            t("ACL: persona without entry denied (fail-closed)", raised)

            # ---- vault missing key ---------------------------------
            os.environ["FORGE_PERSONA"] = "test_persona"
            # Replace the vault with one that lacks our key (but still has
            # the persona on the allow-list).
            vault_file.write_text(json.dumps({"OTHER_KEY": "x"}))
            os.chmod(vault_file, 0o600)
            raised = False
            try:
                run_tool(reg, "test.secret_use",
                         {"env_key": "TEST_API_KEY"},
                         permission_mode="yes", policy=policy)
            except SecretMissing as exc:
                raised = "TEST_API_KEY" in str(exc)
            t("missing key in vault → SecretMissing", raised)

            events = _read_audit(audit_path)
            miss_events = [e for e in events
                           if e.get("event_type") == "secret.vault_missing"]
            t("audit: secret.vault_missing event written",
              len(miss_events) >= 1)

            # ---- vault entirely absent ------------------------------
            vault_file.unlink()
            raised = False
            try:
                run_tool(reg, "test.secret_use",
                         {"env_key": "TEST_API_KEY"},
                         permission_mode="yes", policy=policy)
            except SecretMissing:
                raised = True
            t("absent vault → SecretMissing", raised)

            # ---- spec on disk does not contain value ----------------
            manifest_text = (td_path / "registry.json").read_text()
            t("spec/manifest never contains the secret value",
              secret_value not in manifest_text,
              detail="(would mean secret got written to disk)")
            t("spec/manifest references the secret BY NAME",
              "TEST_API_KEY" in manifest_text)

        finally:
            if saved_home is None:
                os.environ.pop("CORVIN_HOME", None)
            else:
                os.environ["CORVIN_HOME"] = saved_home
            if saved_persona is None:
                os.environ.pop("FORGE_PERSONA", None)
            else:
                os.environ["FORGE_PERSONA"] = saved_persona
            if saved_vault is None:
                os.environ.pop("CORVIN_SECRET_VAULT", None)
            else:
                os.environ["CORVIN_SECRET_VAULT"] = saved_vault


def case_path_gate_vault() -> None:
    print("\n[case_path_gate_vault: hook protects the vault file]")
    sys.path.insert(0, str(REPO / "operator" / "voice"))
    from hooks import path_gate  # type: ignore  # noqa: E402

    saved = os.environ.get("CORVIN_SECRET_VAULT")
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "secrets.json"
        os.environ["CORVIN_SECRET_VAULT"] = str(vault)
        try:
            t("Write to vault → blocked",
              path_gate.is_protected_path(vault))

            # Bash: echo > vault path — protected.
            allow, _ = path_gate.check({
                "tool_name": "Bash",
                "tool_input": {"command": f"echo poisoned > {vault}"},
            })
            t("Bash redirect to vault → denied", not allow)

            # Bash: cat vault — not denied, only writes are blocked.
            # (path_gate is write-protection; reading IS a separate
            # concern, e.g. Bash hint matching may catch it as
            # protected, but allow-on-read is the contract.)
            allow, _ = path_gate.check({
                "tool_name": "Bash",
                "tool_input": {"command": f"cat {vault}"},
            })
            # Hint-match: command contains 'secrets.json' which is in
            # _PROTECTED_HINTS but `cat` is not a write vector. Allow.
            t("Bash cat vault → allowed (read, not write)", allow)

            # Bash: tee -a → blocked (write).
            allow, _ = path_gate.check({
                "tool_name": "Bash",
                "tool_input": {"command": f"echo x | tee -a {vault}"},
            })
            t("Bash tee to vault → denied", not allow)
        finally:
            if saved is None:
                os.environ.pop("CORVIN_SECRET_VAULT", None)
            else:
                os.environ["CORVIN_SECRET_VAULT"] = saved


def main() -> int:
    case_pure_unit_validation()
    case_redact_helper()
    case_vault_load()
    case_registry_validation()
    case_policy_acl()
    case_policy_load_merge()
    case_runner_e2e()
    case_path_gate_vault()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
