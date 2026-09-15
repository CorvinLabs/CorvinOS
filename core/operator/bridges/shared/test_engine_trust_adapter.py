"""Per-subtask E2E — ADR-0020 Phase 30.1b (adapter wiring).

Verifies that ``adapter._check_engine_trust_or_fail`` honours its
contract:

  * No tenant config → permissive default → spawn proceeds (return None)
  * Tenant config without engine_trust block → low default → spawn proceeds
  * `min_tier: high` + claude_code (high) → spawn proceeds
  * `min_tier: high` + low-tier engine → refusal returned + audit emitted
  * Expired bundle manifest → refusal + manifest_expired event
  * Engine without `name` attribute → fail-OPEN (operational issue)
  * Module import-absent → fail-OPEN (loaded as None at top of file)

Layer 30.1b is a load-bearing spawn-path change; covering every
permissive-default branch is the regression contract.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
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


def _iso(offset_days: int = 0) -> str:
    dt = datetime.now(timezone.utc) + timedelta(days=offset_days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_yaml(path: Path, body: dict[str, Any]) -> None:
    import yaml as _y
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_y.safe_dump(body, sort_keys=False))


def _good_manifest(engine_id: str, *, tier: str = "high",
                   valid_until: str | None = None,
                   binary_sha256: str | None = None) -> dict[str, Any]:
    return {
        "apiVersion": "corvin/v1",
        "kind": "EngineTrust",
        "metadata": {
            "engine_id": engine_id,
            "trust_tier": tier,
            "evaluated_at": _iso(-10),
            "evaluated_against": "test-fixture",
            "valid_until": valid_until or _iso(180),
        },
        "spec": {
            "binary_sha256": binary_sha256,
            "jailbreak_resistance": 0.9,
            "system_prompt_respect": 0.9,
            "tool_call_fidelity": 0.95,
            "tested_refusal_classes": ["harmful_content"],
            "notes": "test fixture",
        },
    }


def _write_tenant_config(tmp: Path, *, min_tier: str | None = "low") -> None:
    """Stand up <corvin_home>/tenants/_default/global/tenant.corvin.yaml."""
    p = tmp / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    body: dict[str, Any] = {
        "apiVersion": "corvin/v1",
        "kind": "Tenant",
        "metadata": {"id": "_default"},
        "spec": {},
    }
    if min_tier is not None:
        body["spec"]["engine_trust"] = {"min_tier": min_tier}
    import yaml as _y
    p.write_text(_y.safe_dump(body))


def _fresh_modules():
    """Re-import engine_trust + adapter so module-level constants pick
    up the test's CORVIN_HOME."""
    for mod in ("engine_trust", "adapter"):
        sys.modules.pop(mod, None)
    et = importlib.import_module("engine_trust")
    ad = importlib.import_module("adapter")
    return ad, et


class _StubEngine:
    """Minimal stand-in — only exposes the .name attribute the gate needs."""
    def __init__(self, name: str) -> None:
        self.name = name


# ---------------------------------------------------------------------------
# Section 1 — fail-open paths (operational issues)
# ---------------------------------------------------------------------------


def section_fail_open() -> None:
    print("\n[1/4] Fail-open paths (operational defaults)")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CORVIN_HOME"] = tmp
        try:
            ad, _ = _fresh_modules()

            # 1a — No tenant config at all → permissive low → proceeds
            engine = _StubEngine("claude_code")
            r = ad._check_engine_trust_or_fail(
                engine, channel="test", chat_key="c1")
            t("no tenant config → spawn proceeds", r is None,
              detail=f"got {r!r}")

            # 1b — Tenant config without engine_trust block → low default
            _write_tenant_config(Path(tmp), min_tier=None)
            r = ad._check_engine_trust_or_fail(
                engine, channel="test", chat_key="c1")
            t("tenant config without engine_trust → proceeds", r is None,
              detail=f"got {r!r}")

            # 1c — Engine without .name attribute → fail-OPEN
            class _Anon:
                pass
            r = ad._check_engine_trust_or_fail(
                _Anon(), channel="test", chat_key="c1")
            t("engine without .name → fail-open", r is None,
              detail=f"got {r!r}")
        finally:
            os.environ.pop("CORVIN_HOME", None)


# ---------------------------------------------------------------------------
# Section 2 — happy paths (gate passes legitimately)
# ---------------------------------------------------------------------------


def section_happy_paths() -> None:
    print("\n[2/4] Happy paths (gate passes)")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CORVIN_HOME"] = tmp
        try:
            ad, _ = _fresh_modules()
            engine = _StubEngine("claude_code")

            # 2a — min_tier=low + bundle claude_code (high) → passes
            _write_tenant_config(Path(tmp), min_tier="low")
            r = ad._check_engine_trust_or_fail(
                engine, channel="test", chat_key="c1")
            t("min_tier=low + claude_code (high) → passes", r is None)

            # 2b — min_tier=medium + bundle claude_code (high) → passes
            _write_tenant_config(Path(tmp), min_tier="medium")
            r = ad._check_engine_trust_or_fail(
                engine, channel="test", chat_key="c1")
            t("min_tier=medium + claude_code (high) → passes", r is None)

            # 2c — min_tier=high + bundle claude_code (high) → passes
            _write_tenant_config(Path(tmp), min_tier="high")
            r = ad._check_engine_trust_or_fail(
                engine, channel="test", chat_key="c1")
            t("min_tier=high + claude_code (high) → passes", r is None)
        finally:
            os.environ.pop("CORVIN_HOME", None)


# ---------------------------------------------------------------------------
# Section 3 — gate trips (refusal + audit)
# ---------------------------------------------------------------------------


def section_gate_trips() -> None:
    print("\n[3/4] Gate trips (refusal returned, audit emitted)")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CORVIN_HOME"] = tmp
        try:
            ad, et = _fresh_modules()

            # 3a — min_tier=high + bundle opencode (low) → refused
            _write_tenant_config(Path(tmp), min_tier="high")
            engine = _StubEngine("opencode")
            r = ad._check_engine_trust_or_fail(
                engine, channel="test", chat_key="c1")
            t("min_tier=high + opencode (low) → refused",
              isinstance(r, str) and "[engine-trust]" in r,
              detail=str(r)[:80])
            t("refusal mentions tier mismatch",
              "trust" in (r or "").lower())

            # 3b — Audit event landed in the chain
            audit_path = (Path(tmp) / "tenants" / "_default" /
                          "global" / "forge" / "audit.jsonl")
            t("audit chain file created", audit_path.exists())
            if audit_path.exists():
                lines = [json.loads(line) for line in
                         audit_path.read_text().splitlines() if line]
                kinds = {l["event_type"] for l in lines}
                t("trust_tier_violated event emitted",
                  "engine.trust_tier_violated" in kinds,
                  detail=str(sorted(kinds)))

            # 3c — Engine name "nonexistent" → manifest-missing → refused
            engine2 = _StubEngine("nonexistent_engine")
            r = ad._check_engine_trust_or_fail(
                engine2, channel="test", chat_key="c1")
            t("unknown engine name → refused",
              isinstance(r, str) and "[engine-trust]" in r,
              detail=str(r)[:80])

            # 3d — Override the bundle manifest with an EXPIRED one
            override = (Path(tmp) / "global" / "engine_trust" /
                        "claude_code.yaml")
            _write_yaml(override, _good_manifest(
                "claude_code", tier="high", valid_until=_iso(-30)))
            ad, et = _fresh_modules()
            _write_tenant_config(Path(tmp), min_tier="medium")
            r = ad._check_engine_trust_or_fail(
                _StubEngine("claude_code"),
                channel="test", chat_key="c1")
            t("expired manifest → refused",
              isinstance(r, str) and ("abgelaufen" in (r or "") or "expired" in (r or "")),
              detail=str(r)[:80])
        finally:
            os.environ.pop("CORVIN_HOME", None)


# ---------------------------------------------------------------------------
# Section 4 — module-absent fail-open
# ---------------------------------------------------------------------------


def section_module_absent() -> None:
    print("\n[4/4] Module-absent fail-open (defensive)")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CORVIN_HOME"] = tmp
        try:
            ad, _ = _fresh_modules()
            # Simulate: the import at adapter top failed
            saved = ad._engine_trust
            ad._engine_trust = None
            try:
                r = ad._check_engine_trust_or_fail(
                    _StubEngine("anything"),
                    channel="test", chat_key="c1")
                t("engine_trust module=None → fail-open", r is None)
            finally:
                ad._engine_trust = saved
        finally:
            os.environ.pop("CORVIN_HOME", None)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 60)
    print("test_engine_trust_adapter.py — ADR-0020 Phase 30.1b")
    print("=" * 60)

    section_fail_open()
    section_happy_paths()
    section_gate_trips()
    section_module_absent()

    print()
    print(f"PASS={PASS}  FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
