"""Per-subtask E2E — ADR-0142 (Layer Extension API) M1–M4 + M6.

Covers the load-bearing contract from
Corvin-ADR/decisions/0142-layer-extension-api.md and the must-NOT rules:

  * namespace gate rejects corvin.* / single-word / bad-charset names
    (ext.core_namespace_rejected emitted)
  * deny-wins: an extension cannot un-deny a core deny; any deny blocks
  * scope resolution order (session > task > project > user > tenant)
  * requires-missing => fail-to-load (ext.load_failed)
  * disabled-by-default after `add`
  * audit events emit allow-listed fields only (no hook payload content)

Runnable standalone:  python3 operator/bridges/shared/test_extension_registry.py
Exits non-zero on any FAIL.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(REPO / "operator" / "forge"))

import extension_api as ext_api  # noqa: E402
import extension_registry as reg  # noqa: E402
import layer_cli  # noqa: E402

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


# ── audit capture seam ───────────────────────────────────────────────────────
class _AuditCapture:
    """Stand-in for forge.security_events.write_event that records every event
    (after the ext_api allow-list filter runs). Lets tests assert exactly which
    fields reach the chain."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def write_event(self, path, event_type, *, severity=None, tool="", run_id="",
                    details=None, hash_chain=True, ts=None, unfiltered=False):
        self.events.append({"event_type": event_type, "details": dict(details or {})})
        return {"event_type": event_type}

    def path_fn(self):
        return Path(tempfile.gettempdir()) / "ext-audit-test.jsonl"


def _ctx(cap: _AuditCapture, **kw) -> ext_api.HookContext:
    return ext_api.HookContext(audit_writer=cap.write_event, audit_path_fn=cap.path_fn, **kw)


def _patch_registry_audit(registry: reg.ExtensionRegistry, cap: _AuditCapture) -> None:
    """Force the registry's _audit() to use the captured writer."""
    def _audit(event, *, name="", version="", scope="", hook="", reason=""):
        details: dict[str, Any] = {}
        if name:
            details["name"] = name
        if version:
            details["version"] = version
        if scope:
            details["scope"] = scope
        if hook:
            details["hook"] = hook
        if reason:
            details["reason"] = reason
        ctx = _ctx(cap, tenant_id=registry._tenant_id or "_default",
                   ext_name=name, ext_version=version, ext_scope=scope)
        ctx.audit_write(event, details)
    registry._audit = _audit  # type: ignore[assignment]


def _write_ext(base: Path, name: str, *, scope: str = "tenant",
               version: str = "1.0.0", hooks: list[dict] | None = None,
               requires: list[str] | None = None, hook_body: str | None = None,
               enabled: bool = False) -> Path:
    """Create an extension directory with layer.yaml + optional hook script."""
    import yaml
    ext_dir = base / name
    ext_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "name": name,
        "version": version,
        "description": f"test extension {name}",
        "author": "test <t@example.com>",
        "license": "Apache-2.0",
        "scope": scope,
    }
    if hooks:
        manifest["hooks"] = hooks
    if requires:
        manifest["requires"] = requires
    (ext_dir / "layer.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))
    if hook_body is not None:
        (ext_dir / "hooks").mkdir(exist_ok=True)
        (ext_dir / "hooks" / "hook.py").write_text(hook_body)
    if enabled:
        (ext_dir / ".enabled").touch()
    return ext_dir


# ── Section 1 — namespace gate ───────────────────────────────────────────────
def section_namespace_gate() -> None:
    print("\n[1/6] Namespace gate")
    for bad in ("corvin.path_gate", "corvin.audit", "corvin.anything"):
        try:
            reg.validate_name(bad)
            t(f"reject {bad}", False, detail="no exception")
        except reg.ExtensionNamespaceError:
            t(f"reject {bad}", True)
    # single word (no dot)
    try:
        reg.validate_name("singleword")
        t("reject single-word name", False)
    except reg.ExtensionNamespaceError:
        t("reject single-word name", True)
    # bad charset
    try:
        reg.validate_name("Acme.Bad")  # uppercase
        t("reject bad charset (uppercase)", False)
    except reg.ExtensionNamespaceError:
        t("reject bad charset (uppercase)", True)
    # valid
    try:
        reg.validate_name("acme.input_filter")
        t("accept valid vendor.name", True)
    except reg.ExtensionNamespaceError as e:
        t("accept valid vendor.name", False, detail=repr(e))

    # ext.core_namespace_rejected audit event emitted at load
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CORVIN_HOME"] = tmp
        try:
            cap = _AuditCapture()
            r = reg.ExtensionRegistry(tenant_id="_default")
            _patch_registry_audit(r, cap)
            ext_dir = _write_ext(Path(tmp) / "tenants" / "_default" / "extensions",
                                 "corvin.evil", scope="tenant")
            try:
                r.load_manifest_file(ext_dir / "layer.yaml")
                t("load corvin.* raises", False)
            except reg.ExtensionNamespaceError:
                t("load corvin.* raises", True)
            evs = [e["event_type"] for e in cap.events]
            t("ext.core_namespace_rejected emitted",
              "ext.core_namespace_rejected" in evs, detail=str(evs))
            # allow-list check on that event
            rec = next(e for e in cap.events if e["event_type"] == "ext.core_namespace_rejected")
            t("rejected event fields allow-listed",
              set(rec["details"]).issubset(ext_api.EXT_AUDIT_ALLOWED_FIELDS),
              detail=str(set(rec["details"])))
        finally:
            os.environ.pop("CORVIN_HOME", None)


# ── Section 2 — deny-wins pipeline ───────────────────────────────────────────
class _CoreDeny(ext_api.ExtensionHook):
    def handle(self, tool_name, tool_input, ctx):
        return ext_api.HookResult.deny("core-policy")


class _CoreAllow(ext_api.ExtensionHook):
    def handle(self, tool_name, tool_input, ctx):
        return ext_api.HookResult.allow()


_EXT_ALLOW_BODY = """
from extension_api import ExtensionHook, HookResult
class AllowAll(ExtensionHook):
    def handle(self, tool_name, tool_input, ctx):
        return HookResult.allow()
"""

_EXT_DENY_BODY = """
from extension_api import ExtensionHook, HookResult
class DenyBlocked(ExtensionHook):
    def handle(self, tool_name, tool_input, ctx):
        if "blocked" in str(tool_input):
            ctx.audit_write("ext.hook_denied", {"reason": "blocked-term"})
            return HookResult.deny("custom: blocked term")
        return HookResult.allow()
"""


def section_deny_wins() -> None:
    print("\n[2/6] Deny-wins pipeline")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CORVIN_HOME"] = tmp
        try:
            cap = _AuditCapture()
            ext_base = Path(tmp) / "tenants" / "_default" / "extensions"
            # an extension whose hook always ALLOWS
            _write_ext(ext_base, "acme.allow", scope="tenant",
                       hooks=[{"event": "PreToolUse", "script": "hooks/hook.py", "priority": 100}],
                       hook_body=_EXT_ALLOW_BODY, enabled=True)

            # 2a — core deny + extension allow => still deny (cannot un-deny)
            r = reg.ExtensionRegistry(tenant_id="_default", core_hooks=[(0, "corvin.core", _CoreDeny())])
            _patch_registry_audit(r, cap)
            r.discover()
            ctx = _ctx(cap, tenant_id="_default")
            res = r.run_pre_tool_use("Write", {"x": 1}, ctx)
            t("core deny + ext allow => deny", res.is_deny, detail=res.reason)

            # 2b — core allow + extension deny => deny (extension adds restriction)
            cap2 = _AuditCapture()
            _write_ext(ext_base, "acme.deny", scope="tenant",
                       hooks=[{"event": "PreToolUse", "script": "hooks/hook.py", "priority": 50}],
                       hook_body=_EXT_DENY_BODY, enabled=True)
            r2 = reg.ExtensionRegistry(tenant_id="_default", core_hooks=[(0, "corvin.core", _CoreAllow())])
            _patch_registry_audit(r2, cap2)
            r2.discover()
            ctx2 = _ctx(cap2, tenant_id="_default")
            res2 = r2.run_pre_tool_use("Write", {"cmd": "blocked here"}, ctx2)
            t("core allow + ext deny => deny", res2.is_deny, detail=res2.reason)
            evs2 = [e["event_type"] for e in cap2.events]
            t("ext.hook_denied emitted", "ext.hook_denied" in evs2, detail=str(evs2))

            # 2c — all allow => allow
            r3 = reg.ExtensionRegistry(tenant_id="_default", core_hooks=[(0, "corvin.core", _CoreAllow())])
            _patch_registry_audit(r3, _AuditCapture())
            r3.discover()
            # disable the deny extension for this check
            (ext_base / "acme.deny" / ".enabled").unlink()
            res3 = r3.run_pre_tool_use("Write", {"cmd": "fine"}, _ctx(cap, tenant_id="_default"))
            t("all allow => allow", res3.is_allow, detail=res3.reason)
        finally:
            os.environ.pop("CORVIN_HOME", None)


# ── Section 3 — scope resolution order ───────────────────────────────────────
def section_scope_resolution() -> None:
    print("\n[3/6] Scope resolution order (session > project > user > tenant)")
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as proj:
        os.environ["CORVIN_HOME"] = tmp
        try:
            home = Path(tmp)
            same_name = "acme.dup"
            # tenant
            _write_ext(home / "tenants" / "_default" / "extensions", same_name,
                       scope="tenant", version="1.0.0")
            # user
            _write_ext(home / "global" / "extensions", same_name,
                       scope="user", version="2.0.0")
            # project
            _write_ext(Path(proj) / ".corvin" / "extensions", same_name,
                       scope="project", version="3.0.0")
            # session
            sess = "telegram:chat1"
            _write_ext(home / "tenants" / "_default" / "sessions" / sess / "extensions", same_name,
                       scope="session", version="4.0.0")

            # session wins
            r = reg.ExtensionRegistry(tenant_id="_default", session_id=sess,
                                      project_root=Path(proj))
            _patch_registry_audit(r, _AuditCapture())
            r.discover()
            m = r.get(same_name)
            t("session scope wins", m is not None and m.version == "4.0.0",
              detail=m.version if m else "None")

            # without session => project wins
            r2 = reg.ExtensionRegistry(tenant_id="_default", project_root=Path(proj))
            _patch_registry_audit(r2, _AuditCapture())
            r2.discover()
            m2 = r2.get(same_name)
            t("project beats user/tenant", m2 is not None and m2.version == "3.0.0",
              detail=m2.version if m2 else "None")

            # without session+project => user wins
            r3 = reg.ExtensionRegistry(tenant_id="_default", project_root=Path(tmp) / "noproj")
            _patch_registry_audit(r3, _AuditCapture())
            r3.discover()
            m3 = r3.get(same_name)
            t("user beats tenant", m3 is not None and m3.version == "2.0.0",
              detail=m3.version if m3 else "None")
        finally:
            os.environ.pop("CORVIN_HOME", None)


# ── Section 4 — requires-missing => fail-to-load ─────────────────────────────
def section_requires() -> None:
    print("\n[4/6] requires: core dependency check (fail-to-load)")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CORVIN_HOME"] = tmp
        try:
            cap = _AuditCapture()
            ext_base = Path(tmp) / "tenants" / "_default" / "extensions"
            # 4a — missing capability
            _write_ext(ext_base, "acme.needs_missing", scope="tenant",
                       requires=["corvin.nonexistent >= 1.0"])
            r = reg.ExtensionRegistry(tenant_id="_default")
            _patch_registry_audit(r, cap)
            try:
                r.load_manifest_file(ext_base / "acme.needs_missing" / "layer.yaml")
                t("missing requires raises", False)
            except reg.ExtensionDependencyError:
                t("missing requires raises", True)
            t("ext.load_failed emitted (missing)",
              "ext.load_failed" in [e["event_type"] for e in cap.events])

            # 4b — under-version
            cap2 = _AuditCapture()
            _write_ext(ext_base, "acme.needs_newer", scope="tenant",
                       requires=["corvin.audit >= 99.0"])
            r2 = reg.ExtensionRegistry(tenant_id="_default")
            _patch_registry_audit(r2, cap2)
            try:
                r2.load_manifest_file(ext_base / "acme.needs_newer" / "layer.yaml")
                t("under-version requires raises", False)
            except reg.ExtensionDependencyError:
                t("under-version requires raises", True)

            # 4c — satisfied requires loads fine
            _write_ext(ext_base, "acme.ok_deps", scope="tenant",
                       requires=["corvin.audit >= 1.0", "corvin.path_gate >= 2.0"])
            r3 = reg.ExtensionRegistry(tenant_id="_default")
            _patch_registry_audit(r3, _AuditCapture())
            try:
                m = r3.load_manifest_file(ext_base / "acme.ok_deps" / "layer.yaml")
                t("satisfied requires loads", m.name == "acme.ok_deps")
            except reg.ExtensionError as e:
                t("satisfied requires loads", False, detail=repr(e))

            # 4d — discover() skips the fail-to-load ones, keeps the good one
            r4 = reg.ExtensionRegistry(tenant_id="_default")
            _patch_registry_audit(r4, _AuditCapture())
            r4.discover()
            names = set(r4._extensions)
            t("discover skips failed, keeps good",
              "acme.ok_deps" in names and "acme.needs_missing" not in names,
              detail=str(names))
        finally:
            os.environ.pop("CORVIN_HOME", None)


# ── Section 5 — disabled-by-default after add (CLI) ──────────────────────────
def section_disabled_by_default() -> None:
    print("\n[5/6] Disabled-by-default after add")
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as src:
        os.environ["CORVIN_HOME"] = tmp
        try:
            # source extension dir
            _write_ext(Path(src), "acme.tool", scope="tenant", version="1.2.0")
            src_dir = Path(src) / "acme.tool"

            rc = layer_cli.main(["add", str(src_dir)])
            t("add returns 0", rc == 0)

            r = reg.ExtensionRegistry(tenant_id="_default")
            _patch_registry_audit(r, _AuditCapture())
            r.discover()
            m = r.get("acme.tool")
            t("installed present", m is not None)
            t("installed DISABLED by default", m is not None and not m.enabled)

            # enable via CLI
            rc = layer_cli.main(["enable", "acme.tool"])
            t("enable returns 0", rc == 0)
            r2 = reg.ExtensionRegistry(tenant_id="_default")
            _patch_registry_audit(r2, _AuditCapture())
            r2.discover()
            m2 = r2.get("acme.tool")
            t("now enabled", m2 is not None and m2.enabled)

            # core remove/disable rejected
            rc = layer_cli.main(["remove", "corvin.path_gate"])
            t("remove core => non-zero", rc != 0)
            rc = layer_cli.main(["disable", "corvin.audit"])
            t("disable core => non-zero", rc != 0)

            # remove the extension
            rc = layer_cli.main(["remove", "acme.tool"])
            t("remove ext returns 0", rc == 0)
            r3 = reg.ExtensionRegistry(tenant_id="_default")
            _patch_registry_audit(r3, _AuditCapture())
            r3.discover()
            t("removed gone", r3.get("acme.tool") is None)
        finally:
            os.environ.pop("CORVIN_HOME", None)


# ── Section 6 — audit allow-list enforcement ─────────────────────────────────
def section_audit_allowlist() -> None:
    print("\n[6/6] Audit allow-list enforcement (no payload content)")
    cap = _AuditCapture()
    ctx = _ctx(cap, tenant_id="_default", ext_name="acme.x", ext_version="1.0.0",
               ext_scope="tenant")
    # attempt to smuggle forbidden content
    ctx.audit_write("ext.hook_denied", {
        "reason": "blocked",
        "tool_input": {"secret": "leak-me"},   # forbidden
        "output": "sensitive output",          # forbidden
        "prompt": "user prompt text",          # forbidden
        "hook": "PreToolUse",
    })
    rec = cap.events[-1]
    fields = set(rec["details"])
    t("only allow-listed fields survive",
      fields.issubset(ext_api.EXT_AUDIT_ALLOWED_FIELDS), detail=str(fields))
    t("forbidden content dropped",
      "tool_input" not in fields and "output" not in fields and "prompt" not in fields)
    t("extension identity injected",
      rec["details"].get("name") == "acme.x" and rec["details"].get("version") == "1.0.0")
    t("reason + hook preserved",
      rec["details"].get("reason") == "blocked" and rec["details"].get("hook") == "PreToolUse")

    # Registered allow-list on the writer side (defence-in-depth)
    sys.modules.pop("forge.security_events", None)
    from forge import security_events as se
    for ev in ("ext.installed", "ext.removed", "ext.enabled", "ext.disabled",
               "ext.hook_denied", "ext.load_failed", "ext.core_namespace_rejected"):
        t(f"{ev} registered in EVENT_SEVERITY", ev in se.EVENT_SEVERITY,
          detail=se.EVENT_SEVERITY.get(ev, "<missing>"))
        t(f"{ev} positive allow-list registered", ev in se._EVENT_ALLOWLIST)


def main() -> int:
    print("=" * 60)
    print("test_extension_registry.py — ADR-0142 M1–M4 + M6")
    print("=" * 60)
    section_namespace_gate()
    section_deny_wins()
    section_scope_resolution()
    section_requires()
    section_disabled_by_default()
    section_audit_allowlist()
    print()
    print(f"PASS={PASS}  FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
