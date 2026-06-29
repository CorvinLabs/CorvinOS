"""Built-in Nerve Fibers für CorvinOS — Tier-0 Discovery.

Jede Fiber hier wrAPPT einen bestehenden ACO-Check ohne dessen Implementierung
zu verändern. Neue Layer fügen einfach eine neue Fiber-Klasse hinzu.

Diese Datei ist der zentrale Ort wo der Nutzer sehen kann, welche Layers
vom Nervensystem erfasst werden.
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path

from .nerve import (NerveFiber, NerveSignal, SEVERITY_OK, SEVERITY_LOW,
                    SEVERITY_MEDIUM, SEVERITY_HIGH, SEVERITY_CRITICAL)

logger = logging.getLogger(__name__)


def _home() -> Path | None:
    try:
        from forge import paths as _p  # type: ignore
        return _p.corvin_home()
    except Exception:  # noqa: BLE001
        return None


# ── Fiber: Session-Gesundheit (L1-L5 ACO) ────────────────────────────────────

class SessionFiber(NerveFiber):
    """Scannt alle Chat-Sessions auf Stalls, ACS-Fehler, WS-Instabilität.

    Wraps: anomaly_detector.scan_session + repair.repair_session
    Scope: alle Kanäle (web, discord, voice, cli)
    """
    fiber_id = "aco.session"
    fiber_version = "1.0.0"
    fiber_description = "Chat-Session-Gesundheit (L1-L5): Stalls, ACS-Fehler, WS-Instabilität"

    def scan(self) -> list[NerveSignal]:
        signals: list[NerveSignal] = []
        try:
            from .anomaly_detector import scan_session, SEVERITY_CRITICAL, SEVERITY_HIGH
            from .boot_healer import _find_all_workdirs, _discover_tenants
            for tenant_id in _discover_tenants():
                for workdir in _find_all_workdirs(tenant_id)[:20]:
                    try:
                        anomalies = scan_session(workdir)
                        for a in anomalies:
                            if a.severity in (SEVERITY_CRITICAL, SEVERITY_HIGH):
                                signals.append(NerveSignal(
                                    fiber_id=self.fiber_id,
                                    signal_type=f"session.{a.anomaly_class}",
                                    severity=a.severity,
                                    message=a.message,
                                    data={
                                        "workdir": str(workdir.name),
                                        "tenant_id": tenant_id,
                                        "suggestion": a.suggestion,
                                    },
                                    repair_hint=a.suggestion,
                                ))
                    except Exception:
                        pass
        except Exception as exc:
            logger.debug("[SessionFiber] Scan-Fehler: %s", exc)
        return signals

    def repair(self, signal: NerveSignal) -> NerveSignal | None:
        workdir_name = signal.data.get("workdir")
        tenant_id = signal.data.get("tenant_id", "_default")
        if not workdir_name:
            return None
        try:
            from .repair import repair_session
            from .boot_healer import _find_all_workdirs
            for workdir in _find_all_workdirs(tenant_id):
                if workdir.name == workdir_name:
                    result = repair_session(workdir, dry_run=False)
                    return NerveSignal(
                        fiber_id=self.fiber_id,
                        signal_type="session.repaired",
                        severity=SEVERITY_OK if result.convergence_reached else SEVERITY_HIGH,
                        message=(
                            f"Reparatur: delta_loss={result.delta_loss}, "
                            f"convergence={result.convergence_reached}"
                        ),
                        data={"workdir": workdir_name, "tenant_id": tenant_id},
                    )
        except Exception as exc:
            logger.debug("[SessionFiber] Repair-Fehler: %s", exc)
        return None


# ── Fiber: Engine-Bereitschaft ────────────────────────────────────────────────

class EngineFiber(NerveFiber):
    """Prüft Engine- und Voice-Bereitschaft (claude_code / hermes / TTS / STT).

    Wraps: engine_healer.run_readiness_check
    """
    fiber_id = "aco.engine"
    fiber_version = "1.0.0"
    fiber_description = "Engine + Voice Readiness: claude_code/hermes, TTS, STT"

    def scan(self) -> list[NerveSignal]:
        signals: list[NerveSignal] = []
        try:
            from .engine_healer import run_readiness_check
            from .boot_healer import _discover_tenants
            for tenant_id in _discover_tenants():
                try:
                    result = run_readiness_check(tenant_id)
                    if not result.engine_ok:
                        signals.append(NerveSignal(
                            fiber_id=self.fiber_id,
                            signal_type="engine.unavailable",
                            severity=SEVERITY_CRITICAL,
                            message=f"Keine Engine verfügbar (tenant={tenant_id}, "
                                    f"configured={result.engine_id}, "
                                    f"action={result.engine_action})",
                            data=result.to_audit_details(),
                            repair_hint="Ollama starten oder claude-binary prüfen",
                        ))
                    elif result.engine_action not in ("none", ""):
                        signals.append(NerveSignal(
                            fiber_id=self.fiber_id,
                            signal_type="engine.auto_healed",
                            severity=SEVERITY_OK,
                            message=f"Engine auto-geheilt: {result.engine_action} "
                                    f"(tenant={tenant_id})",
                            data=result.to_audit_details(),
                        ))
                    for warning in result.warnings:
                        signals.append(NerveSignal(
                            fiber_id=self.fiber_id,
                            signal_type="engine.warning",
                            severity=SEVERITY_HIGH,
                            message=warning,
                            data={"tenant_id": tenant_id},
                        ))
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("[EngineFiber] Scan-Fehler: %s", exc)
        return signals


# ── Fiber: System-Integrität (Immunsystem) ───────────────────────────────────

class IntegrityFiber(NerveFiber):
    """Schutz der System-Integrität: Audit-Chain, Config, Licensing, Compliance.

    Wraps: integrity_monitor.run_integrity_scan
    """
    fiber_id = "aco.integrity"
    fiber_version = "1.0.0"
    fiber_description = (
        "System-Integrität: Audit-Chain, Config-Tampering, License-Pubkey, "
        "Compliance-Gates (house_rules, consent, disclosure)"
    )

    def scan(self) -> list[NerveSignal]:
        signals: list[NerveSignal] = []
        try:
            from .integrity_monitor import run_integrity_scan
            from .boot_healer import _discover_tenants
            for tenant_id in _discover_tenants():
                try:
                    findings = run_integrity_scan(tenant_id)
                    for f in findings:
                        signals.append(NerveSignal(
                            fiber_id=self.fiber_id,
                            signal_type=f"integrity.{f.check_name}",
                            severity=f.severity,
                            message=f.message,
                            data={"tenant_id": tenant_id, **f.detail},
                            repair_hint=f"Audit-Log prüfen: {f.check_name}",
                        ))
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("[IntegrityFiber] Scan-Fehler: %s", exc)
        return signals


# ── Fiber: Installation-Bereitschaft ─────────────────────────────────────────

class InstallFiber(NerveFiber):
    """Prüft kritische Dependencies vom Pip-Install bis zur Laufzeit.

    Erkennt: fehlende Pakete, falsche Versionen, platform-inkompatible Deps.
    Wichtig: Muss auf frischer Installation (pip install corvinos) grün sein.
    """
    fiber_id = "install.deps"
    fiber_version = "1.0.0"
    fiber_description = "Installationsbereitschaft: kritische Pakete und Plattform-Kompatibilität"

    # (paketname, importname, min_version_check, required)
    _DEPS = [
        ("fastapi",     "fastapi",        None,    True),
        ("pydantic",    "pydantic",       None,    True),
        ("httpx",       "httpx",          None,    True),
        ("uvicorn",     "uvicorn",        None,    True),
        ("pyyaml",      "yaml",           None,    True),
        ("PyJWT",       "jwt",            None,    True),
        ("cryptography","cryptography",   None,    True),
        ("edge-tts",    "edge_tts",       None,    False),  # opt-in TTS
        ("openai",      "openai",         None,    False),  # opt-in STT/TTS
    ]

    def scan(self) -> list[NerveSignal]:
        import importlib as _il
        import sys

        signals: list[NerveSignal] = []
        for pkg_name, import_name, _, required in self._DEPS:
            spec = _il.util.find_spec(import_name)
            available = spec is not None
            if not available and required:
                signals.append(NerveSignal(
                    fiber_id=self.fiber_id,
                    signal_type="install.missing_required",
                    severity=SEVERITY_CRITICAL,
                    message=f"Pflichtpaket fehlt: {pkg_name} ({import_name} nicht importierbar)",
                    data={"package": pkg_name, "import_name": import_name},
                    repair_hint=f"pip install '{pkg_name}'",
                ))
            elif not available and not required:
                signals.append(NerveSignal(
                    fiber_id=self.fiber_id,
                    signal_type="install.missing_optional",
                    severity=SEVERITY_HIGH,
                    message=f"Optionales Paket fehlt: {pkg_name} — Feature eingeschränkt",
                    data={"package": pkg_name, "import_name": import_name},
                    repair_hint=f"pip install '{pkg_name}' für volle Funktionalität",
                    audit=False,  # Nicht jede fehlende Optional-Dep in Audit schreiben
                ))

        # Plattform-spezifische Prüfung: faster-whisper auf non-Windows
        if sys.platform != "win32":
            spec = _il.util.find_spec("faster_whisper")
            if spec is None:
                signals.append(NerveSignal(
                    fiber_id=self.fiber_id,
                    signal_type="install.missing_optional",
                    severity=SEVERITY_HIGH,
                    message="faster-whisper fehlt — lokale Spracheingabe deaktiviert",
                    data={"package": "faster-whisper", "platform": sys.platform},
                    repair_hint="pip install faster-whisper",
                    audit=False,
                ))

        return signals


# ── Fiber: Audit-Chain-Gesundheit ─────────────────────────────────────────────

class AuditChainFiber(NerveFiber):
    """Kontinuierliches Monitoring der Hash-Chain-Integrität (L16 GDPR Art. 30, 32).

    Überwacht: Kettenbrüche, fehlende Events, verdächtige Lücken.
    """
    fiber_id = "l16.audit_chain"
    fiber_version = "1.0.0"
    fiber_description = "L16 Audit-Chain: Hash-Integrität, GDPR Art. 30/32"

    def scan(self) -> list[NerveSignal]:
        signals: list[NerveSignal] = []
        try:
            from operator.bridges.shared.audit import verify_audit, audit_path
            audit_file = audit_path()
            if not audit_file.exists():
                return signals  # frische Installation
            ok, problems = verify_audit(audit_file)
            if not ok:
                signals.append(NerveSignal(
                    fiber_id=self.fiber_id,
                    signal_type="audit.chain_broken",
                    severity=SEVERITY_CRITICAL,
                    message=f"Audit-Chain gebrochen: {len(problems)} Problem(e)",
                    data={"problem_count": len(problems),
                          "first_problem": problems[0] if problems else {}},
                    repair_hint="Audit-Log-Datei auf Manipulation untersuchen",
                ))
            else:
                # Stichprobe: Letzter Event sollte kürzlich sein
                signals.append(NerveSignal(
                    fiber_id=self.fiber_id,
                    signal_type="audit.chain_ok",
                    severity=SEVERITY_OK,
                    message="Audit-Chain intakt",
                    data={},
                    audit=False,  # OK-Signals nicht in Audit schreiben
                ))
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("[AuditChainFiber] Fehler: %s", exc)
        return signals


# ── Fiber: Consent- und Disclosure-Gate (L16, EU AI Act Art. 50) ─────────────

class ComplianceFiber(NerveFiber):
    """EU AI Act Art. 50 + GDPR Art. 6/7 Consent-Gate-Monitoring.

    Prüft: Disclosure-Gate aktiv, Consent-Gate aktiv, House-Rules geladen.
    """
    fiber_id = "l16.compliance"
    fiber_version = "1.0.0"
    fiber_description = "EU AI Act Art. 50 + GDPR: Disclosure, Consent, House-Rules"

    def scan(self) -> list[NerveSignal]:
        signals: list[NerveSignal] = []
        try:
            import importlib.util
            for module_name, label in [
                ("operator.bridges.shared.house_rules", "house_rules"),
                ("operator.bridges.shared.consent",     "consent_gate"),
                ("operator.bridges.shared.disclosure",  "disclosure_gate"),
            ]:
                spec = importlib.util.find_spec(module_name)
                if spec is None:
                    signals.append(NerveSignal(
                        fiber_id=self.fiber_id,
                        signal_type=f"compliance.{label}_missing",
                        severity=SEVERITY_CRITICAL,
                        message=f"Compliance-Modul nicht importierbar: {module_name}",
                        data={"module": module_name, "label": label},
                        repair_hint=f"pip install corvinos oder Paket-Installation prüfen",
                    ))
        except Exception as exc:
            logger.debug("[ComplianceFiber] Fehler: %s", exc)
        return signals


# ── Fiber: System-Ressourcen (Disk + Memory) ─────────────────────────────────

class ResourceFiber(NerveFiber):
    """Disk- und Speicher-Headroom für das corvin_home. Disk-Full ist eine der
    häufigsten verdeckten Fehlerursachen (Writes scheitern still)."""
    fiber_id = "sys.resources"
    fiber_version = "1.0.0"
    fiber_description = "Disk- + RAM-Headroom (sys.resources): warnt bei Knappheit"

    def scan(self) -> list[NerveSignal]:
        out: list[NerveSignal] = []
        home = _home()
        try:
            if home is not None:
                du = shutil.disk_usage(str(home))
                free_mb = du.free // (1024 * 1024)
                if free_mb < 100:
                    sev = SEVERITY_CRITICAL
                elif free_mb < 500:
                    sev = SEVERITY_HIGH
                else:
                    sev = None
                if sev:
                    out.append(NerveSignal(
                        fiber_id=self.fiber_id, signal_type="resources.low_disk",
                        severity=sev, message=f"Wenig Speicherplatz: {free_mb} MB frei",
                        data={"free_mb": free_mb, "total_mb": du.total // (1024 * 1024)},
                        repair_hint="Speicher freigeben / L5 orphan_tmp + stale_lock sweep"))
        except OSError:
            pass
        try:  # Linux best-effort memory probe
            mi = Path("/proc/meminfo")
            if mi.is_file():
                kv = {}
                for line in mi.read_text().splitlines():
                    k, _, v = line.partition(":")
                    kv[k.strip()] = v.strip()
                avail = int(kv.get("MemAvailable", "0 kB").split()[0]) // 1024
                if 0 < avail < 200:
                    out.append(NerveSignal(
                        fiber_id=self.fiber_id, signal_type="resources.low_mem",
                        severity=SEVERITY_HIGH, message=f"Wenig RAM verfügbar: {avail} MB",
                        data={"avail_mb": avail},
                        repair_hint="Speicher-Last prüfen / Engine-Parallelität senken"))
        except (OSError, ValueError):
            pass
        return out


# ── Fiber: Log-Gesundheit (Fehlerrate im Debug-Log) ──────────────────────────

class LogHealthFiber(NerveFiber):
    """Tastet die letzten Zeilen von corvin.log ab und meldet Fehler-Spitzen —
    detaillierte, kontinuierliche Selbst-Beobachtung des Logging-Systems."""
    fiber_id = "aco.log_health"
    fiber_version = "1.0.0"
    fiber_description = "Fehlerrate im Debug-Log (aco.log_health): meldet Spitzen"
    _TAIL = 800

    def scan(self) -> list[NerveSignal]:
        home = _home()
        if home is None:
            return []
        log = home / "logs" / "corvin.log"
        if not log.is_file():
            return []
        try:
            with log.open("r", encoding="utf-8", errors="replace") as fh:
                tail = fh.readlines()[-self._TAIL:]
        except OSError:
            return []
        errs = sum(1 for ln in tail if "ERROR" in ln or "CRITICAL" in ln or "Traceback" in ln)
        if not tail:
            return []
        rate = errs / len(tail)
        if errs >= 50 or rate >= 0.25:
            sev = SEVERITY_HIGH if errs >= 50 else SEVERITY_MEDIUM
            return [NerveSignal(
                fiber_id=self.fiber_id, signal_type="log.error_spike", severity=sev,
                message=f"Erhöhte Fehlerrate im Log: {errs}/{len(tail)} Zeilen",
                data={"errors": errs, "window": len(tail)},
                repair_hint="ACO L4 Diagnose auf die jüngsten Tracebacks ansetzen")]
        return []


# ── Fiber: Config-Drift (nicht-parsebare Konfigurationen) ─────────────────────

class ConfigDriftFiber(NerveFiber):
    """Findet beschädigte ``*.config.json`` unter dem Home (Boot-Blocker). Detection
    only — die actuating Reparatur macht L5 corrupt_config_reset (opt-in)."""
    fiber_id = "config.drift"
    fiber_version = "1.0.0"
    fiber_description = "Beschädigte *.config.json (config.drift): meldet Parse-Fehler"

    def scan(self) -> list[NerveSignal]:
        import json as _json
        home = _home()
        if home is None:
            return []
        out: list[NerveSignal] = []
        for p in list(home.rglob("*.config.json"))[:200]:
            try:
                if p.is_file():
                    _json.loads(p.read_text(encoding="utf-8"))
            except _json.JSONDecodeError:
                out.append(NerveSignal(
                    fiber_id=self.fiber_id, signal_type="config.corrupt",
                    severity=SEVERITY_HIGH, message=f"Beschädigte Konfiguration: {p.name}",
                    data={"path": str(p.relative_to(home))},
                    repair_hint="L5 corrupt_config_reset (CORVIN_ACO_L5_RISKY=1)"))
            except OSError:
                pass
        return out


# ── Registry der Built-in Fibers (wird von nerve.py importiert) ───────────────

_BUILTIN_FIBERS: list[NerveFiber] = [
    InstallFiber(),       # Immer zuerst — frische Installation
    AuditChainFiber(),    # L16 Kern-Sicherheit
    ComplianceFiber(),    # EU AI Act / GDPR
    IntegrityFiber(),     # Immunsystem
    EngineFiber(),        # Engine + Voice
    SessionFiber(),       # Chat-Sessions
    ResourceFiber(),      # Disk + RAM Headroom (NEU)
    LogHealthFiber(),     # Log-Fehlerrate (NEU)
    ConfigDriftFiber(),   # Beschädigte Configs (NEU)
]
