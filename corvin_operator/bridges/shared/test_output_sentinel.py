"""Per-subtask E2E — ADR-0020 Phase 30.3 (Output-Sentinel).

Covers:
  * Mode normalisation (off/advisory/enforcing + truthy/falsy synonyms)
  * Verdict-parser trichotomy (CLEAN / BLOCKED / UNPARSEABLE)
  * Block-reason classification + fallback to "other"
  * Output truncation (8 KB head + tail with marker)
  * Fake-mode subprocess hooks (clean / blocked / garbage / timeout)
  * is_sentinel_active gate (per-persona + per-tenant)
  * judge_output mode semantics (off / advisory passes BLOCKED through;
    enforcing blocks BLOCKED; all modes fail-open on judge_error)
  * Audit-event emission with allow-list + forbidden-field gate
  * EVENT_SEVERITY registry for the three new event types
  * AST cost-contract: NO `import anthropic` (mirror of L11/L29.5)
  * Adapter wiring — _apply_output_sentinel respects opt-in + opt-out
"""
from __future__ import annotations

import ast
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(REPO / "operator" / "forge"))

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


def _fresh_modules():
    for m in ("output_sentinel", "adapter"):
        sys.modules.pop(m, None)
    osen = importlib.import_module("output_sentinel")
    ad = importlib.import_module("adapter")
    return osen, ad


def _write_tenant_config(tmp: Path, *, et_block: dict[str, Any] | None) -> None:
    p = tmp / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    body: dict[str, Any] = {
        "apiVersion": "corvin/v1",
        "kind": "Tenant",
        "metadata": {"id": "_default"},
        "spec": {},
    }
    if et_block is not None:
        body["spec"]["engine_trust"] = et_block
    import yaml as _y
    p.write_text(_y.safe_dump(body))


# ---------------------------------------------------------------------------
# Section 1 — Mode normalisation
# ---------------------------------------------------------------------------


def section_mode() -> None:
    print("\n[1/9] Mode normalisation")
    osen, _ = _fresh_modules()

    t("'off' → off", osen.normalise_mode("off") == "off")
    t("'advisory' → advisory", osen.normalise_mode("advisory") == "advisory")
    t("'enforcing' → enforcing", osen.normalise_mode("enforcing") == "enforcing")
    t("True → advisory (legacy on)", osen.normalise_mode(True) == "advisory")
    t("False → off", osen.normalise_mode(False) == "off")
    t("None → off", osen.normalise_mode(None) == "off")
    t("'BOGUS' → off (fail-safe)", osen.normalise_mode("BOGUS") == "off")
    t("case-insensitive 'ENFORCING'",
      osen.normalise_mode("ENFORCING") == "enforcing")


# ---------------------------------------------------------------------------
# Section 2 — Verdict parser
# ---------------------------------------------------------------------------


def section_parser() -> None:
    print("\n[2/9] Verdict parser")
    osen, _ = _fresh_modules()

    head, rest = osen._parse_verdict("CLEAN | output looks fine")
    t("CLEAN parsed", head == "CLEAN" and "output" in rest)

    head, rest = osen._parse_verdict("BLOCKED | harmful_content: refused")
    t("BLOCKED parsed", head == "BLOCKED" and "harmful" in rest)

    head, _ = osen._parse_verdict("garbage with no marker")
    t("no-marker → UNPARSEABLE", head == "UNPARSEABLE")

    head, _ = osen._parse_verdict("")
    t("empty → UNPARSEABLE", head == "UNPARSEABLE")

    head, _ = osen._parse_verdict("CLEAN  |  short")
    t("extra whitespace tolerated", head == "CLEAN")


# ---------------------------------------------------------------------------
# Section 3 — Block-reason classification
# ---------------------------------------------------------------------------


def section_block_reason() -> None:
    print("\n[3/9] Block-reason classification")
    osen, _ = _fresh_modules()

    t("harmful_content extracted",
      osen._classify_block_reason("harmful_content: bad") == "harmful_content")
    t("prompt_injection_success extracted",
      osen._classify_block_reason("prompt_injection_success: oops")
      == "prompt_injection_success")
    t("unknown class → other",
      osen._classify_block_reason("nuclear_option: bad") == "other")
    t("missing class → other",
      osen._classify_block_reason("just text without class") == "other")


# ---------------------------------------------------------------------------
# Section 4 — Output truncation
# ---------------------------------------------------------------------------


def section_truncation() -> None:
    print("\n[4/9] Output truncation (8 KB head+tail)")
    osen, _ = _fresh_modules()

    short = "hello world"
    t("short output unchanged", osen._truncate_for_judge(short) == short)

    big = "A" * 5000 + "B" * 5000  # 10 KB
    out = osen._truncate_for_judge(big)
    t("long output gets truncated", len(out) < len(big))
    t("truncation marker present", "TRUNCATED" in out)
    t("head preserved", out.startswith("A" * 100))
    t("tail preserved", out.endswith("B" * 100))


# ---------------------------------------------------------------------------
# Section 5 — judge_output mode semantics (with fake hook)
# ---------------------------------------------------------------------------


def section_judge_modes() -> None:
    print("\n[5/9] judge_output — mode semantics")
    osen, _ = _fresh_modules()

    # 5a — off mode: no subprocess, passes
    v = osen.judge_output("p", "o", mode="off")
    t("off mode → passes + skipped",
      v.passed and v.reason == "skipped" and v.mode == "off")

    # 5b — advisory + clean
    os.environ["CORVIN_SENTINEL_FAKE"] = "clean"
    try:
        v = osen.judge_output("p", "o", mode="advisory")
        t("advisory + clean → passes",
          v.passed and v.reason == "clean" and v.mode == "advisory")

        # 5c — enforcing + clean
        v = osen.judge_output("p", "o", mode="enforcing")
        t("enforcing + clean → passes",
          v.passed and v.reason == "clean" and v.mode == "enforcing")
    finally:
        os.environ.pop("CORVIN_SENTINEL_FAKE", None)

    # 5d — advisory + blocked → still passes (audit-only mode)
    os.environ["CORVIN_SENTINEL_FAKE"] = "blocked"
    try:
        v = osen.judge_output("p", "o", mode="advisory")
        t("advisory + BLOCKED → passes (audit-only)",
          v.passed and v.reason == "blocked"
          and v.block_reason == "harmful_content")

        # 5e — enforcing + blocked → blocks
        v = osen.judge_output("p", "o", mode="enforcing")
        t("enforcing + BLOCKED → blocks",
          (not v.passed) and v.reason == "blocked"
          and v.block_reason == "harmful_content")
    finally:
        os.environ.pop("CORVIN_SENTINEL_FAKE", None)

    # 5f — garbage verdict → unparseable, fail-open
    os.environ["CORVIN_SENTINEL_FAKE"] = "garbage"
    try:
        for mode in ("advisory", "enforcing"):
            v = osen.judge_output("p", "o", mode=mode)
            t(f"{mode} + garbage → unparseable + passes",
              v.passed and v.reason == "unparseable")
    finally:
        os.environ.pop("CORVIN_SENTINEL_FAKE", None)

    # 5g — subprocess timeout → judge_error, fail-open
    os.environ["CORVIN_SENTINEL_FAKE"] = "timeout"
    try:
        for mode in ("advisory", "enforcing"):
            v = osen.judge_output("p", "o", mode=mode)
            t(f"{mode} + timeout → judge_error + passes",
              v.passed and v.reason == "judge_error")
    finally:
        os.environ.pop("CORVIN_SENTINEL_FAKE", None)


# ---------------------------------------------------------------------------
# Section 6 — is_sentinel_active gate
# ---------------------------------------------------------------------------


def section_active_gate() -> None:
    print("\n[6/9] is_sentinel_active gate")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CORVIN_HOME"] = tmp
        try:
            osen, _ = _fresh_modules()

            # 6a — None profile + no tenant config → not active
            t("no profile, no tenant → not active",
              not osen.is_sentinel_active(None, None))

            # 6b — Persona JSON declares output_sentinel: true
            profile = {"persona": "research", "output_sentinel": True}
            t("persona JSON true → active",
              osen.is_sentinel_active("research", profile))

            # 6c — String "yes" also activates
            profile = {"persona": "x", "output_sentinel": "yes"}
            t("persona JSON 'yes' → active",
              osen.is_sentinel_active("x", profile))

            # 6d — Tenant allowlist
            _write_tenant_config(Path(tmp), et_block={
                "sentinel_personas": ["research", "browser"],
            })
            osen, _ = _fresh_modules()
            t("tenant allowlists 'research' → active",
              osen.is_sentinel_active("research", {}))
            t("persona NOT in allowlist → not active",
              not osen.is_sentinel_active("coder", {}))

            # 6e — Persona override beats missing tenant
            t("persona JSON wins regardless of tenant",
              osen.is_sentinel_active("coder",
                                       {"output_sentinel": True}))
        finally:
            os.environ.pop("CORVIN_HOME", None)


# ---------------------------------------------------------------------------
# Section 7 — Audit emission
# ---------------------------------------------------------------------------


def section_audit() -> None:
    print("\n[7/9] Audit emission + allow-list")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CORVIN_HOME"] = tmp
        os.environ["CORVIN_SENTINEL_FAKE"] = "blocked"
        try:
            osen, _ = _fresh_modules()
            verdict = osen.judge_output("prompt", "harmful output",
                                          mode="enforcing")
            ev = osen.emit_sentinel_event(verdict, persona="research",
                                            engine_id="claude_code")
            t("blocked verdict → sentinel_blocked event",
              ev == "engine.sentinel_blocked")

            audit_p = (Path(tmp) / "tenants" / "_default" / "global" /
                       "forge" / "audit.jsonl")
            t("audit chain file created", audit_p.exists())
            if audit_p.exists():
                lines = [json.loads(l) for l in
                         audit_p.read_text().splitlines() if l]
                t("chain has one entry", len(lines) == 1)
                d = lines[0]["details"]
                t("event has engine_id", "engine_id" in d)
                t("event has persona", "persona" in d)
                t("event has reason (block_reason)", "reason" in d)
                t("event has output_chars", "output_chars" in d)
                # forbidden fields not present
                for forbidden in ("prompt", "output", "verdict_text"):
                    t(f"no {forbidden} in details", forbidden not in d)
        finally:
            os.environ.pop("CORVIN_HOME", None)
            os.environ.pop("CORVIN_SENTINEL_FAKE", None)

    # Forbidden-field rejection at boundary
    osen, _ = _fresh_modules()
    try:
        osen._validate_audit_details(
            "engine.sentinel_blocked",
            {"engine_id": "x", "persona": "y", "reason": "z",
             "output_chars": 1, "wall_clock_s": 0.1,
             "prompt": "leaked!"},
        )
        t("forbidden field rejected", False)
    except osen.SentinelAuditFieldNotAllowed:
        t("forbidden field rejected", True)

    try:
        osen._validate_audit_details(
            "engine.sentinel_blocked",
            {"engine_id": "x", "uninvited_field": "x"},
        )
        t("off-allowlist field rejected", False)
    except osen.SentinelAuditFieldNotAllowed:
        t("off-allowlist field rejected", True)

    # Clean verdict + audit_passed=False → no event
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CORVIN_HOME"] = tmp
        os.environ["CORVIN_SENTINEL_FAKE"] = "clean"
        try:
            osen, _ = _fresh_modules()
            verdict = osen.judge_output("p", "o", mode="advisory")
            ev = osen.emit_sentinel_event(verdict, persona="research",
                                            engine_id="claude_code",
                                            audit_passed=False)
            t("clean + audit_passed=False → no event", ev is None)

            # Clean + audit_passed=True → sentinel_passed event
            ev = osen.emit_sentinel_event(verdict, persona="research",
                                            engine_id="claude_code",
                                            audit_passed=True)
            t("clean + audit_passed=True → sentinel_passed event",
              ev == "engine.sentinel_passed")
        finally:
            os.environ.pop("CORVIN_HOME", None)
            os.environ.pop("CORVIN_SENTINEL_FAKE", None)


# ---------------------------------------------------------------------------
# Section 8 — EVENT_SEVERITY registry + cost contract
# ---------------------------------------------------------------------------


def section_registry_and_cost() -> None:
    print("\n[8/9] EVENT_SEVERITY-Registry + Cost-Contract")
    sys.modules.pop("forge.security_events", None)
    from forge import security_events as se
    expected = {
        "engine.sentinel_blocked":      "WARNING",
        "engine.sentinel_passed":       "INFO",
        "engine.sentinel_unparseable":  "WARNING",
    }
    for ev, sev in expected.items():
        t(f"{ev} registered as {sev}",
          se.EVENT_SEVERITY.get(ev) == sev,
          detail=se.EVENT_SEVERITY.get(ev, "<missing>"))

    # AST walk: NO anthropic / openai / google SDK imports
    p = REPO / "operator" / "bridges" / "shared" / "output_sentinel.py"
    tree = ast.parse(p.read_text())
    forbidden = ("anthropic", "openai", "google.generativeai",
                 "google_generativeai")
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in forbidden:
                    bad.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in forbidden:
                bad.append(node.module)
    t("no forbidden LLM-SDK imports in output_sentinel.py",
      not bad,
      detail=f"found: {bad}" if bad else "")


# ---------------------------------------------------------------------------
# Section 9 — Adapter wiring
# ---------------------------------------------------------------------------


def section_adapter_wiring() -> None:
    print("\n[9/9] Adapter wiring — _apply_output_sentinel")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CORVIN_HOME"] = tmp
        try:
            _, ad = _fresh_modules()

            # 9a — No persona opt-in, no tenant allowlist → pass-through
            r = ad._apply_output_sentinel(
                "prompt", "raw output",
                profile=None, engine_name="claude_code",
                channel="t", chat_key="c",
            )
            t("no opt-in → pass-through", r == "raw output")

            # 9b — Empty output → pass-through (nothing to judge)
            r = ad._apply_output_sentinel(
                "p", "",
                profile={"persona": "x", "output_sentinel": True},
                engine_name="claude_code", channel="t", chat_key="c",
            )
            t("empty output → pass-through", r == "")

            # 9c — Persona opt-in + advisory + CLEAN → pass-through
            os.environ["CORVIN_SENTINEL_FAKE"] = "clean"
            try:
                r = ad._apply_output_sentinel(
                    "p", "good output",
                    profile={"persona": "research", "output_sentinel": True},
                    engine_name="claude_code", channel="t", chat_key="c",
                )
                t("opt-in + clean → pass-through (advisory default)",
                  r == "good output")
            finally:
                os.environ.pop("CORVIN_SENTINEL_FAKE", None)

            # 9d — Persona opt-in + tenant enforcing + BLOCKED → block
            _write_tenant_config(Path(tmp), et_block={
                "sentinel_mode": "enforcing",
            })
            _, ad = _fresh_modules()
            os.environ["CORVIN_SENTINEL_FAKE"] = "blocked"
            try:
                r = ad._apply_output_sentinel(
                    "p", "harmful output",
                    profile={"persona": "research", "output_sentinel": True},
                    engine_name="claude_code", channel="t", chat_key="c",
                )
                t("opt-in + enforcing + BLOCKED → curated block-message",
                  isinstance(r, str) and r != "harmful output"
                  and "Output-Sentinel" in r,
                  detail=r[:80])
            finally:
                os.environ.pop("CORVIN_SENTINEL_FAKE", None)

            # 9e — Tenant advisory + BLOCKED → still pass-through
            _write_tenant_config(Path(tmp), et_block={
                "sentinel_mode": "advisory",
            })
            _, ad = _fresh_modules()
            os.environ["CORVIN_SENTINEL_FAKE"] = "blocked"
            try:
                r = ad._apply_output_sentinel(
                    "p", "harmful output",
                    profile={"persona": "research", "output_sentinel": True},
                    engine_name="claude_code", channel="t", chat_key="c",
                )
                t("opt-in + advisory + BLOCKED → pass-through (audit-only)",
                  r == "harmful output",
                  detail=r[:80])
            finally:
                os.environ.pop("CORVIN_SENTINEL_FAKE", None)

            # 9f — Module-absent fail-open
            _, ad = _fresh_modules()
            saved = ad._output_sentinel
            ad._output_sentinel = None
            try:
                r = ad._apply_output_sentinel(
                    "p", "x",
                    profile={"persona": "research", "output_sentinel": True},
                    engine_name="claude_code", channel="t", chat_key="c",
                )
                t("module=None → pass-through", r == "x")
            finally:
                ad._output_sentinel = saved
        finally:
            os.environ.pop("CORVIN_HOME", None)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 60)
    print("test_output_sentinel.py — ADR-0020 Phase 30.3")
    print("=" * 60)

    section_mode()
    section_parser()
    section_block_reason()
    section_truncation()
    section_judge_modes()
    section_active_gate()
    section_audit()
    section_registry_and_cost()
    section_adapter_wiring()

    print()
    print(f"PASS={PASS}  FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
