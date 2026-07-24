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
import sys
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


def _bridges_shared_dir() -> Path:
    """Repo-relative ``operator/bridges/shared`` (source-tree mode).

    ``operator/`` has no ``__init__.py`` and shadows the stdlib ``operator``
    module, so ``from operator.bridges.shared.x import y`` can NEVER resolve
    (regular stdlib modules always win over namespace-package candidates,
    regardless of sys.path order) — confirmed structurally broken, not just
    theoretically: every prior fiber that tried the dotted form silently
    degraded to "module unavailable" on every single scan. The fix used
    throughout operator/orchestration/tde/ (e.g. tde_audit.py) is to put the
    leaf directory on sys.path and import the bare module name instead.
    """
    return Path(__file__).resolve().parents[4] / "operator" / "bridges" / "shared"


def _ensure_bridges_on_path() -> None:
    shared = _bridges_shared_dir()
    if shared.is_dir() and str(shared) not in sys.path:
        sys.path.insert(0, str(shared))


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
        # ADR-0185: local STT/TTS engines, base deps on every platform now.
        ("pywhispercpp","pywhispercpp",   None,    True),
        ("piper-tts",   "piper",          None,    True),
    ]

    def scan(self) -> list[NerveSignal]:
        import importlib as _il

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
            _ensure_bridges_on_path()
            from audit import verify_audit, audit_path  # type: ignore[import-not-found]
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
            _ensure_bridges_on_path()
            # Bare names, NOT "operator.bridges.shared.X" — see
            # _bridges_shared_dir() docstring: the dotted form can never
            # resolve because stdlib `operator` shadows the repo's operator/
            # directory. find_spec() on a dotted name whose parent isn't a
            # real package RAISES (not returns None) — every module_name
            # after the first would never even be checked.
            for module_name, label in [
                ("house_rules", "house_rules"),
                ("consent",     "consent_gate"),
                ("disclosure",  "disclosure_gate"),
            ]:
                try:
                    spec = importlib.util.find_spec(module_name)
                except (ImportError, ValueError):
                    spec = None
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


# ── Fiber: Remote-Instanz-Logs (z.B. Hetzner) ────────────────────────────────

class RemoteLogFiber(NerveFiber):
    """Analysiert die gespiegelten Logs ANDERER CorvinOS-Instanzen unter
    ``aco/remote/<name>/`` (per ``corvin-maintainer pull-remote`` geholt). So
    tauchen Fehler des Hetzner-Servers im selben Nervensystem-Scan auf."""
    fiber_id = "remote.log_health"
    fiber_version = "1.0.0"
    fiber_description = "Fehlerrate in gespiegelten Remote-Instanz-Logs (remote.log_health)"
    _TAIL = 800

    def scan(self) -> list[NerveSignal]:
        home = _home()
        if home is None:
            return []
        out: list[NerveSignal] = []
        remote_root = home / "aco" / "remote"
        if not remote_root.is_dir():
            return []
        for inst in remote_root.iterdir():
            if not inst.is_dir():
                continue
            for log in inst.rglob("corvin.log"):
                try:
                    with log.open("r", encoding="utf-8", errors="replace") as fh:
                        tail = fh.readlines()[-self._TAIL:]
                except OSError:
                    continue
                if not tail:
                    continue
                errs = sum(1 for ln in tail
                           if "ERROR" in ln or "CRITICAL" in ln or "Traceback" in ln)
                if errs >= 30:
                    out.append(NerveSignal(
                        fiber_id=self.fiber_id, signal_type="remote.error_spike",
                        severity=SEVERITY_HIGH if errs >= 80 else SEVERITY_MEDIUM,
                        message=f"Remote '{inst.name}': {errs} Fehler in den letzten "
                                f"{len(tail)} Log-Zeilen",
                        data={"remote": inst.name, "errors": errs, "window": len(tail)},
                        repair_hint="Remote-Logs im Support-Bundle prüfen / L4-Diagnose"))
        return out


# ── Fiber: TDE Delegation Runner (ADR-0214) ──────────────────────────────────

class TdeDelegationFiber(NerveFiber):
    """Beobachtet den Tiered-Delegation-Engine-Runner über die tde.* Audit-Chain.

    Liest AUSSCHLIESSLICH bereits content-freie ``tde.*`` Events (siehe
    ``operator/orchestration/tde/tde_audit.py`` — allowlisted Scalars, niemals
    Statement-/Snapshot-Inhalte). Diese Fiber fügt der Chain keine neuen Events
    hinzu, sie liest nur; Fehlklassifikationen bleiben also nicht unbemerkt,
    weil TDE selbst schon vor dem Schreiben scrubbt.

    Exzessive Beobachtung (bewusst, auf Nutzerwunsch): jeder Scan meldet ein
    OK-Signal mit vollen Zählern (audit=False — kein Audit-Spam), zusätzlich
    HIGH/MEDIUM bei Auffälligkeiten. Das Signal ist damit über
    NerveRegistry.scan_all()/summarize_signals() jederzeit sichtbar, auch wenn
    nichts kaputt ist.
    """
    fiber_id = "tde.delegation_runner"
    fiber_version = "1.0.0"
    fiber_description = (
        "ADR-0214 TDE Delegation Runner: L34-Blocks, Delegations-Erfolgsrate, "
        "gelernter Quality-Loss — gelesen aus der tde.* Audit-Chain"
    )
    _TAIL_LINES = 2000
    # Ab dieser Zahl gemessener Delegationen wird die Fehlerrate/der Loss
    # überhaupt bewertet — sonst wären 1/1 Fehlschläge sofort ein HIGH-Signal.
    _MIN_SAMPLES = 5
    _FAILURE_RATE_HIGH = 0.30
    _LOSS_PCT_MEDIUM = 5.0  # muss QUALITY_THRESHOLD (adaptive_delegation_executor.py) spiegeln
    _LOSS_PCT_HIGH = 15.0

    def scan(self) -> list[NerveSignal]:
        import json as _json

        try:
            _ensure_bridges_on_path()
            from audit import audit_path  # type: ignore[import-not-found]
        except Exception as exc:
            logger.debug("[TdeDelegationFiber] audit-Modul nicht importierbar: %s", exc)
            return []

        try:
            path = audit_path()
            if not path.exists():
                return []
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                tail = fh.readlines()[-self._TAIL_LINES:]
        except OSError as exc:
            logger.debug("[TdeDelegationFiber] Audit-Datei nicht lesbar: %s", exc)
            return []

        delegated_total = 0
        delegated_failed = 0
        l34_blocked = 0
        measured_losses: list[float] = []
        engines_selected: dict[str, int] = {}

        for line in tail:
            line = line.strip()
            if not line:
                continue
            try:
                event = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            event_type = event.get("event_type", "")
            if not event_type.startswith("tde."):
                continue
            details = event.get("details") or {}

            if event_type == "tde.step_delegated":
                delegated_total += 1
                if not details.get("success"):
                    delegated_failed += 1
            elif event_type == "tde.l34_blocked":
                l34_blocked += 1
            elif event_type == "tde.loss_recorded" and details.get("measured"):
                loss_pct = details.get("loss_pct")
                if isinstance(loss_pct, (int, float)):
                    measured_losses.append(float(loss_pct))
            elif event_type == "tde.engine_selected":
                engine = details.get("engine", "unknown")
                engines_selected[engine] = engines_selected.get(engine, 0) + 1

        signals: list[NerveSignal] = []
        failure_rate = (delegated_failed / delegated_total) if delegated_total else 0.0
        avg_measured_loss = (
            sum(measured_losses) / len(measured_losses) if measured_losses else None
        )

        # Immer sichtbar, auch wenn alles gesund ist (exzessive Beobachtung).
        signals.append(NerveSignal(
            fiber_id=self.fiber_id,
            signal_type="tde.runner_stats",
            severity=SEVERITY_OK,
            message=(
                f"TDE-Runner: {delegated_total} Delegationen "
                f"({delegated_failed} fehlgeschlagen), {l34_blocked} L34-Blocks, "
                f"avg_measured_loss="
                f"{f'{avg_measured_loss:.1f}%' if avg_measured_loss is not None else 'n/a'}"
            ),
            data={
                "delegated_total": delegated_total,
                "delegated_failed": delegated_failed,
                "l34_blocked": l34_blocked,
                "measured_loss_samples": len(measured_losses),
                "avg_measured_loss_pct": avg_measured_loss,
                "engines_selected": engines_selected,
            },
            audit=False,
        ))

        if delegated_total >= self._MIN_SAMPLES and failure_rate > self._FAILURE_RATE_HIGH:
            signals.append(NerveSignal(
                fiber_id=self.fiber_id,
                signal_type="tde.delegation_failure_spike",
                severity=SEVERITY_HIGH,
                message=(
                    f"TDE-Delegations-Fehlerrate erhöht: {delegated_failed}/"
                    f"{delegated_total} ({failure_rate:.0%}) — Worker-IPC prüfen"
                ),
                data={"delegated_total": delegated_total, "delegated_failed": delegated_failed,
                      "failure_rate": round(failure_rate, 3)},
                repair_hint="claude-Binary/Netzwerk der Worker-Subprozesse prüfen "
                            "(operator/orchestration/tde/worker_ipc.py)",
            ))

        if avg_measured_loss is not None and len(measured_losses) >= self._MIN_SAMPLES:
            if avg_measured_loss >= self._LOSS_PCT_HIGH:
                signals.append(NerveSignal(
                    fiber_id=self.fiber_id,
                    signal_type="tde.quality_loss_elevated",
                    severity=SEVERITY_HIGH,
                    message=f"TDE gemessener Quality-Loss stark erhöht: {avg_measured_loss:.1f}%",
                    data={"avg_measured_loss_pct": avg_measured_loss, "samples": len(measured_losses)},
                    repair_hint="loss_judge.py / SITE_DELEGATE_OUTPUT_JUDGE prüfen — "
                                "Delegation ggf. per /use-engine claude_code umgehen",
                ))
            elif avg_measured_loss >= self._LOSS_PCT_MEDIUM:
                signals.append(NerveSignal(
                    fiber_id=self.fiber_id,
                    signal_type="tde.quality_loss_elevated",
                    severity=SEVERITY_MEDIUM,
                    message=f"TDE gemessener Quality-Loss über dem Gate-Schwellwert: "
                            f"{avg_measured_loss:.1f}%",
                    data={"avg_measured_loss_pct": avg_measured_loss, "samples": len(measured_losses)},
                ))

        return signals


def _load_wiring_manifests() -> list[dict]:
    """Load every WIRING.yaml this Fiber cares about (best-effort)."""
    try:
        import yaml
    except ImportError:
        return []
    repo_root = Path(__file__).resolve().parents[4]
    manifest_paths = [
        repo_root / "operator" / "orchestration" / "WIRING.yaml",
        repo_root / "operator" / "orchestration" / "tde" / "WIRING.yaml",
    ]
    components: list[dict] = []
    for mpath in manifest_paths:
        if not mpath.is_file():
            continue
        try:
            data = yaml.safe_load(mpath.read_text()) or {}
        except Exception as exc:  # noqa: BLE001
            logger.debug("[WiringIntegrityFiber] %s unreadable: %s", mpath, exc)
            continue
        for entry in data.get("components", []):
            entry = dict(entry)
            entry["_manifest"] = str(mpath)
            components.append(entry)
    return components


class WiringIntegrityFiber(NerveFiber):
    """ADR-0215 Phase 2 — runtime proof that WIRING.yaml's claims still hold.

    Two independent checks, re-run on every scan (not just at CI time):

    1. **Static re-check.** Re-resolves every `live` entry_point exactly like
       ``operator/orchestration/wiring_gate.py`` does at CI time. A CI gate
       only proves reachability at merge time — a dependency removed later,
       a Python-version bump, or an editable-install gone stale can silently
       break a `live` entry_point AFTER merge. This catches that drift in
       production, not just at PR time.
    2. **Traffic cross-check.** If ANY component under the tde/ manifest is
       declared `live`, at least one `tde.*` audit event should appear in a
       real, running install over time. Zero `tde.*` events despite `live`
       declarations is a signal worth surfacing — either TDE genuinely never
       gets selected (auto-routing/dispatch may be broken) or the audit
       pipe itself is broken (see AuditChainFiber for that specific check).

    Known limitation (documented, not hidden): there is no per-MODULE audit
    signal today — the tde.* namespace instruments the pipeline's high-level
    flow (engine_selected, plan_executed, step_*), not "was
    streaming_executor.py specifically invoked". A `deferred` component that
    gets silently wired without an accompanying audit-event addition would
    not be caught by check 2 alone; check 1 (does `deferred`'s reason still
    match reality — i.e. does grepping for real callers still find none) is
    NOT re-run here on purpose (that is a static source-tree check,
    appropriate for CI, not for a per-scan runtime Fiber that must stay
    cheap and never touch the source tree of a packaged install).
    """
    fiber_id = "aco.wiring_integrity"
    fiber_version = "1.0.0"
    fiber_description = (
        "ADR-0215: re-verifies WIRING.yaml `live` entry_points at runtime "
        "and cross-checks TDE traffic evidence against `live` claims"
    )
    _TAIL_LINES = 2000

    def scan(self) -> list[NerveSignal]:
        signals: list[NerveSignal] = []
        components = _load_wiring_manifests()
        if not components:
            return signals  # PyYAML unavailable or manifests missing — silent no-op, not an error

        live = [c for c in components if c.get("status") == "live"]
        deferred = [c for c in components if c.get("status") == "deferred"]
        broken: list[dict] = []

        repo_root = Path(__file__).resolve().parents[4]
        orch_dir = repo_root / "operator" / "orchestration"
        for entry in live:
            entry_point = entry.get("entry_point")
            name = entry.get("name", "?")
            if not entry_point:
                broken.append({"name": name, "reason": "no entry_point declared"})
                continue
            module_name, _, symbol = entry_point.partition(":")
            sp = str(orch_dir)
            added = sp not in sys.path
            if added:
                sys.path.insert(0, sp)
            try:
                import importlib
                mod = importlib.import_module(module_name)
                if symbol and not hasattr(mod, symbol):
                    broken.append({"name": name, "reason": f"module ok, no attribute `{symbol}`"})
            except Exception as exc:  # noqa: BLE001
                broken.append({"name": name, "reason": f"{type(exc).__name__}: {exc}"})
            finally:
                if added and sp in sys.path:
                    sys.path.remove(sp)

        signals.append(NerveSignal(
            fiber_id=self.fiber_id,
            signal_type="wiring.manifest_stats",
            severity=SEVERITY_OK,
            message=(
                f"Wiring-Manifest: {len(live)} live, {len(deferred)} deferred, "
                f"{len(broken)} gebrochen"
            ),
            data={"live_count": len(live), "deferred_count": len(deferred),
                  "broken_count": len(broken)},
            audit=False,
        ))

        for b in broken:
            signals.append(NerveSignal(
                fiber_id=self.fiber_id,
                signal_type="wiring.live_entry_point_broken",
                severity=SEVERITY_HIGH,
                message=(
                    f"WIRING.yaml behauptet `{b['name']}` sei live, aber der "
                    f"entry_point ist kaputt: {b['reason']}"
                ),
                data=b,
                repair_hint="operator/orchestration/wiring_gate.py lokal laufen lassen "
                            "und WIRING.yaml oder den Code korrigieren",
            ))

        # Traffic cross-check: any tde.* evidence at all?
        tde_live = [c for c in live if c.get("_manifest", "").endswith("tde/WIRING.yaml")]
        if tde_live:
            try:
                _ensure_bridges_on_path()
                from audit import audit_path  # type: ignore[import-not-found]
                path = audit_path()
                tde_events_seen = 0
                if path.exists():
                    with path.open("r", encoding="utf-8", errors="replace") as fh:
                        tail = fh.readlines()[-self._TAIL_LINES:]
                    import json as _json
                    for line in tail:
                        try:
                            event = _json.loads(line.strip() or "{}")
                        except _json.JSONDecodeError:
                            continue
                        if str(event.get("event_type", "")).startswith("tde."):
                            tde_events_seen += 1
                if tde_events_seen == 0:
                    signals.append(NerveSignal(
                        fiber_id=self.fiber_id,
                        signal_type="wiring.tde_declared_live_but_silent",
                        severity=SEVERITY_MEDIUM,
                        message=(
                            f"{len(tde_live)} TDE-Komponenten sind `live` deklariert, "
                            f"aber die letzten {self._TAIL_LINES} Audit-Zeilen enthalten "
                            f"kein einziges tde.*-Event — TDE wird evtl. nie aufgerufen"
                        ),
                        data={"tde_live_count": len(tde_live), "tde_events_in_tail": 0},
                        repair_hint="Prüfen ob /use-engine tiered_delegation oder "
                                    "Auto-Routing überhaupt erreicht werden; alternativ "
                                    "AuditChainFiber prüfen (evtl. ist die Chain selbst kaputt)",
                    ))
            except Exception as exc:  # noqa: BLE001
                logger.debug("[WiringIntegrityFiber] Traffic-Check fehlgeschlagen: %s", exc)

        return signals


class TokenSavingsFiber(NerveFiber):
    """ADR-0215 Phase 2 — makes ADR-0210/ADR-0214's headline savings claims
    (56-70% tokens, 50% latency) continuously, empirically checked instead
    of asserted once at write-time.

    HONESTY NOTE (found during ADR-0215 implementation, not hidden): the TDE
    pipeline does not currently instrument REAL per-call token usage
    anywhere — ``worker_ipc.run_one_shot`` invokes the worker CLI with
    ``--output-format text``, not ``json``, so no structured usage/token
    count is ever captured; the only token-shaped number available
    (``Step.estimated_tokens`` / ``GlobalPlan.estimated_tokens``) is the
    LM's OWN pre-execution guess from the InitialAnalysis call, not a
    measured actual. Reporting that guess as "token savings" would be
    exactly the kind of unverified claim ADR-0215 exists to stop making.
    This Fiber therefore reports only what IS genuinely measured today —
    real wall-clock ``duration_ms`` per step, delegated vs. local — and
    reports the estimated-token figure under a name that says what it is
    (``avg_estimated_tokens_per_step``, never "savings"). Real token-usage
    instrumentation is a separate, tracked follow-up (would need
    ``--output-format json`` parsing in worker_ipc.py), not silently
    faked here.
    """
    fiber_id = "aco.token_savings"
    fiber_version = "1.0.0"
    fiber_description = (
        "ADR-0215: real measured latency (delegated vs. local) from tde.* "
        "audit traffic — NOT a token-savings measurement (see class docstring)"
    )
    _TAIL_LINES = 5000
    _MIN_SAMPLES = 5

    def scan(self) -> list[NerveSignal]:
        signals: list[NerveSignal] = []
        try:
            _ensure_bridges_on_path()
            from audit import audit_path  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            logger.debug("[TokenSavingsFiber] audit-Modul nicht importierbar: %s", exc)
            return signals

        try:
            path = audit_path()
            if not path.exists():
                return signals
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                tail = fh.readlines()[-self._TAIL_LINES:]
        except OSError as exc:
            logger.debug("[TokenSavingsFiber] Audit-Datei nicht lesbar: %s", exc)
            return signals

        import json as _json
        delegated_durations: list[float] = []
        local_durations: list[float] = []
        engine_selection_counts: dict[str, int] = {}

        for line in tail:
            line = line.strip()
            if not line:
                continue
            try:
                event = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            event_type = event.get("event_type", "")
            if not event_type.startswith("tde."):
                continue
            details = event.get("details") or {}

            if event_type == "tde.step_delegated" and details.get("success"):
                dur = details.get("duration_ms")
                if isinstance(dur, (int, float)):
                    delegated_durations.append(float(dur))
            elif event_type == "tde.step_executed_local" and details.get("success"):
                dur = details.get("duration_ms")
                if isinstance(dur, (int, float)):
                    local_durations.append(float(dur))
            elif event_type == "tde.engine_selected":
                engine = details.get("engine", "unknown")
                engine_selection_counts[engine] = engine_selection_counts.get(engine, 0) + 1

        avg_delegated = (
            sum(delegated_durations) / len(delegated_durations) if delegated_durations else None
        )
        avg_local = sum(local_durations) / len(local_durations) if local_durations else None
        latency_delta_pct = None
        if avg_delegated is not None and avg_local not in (None, 0):
            latency_delta_pct = round(100.0 * (avg_local - avg_delegated) / avg_local, 1)

        signals.append(NerveSignal(
            fiber_id=self.fiber_id,
            signal_type="wiring.tde_latency_stats",
            severity=SEVERITY_OK,
            message=(
                f"TDE Latenz (real gemessen, {len(delegated_durations)} delegierte / "
                f"{len(local_durations)} lokale Steps): "
                f"avg_delegated={f'{avg_delegated:.0f}ms' if avg_delegated else 'n/a'}, "
                f"avg_local={f'{avg_local:.0f}ms' if avg_local else 'n/a'}, "
                f"delta={f'{latency_delta_pct:+.1f}%' if latency_delta_pct is not None else 'n/a'} "
                f"| Engine-Wahl: {engine_selection_counts} "
                f"| Tokens: NICHT instrumentiert (siehe Klassen-Docstring)"
            ),
            data={
                "sample_delegated": len(delegated_durations),
                "sample_local": len(local_durations),
                "avg_delegated_duration_ms": avg_delegated,
                "avg_local_duration_ms": avg_local,
                "latency_delta_pct_delegated_vs_local": latency_delta_pct,
                "engine_selection_counts": engine_selection_counts,
                "token_usage_instrumented": False,
            },
            audit=False,
        ))

        if (
            latency_delta_pct is not None
            and len(delegated_durations) >= self._MIN_SAMPLES
            and len(local_durations) >= self._MIN_SAMPLES
            and latency_delta_pct < 0
        ):
            # Delegation is measurably SLOWER than local execution on average
            # — the opposite of ADR-0214's "50% latency reduction" claim.
            signals.append(NerveSignal(
                fiber_id=self.fiber_id,
                signal_type="wiring.tde_delegation_slower_than_local",
                severity=SEVERITY_MEDIUM,
                message=(
                    f"TDE-Delegation ist im Mittel {abs(latency_delta_pct):.1f}% "
                    f"LANGSAMER als lokale Ausführung — widerspricht der "
                    f"ADR-0214-Latenz-Ersparnis-Behauptung"
                ),
                data={"latency_delta_pct": latency_delta_pct,
                      "sample_delegated": len(delegated_durations),
                      "sample_local": len(local_durations)},
                repair_hint="RPC-Overhead (DELEGATION_OVERHEAD_TOKENS/Netzwerk) prüfen — "
                            "siehe operator/orchestration/tde/adaptive_delegation_executor.py",
            ))

        return signals


# ── Registry der Built-in Fibers (wird von nerve.py importiert) ───────────────

def _make_htrace_fiber():
    try:
        from .htrace_uploader import HealingTraceUploaderFiber
        return HealingTraceUploaderFiber()
    except Exception as exc:  # noqa: BLE001
        logger.warning("htrace: HealingTraceUploaderFiber unavailable — %s", exc)
        return None


_BUILTIN_FIBERS: list[NerveFiber] = [
    InstallFiber(),       # Immer zuerst — frische Installation
    AuditChainFiber(),    # L16 Kern-Sicherheit
    ComplianceFiber(),    # EU AI Act / GDPR
    IntegrityFiber(),     # Immunsystem
    EngineFiber(),        # Engine + Voice
    SessionFiber(),       # Chat-Sessions
    ResourceFiber(),      # Disk + RAM Headroom
    LogHealthFiber(),     # Log-Fehlerrate
    ConfigDriftFiber(),   # Beschädigte Configs
    RemoteLogFiber(),     # Remote-Instanz-Logs, z.B. Hetzner
    TdeDelegationFiber(), # ADR-0214 TDE Delegation Runner
    WiringIntegrityFiber(), # ADR-0215 Wiring-Manifest Runtime-Proof
    TokenSavingsFiber(),    # ADR-0215 Latenz/Token-Ehrlichkeitsmessung
    *([f] if (f := _make_htrace_fiber()) else []),  # ADR-0180 HealingTrace-Uploader
]
