"""Standard plugins: the list of plugins a fresh CorvinOS installation gets.

The list is ``default_plugins.yaml`` next to this module. :func:`provision` installs each
entry through the SAME gates as ``POST /api/v1/marketplace/plugins/{id}/install``
(``routes/marketplace_install._run_install``): marketplace index allowlist, local source
resolved under a trusted root, ADR-0247 manifest gate, location-derived origin, the
``PluginLifecycle`` registry write. Nothing is bypassed; the only difference is that the
caller is the host at boot rather than a browser session.

Rules that make this safe to run on every boot:

* **Once per tenant per entry.** ``<tenant>/plugins/default_plugins.json`` records every
  entry that was offered. A plugin the operator uninstalled is not re-installed, and a
  second boot is a no-op.
* **Never implies consent.** ``enable: true`` only enables a plugin whose record needs no
  consent (``PluginRecord.consent_required()``); otherwise it stays installed, disabled.
* **Never blocks boot.** :func:`start` runs in a daemon thread (the source may have to be
  downloaded from GitHub); every failure is logged and recorded as a per-entry outcome.
* **Retried until it sticks.** A failed entry (e.g. offline first boot) is NOT written to
  the ledger, so the next boot tries it again.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("corvin.default_plugins")

LIST_PATH = Path(__file__).with_name("default_plugins.yaml")
_INDEX_ID_RE = re.compile(r"^plugin:[a-z]+-[A-Za-z0-9_-]+-[A-Za-z0-9_-]+$")
_SID = "boot:default_plugins"
_started: set[str] = set()
_lock = threading.Lock()


class DefaultPluginsError(Exception):
    """The list file is malformed."""


def _vtuple(v: str) -> tuple:
    return tuple(int(p) if p.isdigit() else 0 for p in re.split(r"[.\-+]", str(v))[:4])


def load_list(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Parse and validate the list. Raises :class:`DefaultPluginsError` on any defect —
    a malformed bundle list must fail loudly, not silently install a subset."""
    import yaml

    p = path or LIST_PATH
    try:
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DefaultPluginsError(f"cannot read {p}: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("schema") != 1 or not isinstance(doc.get("plugins"), list):
        raise DefaultPluginsError(f"{p}: expected `schema: 1` and a `plugins:` list")
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for i, e in enumerate(doc["plugins"]):
        if not isinstance(e, dict):
            raise DefaultPluginsError(f"plugins[{i}] is not a mapping")
        iid = str(e.get("index_id") or "")
        if not _INDEX_ID_RE.match(iid):
            raise DefaultPluginsError(f"plugins[{i}].index_id {iid!r} is not a marketplace index id")
        if iid in seen:
            raise DefaultPluginsError(f"duplicate entry {iid}")
        seen.add(iid)
        out.append({
            "index_id": iid,
            "registry_id": str(e.get("registry_id") or ""),
            "name": str(e.get("name") or iid),
            "min_version": str(e.get("min_version") or "0.0.0"),
            "enable": bool(e.get("enable", False)),
            "reason": " ".join(str(e.get("reason") or "").split()),
        })
    return out


def _ledger_path(tenant_id: str) -> Path:
    from corvin_plugins.state import registry_path  # type: ignore[import-not-found]

    return registry_path(tenant_id=tenant_id).parent / "default_plugins.json"


def _read_ledger(tenant_id: str) -> Dict[str, Any]:
    try:
        d = json.loads(_ledger_path(tenant_id).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and isinstance(d.get("offered"), dict) else {"offered": {}}
    except (OSError, ValueError):
        return {"offered": {}}


def _write_ledger(tenant_id: str, ledger: Dict[str, Any]) -> None:
    path = _ledger_path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".default_plugins.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(ledger, fh, indent=2, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _audit(tenant_id: str, action: str, plugin_id: str) -> None:
    try:
        from . import audit as console_audit

        console_audit.action_performed(
            tenant_id=tenant_id, sid_fingerprint=_SID, action=action,
            target_kind="marketplace_plugin", target_id=plugin_id,
        )
    except Exception:  # noqa: BLE001 — the lifecycle already wrote its own chain record
        log.warning("default plugin audit failed for %s", plugin_id, exc_info=True)


def provision(tenant_id: str, *, entries: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Install every not-yet-offered entry for *tenant_id*. Returns one outcome dict per entry:
    ``status`` is ``installed`` | ``already_offered`` | ``failed`` | ``skipped``."""
    from .routes import marketplace_install as _mi
    from .routes import marketplace_resolve as _resolve

    entries = load_list() if entries is None else entries
    results: List[Dict[str, Any]] = []
    if not entries:
        return results
    if not _mi._LIFECYCLE_AVAILABLE or not _resolve.available():
        return [{"index_id": e["index_id"], "status": "failed",
                 "error": "plugin subsystem unavailable in this installation"} for e in entries]
    from corvin_plugins.manifest import PluginError  # type: ignore[import-not-found]
    from corvin_plugins.state import TenantRegistry  # type: ignore[import-not-found]

    with _lock:
        ledger = _read_ledger(tenant_id)
        pending = [e for e in entries if e["index_id"] not in ledger["offered"]]
        if pending:
            try:
                _resolve._bootstrap.ensure_marketplace_source()
            except Exception:  # noqa: BLE001 — best-effort; resolve reports the real reason
                log.warning("marketplace source sync failed", exc_info=True)
        for e in entries:
            iid = e["index_id"]
            res: Dict[str, Any] = {"index_id": iid, "name": e["name"]}
            results.append(res)
            if iid in ledger["offered"]:
                res["status"] = "already_offered"
                continue
            try:
                if not _mi._index_has(iid):
                    raise DefaultPluginsError(f"{iid} is not in the marketplace index")
                plugin_dir, manifest = _resolve.load_manifest(iid)
                if _vtuple(str(manifest.get("version", "0.0.0"))) < _vtuple(e["min_version"]):
                    res.update(status="skipped", error=(
                        f"marketplace lists {manifest.get('version')}, below min_version {e['min_version']}"))
                    continue
                report = _resolve.validate_builtin_manifest(plugin_dir)
                if not report.ok:
                    raise DefaultPluginsError("manifest gate: " + "; ".join(f.message for f in report.errors[:3]))
                record = _resolve.record_from_manifest(manifest, plugin_dir=plugin_dir)
                life = _mi._lifecycle(tenant_id)
                try:
                    life.install(record, installed_by="default_plugins")
                    _audit(tenant_id, "marketplace.install", iid)
                except PluginError as exc:
                    if "already installed" not in str(exc):
                        raise
                res.update(status="installed", registry_id=record.plugin_id, version=str(record.version),
                           requires_consent=bool(record.consent_required()), enabled=False)
                if e["enable"] and not record.consent_required():
                    try:
                        res["enabled"] = bool(life.enable(record.plugin_id).enabled)
                        _audit(tenant_id, "marketplace.enable", iid)
                    except Exception as exc:  # noqa: BLE001 — installed stays; operator can enable
                        res["enable_error"] = f"{type(exc).__name__}: {exc}"[:300]
                elif e["enable"]:
                    res["enable_error"] = "needs the operator's consent — left disabled"
                ledger["offered"][iid] = {"registry_id": record.plugin_id, "version": str(record.version)}
                _write_ledger(tenant_id, ledger)
            except Exception as exc:  # noqa: BLE001 — one bad entry must not stop the rest
                res.update(status="failed", error=f"{type(exc).__name__}: {exc}"[:300])
                log.warning("default plugin %s not provisioned: %s", iid, res["error"])
                _audit(tenant_id, "marketplace.install_failed", iid)
    return results


def start(tenant_id: Optional[str] = None) -> bool:
    """Provision the standard plugins in a daemon thread, once per process and tenant."""
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("CORVIN_DEFAULT_PLUGINS_IN_TESTS"):
        return False
    if tenant_id is None:
        from forge.tenants import current_tenant  # type: ignore[import-not-found]

        tenant_id = current_tenant()
    if tenant_id in _started:
        return False
    _started.add(tenant_id)

    def _run() -> None:
        try:
            for r in provision(tenant_id):
                log.info("default plugin %s: %s %s", r["index_id"], r["status"], r.get("error", ""))
        except Exception:  # noqa: BLE001 — never blocks or kills the host
            log.warning("default plugin provisioning crashed", exc_info=True)

    threading.Thread(target=_run, name="default-plugins", daemon=True).start()
    return True
