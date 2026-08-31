"""ACO Integrity Monitor — Immunsystem für CorvinOS (ADR-0174, L16).

Proaktive Sicherheitsprüfungen bei jedem Boot-Healer-Zyklus:

  1. Audit-Chain-Integrität   — erkennt eingefügte/veränderte/gelöschte Events
  2. Config-Integrität        — erkennt Manipulation von tenant.corvin.yaml
  3. Engine-Sicherheit        — erkennt Redirect-Angriffe (Engine → fremder Host)
  4. License-Pubkey-Integrität— erkennt Manipulation des Signing-Keys (ADR-0093)
  5. Compliance-Gate-Integrität— prüft House-Rules und Consent-Gate auf Manipulation
  6. Alert-Eskalation         — schreibt bei CRITICAL einen Alert-File +Audit-Event

Prinzipien (Immunsystem-Analogie):
  * ERKENNEN  — kontinuierliches Monitoring ohne Betriebsunterbrechung
  * ISOLIEREN — Operator-Alert, Session nicht weiter bedienen
  * HEILEN    — Config-Rollback auf letzten verifizierten Stand
  * MERKEN    — Checksum-State im Tenant-Verzeichnis

Contract:
  * NEVER blockiert > 10 s.
  * NEVER stürzt ab — alle Exceptions degradieren gracefully.
  * NEVER schreibt PII in Alert-Files oder Audit-Details.
  * ALWAYS schreibt einen Audit-Event für jede CRITICAL-Erkennung.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Bekannte sichere Engines (keine externe URL-Umleitung)
_SAFE_ENGINES = frozenset({"claude_code", "hermes", "claude_code_local"})

# Erlaubte externe Hosts für Engine-URLs (nur Anthropic + eigene Infra)
_ALLOWED_ENGINE_HOSTS = frozenset({
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "api.anthropic.com",
})

# Wie viele Audit-Events wir am Ende der Chain prüfen (Performance-Kompromiss)
_AUDIT_TAIL_EVENTS = 100

# Pfad für Checksum-State relativ zum Tenant-Globalverzeichnis
_CHECKSUM_STATE_FILENAME = ".aco_integrity_checksums.json"

# Alert-Verzeichnis relativ zu corvin_home
_ALERTS_DIR = "alerts"


# ── Datentypen ────────────────────────────────────────────────────────────────

@dataclass
class IntegrityFinding:
    severity: str              # "CRITICAL" | "HIGH" | "MEDIUM"
    check_name: str
    message: str
    action_taken: str = "none"  # "none" | "alerted" | "reverted"
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "check_name": self.check_name,
            "message": self.message,
            "action_taken": self.action_taken,
            "detail": self.detail,
        }


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _corvin_home() -> Path:
    try:
        from forge import paths as _fp
        return Path(_fp.corvin_home())
    except Exception:
        return Path(os.environ.get("CORVIN_HOME", Path.home() / ".corvin"))


def _tenant_global_dir(tenant_id: str) -> Path:
    return _corvin_home() / "tenants" / tenant_id / "global"


def _sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        return None


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _load_checksum_state(tenant_id: str) -> dict:
    state_path = _tenant_global_dir(tenant_id) / _CHECKSUM_STATE_FILENAME
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_checksum_state(tenant_id: str, state: dict) -> None:
    state_path = _tenant_global_dir(tenant_id) / _CHECKSUM_STATE_FILENAME
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug("[ACO] Cannot save checksum state: %s", exc)


# ── Check 1: Audit-Chain-Integrität ──────────────────────────────────────────

def check_audit_chain_integrity(tenant_id: str) -> list[IntegrityFinding]:
    """Verifiziert die Tail-Abschnitt der Hash-Chain im Audit-Log.

    Ruft die zentrale `verify_audit()` Funktion auf, die bereits im
    Operator/Bridges-Stack vorhanden ist.  Nur CRITICAL wenn die Chain
    gebrochen ist — fehlendes Audit-File ist kein Fehler (frische Installation).
    """
    findings: list[IntegrityFinding] = []
    try:
        # ADR-0215 F5: the dotted `from operator.bridges.shared...` primary
        # attempt this used to have can never resolve (stdlib `operator`
        # always shadows the repo's operator/ directory) — dead code,
        # removed. parents[4] is the repo root (core/console/corvin_console/
        # aco/integrity_monitor.py -> aco -> corvin_console -> console ->
        # core -> repo root).
        bridge_shared = Path(__file__).resolve().parents[4] / "operator" / "bridges" / "shared"
        import sys
        if str(bridge_shared) not in sys.path:
            sys.path.insert(0, str(bridge_shared))
        from audit import verify_audit, audit_path  # type: ignore
    except ImportError:
        logger.debug("[ACO-Integrity] verify_audit not importable — skipping chain check")
        return findings

    try:
        audit_file = audit_path()
        if not audit_file.exists():
            return findings  # frische Installation — kein Fehler

        ok, problems = verify_audit(audit_file)
        if not ok:
            findings.append(IntegrityFinding(
                severity="CRITICAL",
                check_name="audit_chain_integrity",
                message=f"Audit-Chain-Verifikation fehlgeschlagen: {len(problems)} Problem(e)",
                detail={"problem_count": len(problems),
                        "first_problem": problems[0] if problems else {}},
            ))
            logger.critical(
                "[ACO-Integrity] CRITICAL: Audit-Chain gebrochen — %d Problem(e)! "
                "Mögliche Manipulation des Audit-Logs.",
                len(problems),
            )
    except Exception as exc:
        logger.debug("[ACO-Integrity] Audit-Chain-Check fehlgeschlagen: %s", exc, exc_info=True)

    return findings


# ── Check 2: Config-Integrität (tenant.corvin.yaml) ──────────────────────────

def check_config_integrity(tenant_id: str) -> list[IntegrityFinding]:
    """Erkennt unerwartete Änderungen an tenant.corvin.yaml.

    Bei der ersten Prüfung wird ein Checksum-Baseline gespeichert.
    Bei nachfolgenden Prüfungen wird verglichen. Eine Änderung ohne
    vorherige Boot-Healer-Initialisierung wird als HIGH eingestuft —
    sie könnte durch den Operator selbst oder durch einen Angriff entstanden sein.
    """
    findings: list[IntegrityFinding] = []

    config_path = _tenant_global_dir(tenant_id) / "tenant.corvin.yaml"
    if not config_path.exists():
        return findings

    current_hash = _sha256_file(config_path)
    if current_hash is None:
        return findings

    state = _load_checksum_state(tenant_id)
    stored_hash = state.get("tenant_config_sha256")

    if stored_hash is None:
        # Erstmalig — Baseline speichern
        state["tenant_config_sha256"] = current_hash
        state["tenant_config_ts"] = _now_iso()
        _save_checksum_state(tenant_id, state)
        logger.debug("[ACO-Integrity] Config-Checksum Baseline gesetzt für tenant=%s", tenant_id)
        return findings

    if current_hash != stored_hash:
        findings.append(IntegrityFinding(
            severity="HIGH",
            check_name="config_integrity",
            message=(
                f"tenant.corvin.yaml wurde seit letztem Zyklus geändert "
                f"(tenant={tenant_id})"
            ),
            detail={
                "stored_hash": stored_hash[:16] + "…",
                "current_hash": current_hash[:16] + "…",
                "stored_at": state.get("tenant_config_ts", "unknown"),
            },
        ))
        logger.warning(
            "[ACO-Integrity] Config-Änderung erkannt für tenant=%s — "
            "Checksum aktualisiert. Falls nicht erwartet: Audit-Log prüfen.",
            tenant_id,
        )
        # Neue Baseline
        state["tenant_config_sha256"] = current_hash
        state["tenant_config_ts"] = _now_iso()
        _save_checksum_state(tenant_id, state)

    return findings


# ── Check 3: Engine-Sicherheit ────────────────────────────────────────────────

def check_engine_config_safe(tenant_id: str) -> list[IntegrityFinding]:
    """Erkennt Engine-Redirect-Angriffe.

    Überprüft, ob der konfigurierte Engine-Wert auf einen bekannt sicheren
    Identifier verweist oder ob eine URL auf einen erlaubten Host zeigt.
    Ein Redirect auf einen unbekannten externen Host ist ein CRITICAL-Befund —
    Daten könnten an Dritte exfiltriert werden.
    """
    findings: list[IntegrityFinding] = []

    config_path = _tenant_global_dir(tenant_id) / "tenant.corvin.yaml"
    if not config_path.exists():
        return findings

    try:
        import yaml  # type: ignore
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        spec = data.get("spec", {})

        engine = spec.get("default_engine", "")
        if not isinstance(engine, str) or not engine.strip():
            return findings

        engine = engine.strip()

        # Sicherer Bezeichner → kein URL-Check nötig
        if engine in _SAFE_ENGINES:
            return findings

        # Könnte eine URL sein (z.B. http://external-host/v1) → Host extrahieren
        if "://" in engine or engine.startswith("http"):
            try:
                from urllib.parse import urlparse
                host = urlparse(engine).hostname or ""
                if host and host not in _ALLOWED_ENGINE_HOSTS:
                    findings.append(IntegrityFinding(
                        severity="CRITICAL",
                        check_name="engine_config_safe",
                        message=(
                            f"Engine-Konfiguration zeigt auf unbekannten externen Host: "
                            f"'{host}' (tenant={tenant_id})"
                        ),
                        detail={"engine_host": host},
                    ))
                    logger.critical(
                        "[ACO-Integrity] CRITICAL: Engine-Redirect auf unbekannten Host '%s' "
                        "— möglicher Exfiltrationsangriff! tenant=%s",
                        host, tenant_id,
                    )
            except Exception as exc:
                logger.debug("[ACO-Integrity] URL-Parse-Fehler: %s", exc)
        else:
            # Unbekannter nicht-URL Engine-Identifier — HIGH
            findings.append(IntegrityFinding(
                severity="HIGH",
                check_name="engine_config_safe",
                message=(
                    f"Unbekannter Engine-Identifier '{engine}' — "
                    f"nicht in der Safe-List (tenant={tenant_id})"
                ),
                detail={"engine": engine},
            ))
            logger.warning(
                "[ACO-Integrity] Unbekannter Engine-Identifier '%s' für tenant=%s",
                engine, tenant_id,
            )

    except Exception as exc:
        logger.debug("[ACO-Integrity] Engine-Config-Check fehlgeschlagen: %s", exc, exc_info=True)

    return findings


# ── Check 4: ACS-Fehlerrate als Angriffsindikator ────────────────────────────

def check_global_acs_anomaly(tenant_id: str) -> list[IntegrityFinding]:
    """Erkennt systemweite ACS-Anomalien als möglichen Prompt-Injection-Angriff.

    Prüft das globale Audit-Log auf gehäufte ACS-Fehler binnen kurzer Zeit.
    Ein Spike > 50% Fehlerrate in den letzten 20 ACS-Events deutet auf
    einen koordinierten Angriff oder schwerwiegenden Engine-Defekt hin.
    """
    findings: list[IntegrityFinding] = []
    try:
        # ADR-0215 F5: dead dotted primary removed (see
        # check_audit_chain_integrity() above for the same fix).
        bridge_shared = Path(__file__).resolve().parents[4] / "operator" / "bridges" / "shared"
        import sys
        if str(bridge_shared) not in sys.path:
            sys.path.insert(0, str(bridge_shared))
        from audit import audit_path  # type: ignore
        audit_file = audit_path()
        if not audit_file.exists():
            return findings

        lines = audit_file.read_text(encoding="utf-8", errors="replace").splitlines()
        acs_events: list[dict] = []
        for line in reversed(lines[-500:]):  # letzte 500 Zeilen
            try:
                evt = json.loads(line)
                if evt.get("event_type", "").startswith("acs."):
                    acs_events.append(evt)
                    if len(acs_events) >= 20:
                        break
            except Exception:
                continue

        if len(acs_events) < 5:
            return findings

        errors = [e for e in acs_events if e.get("severity") in ("ERROR", "CRITICAL")]
        error_rate = len(errors) / len(acs_events)

        if error_rate >= 0.5:
            findings.append(IntegrityFinding(
                severity="HIGH",
                check_name="global_acs_anomaly",
                message=(
                    f"Systemweite ACS-Fehlerrate {error_rate:.0%} "
                    f"({len(errors)}/{len(acs_events)} Events) — "
                    f"möglicher koordinierter Angriff (tenant={tenant_id})"
                ),
                detail={
                    "error_count": len(errors),
                    "total_acs_events": len(acs_events),
                    "error_rate": round(error_rate, 3),
                },
            ))

    except Exception as exc:
        logger.debug("[ACO-Integrity] ACS-Anomalie-Check fehlgeschlagen: %s", exc)

    return findings


# ── Alert-Eskalation ──────────────────────────────────────────────────────────

def alert_operator(findings: list[IntegrityFinding], tenant_id: str) -> None:
    """Schreibt einen Alert-File und einen Audit-Event für CRITICAL-Befunde.

    Alert-Files landen in ~/.corvin/alerts/integrity-TIMESTAMP.json und werden
    von der Operator-Konsole als Badge angezeigt. Alle Befunde werden auch als
    aco.integrity_alert Audit-Event in die Hash-Chain geschrieben.
    """
    critical = [f for f in findings if f.severity == "CRITICAL"]
    if not critical:
        return

    alerts_dir = _corvin_home() / _ALERTS_DIR
    try:
        alerts_dir.mkdir(parents=True, exist_ok=True)
        ts_safe = _now_iso().replace(":", "-").replace(".", "-")
        alert_path = alerts_dir / f"integrity-{ts_safe}-{tenant_id}.json"
        payload = {
            "ts": _now_iso(),
            "tenant_id": tenant_id,
            "source": "aco.integrity_monitor",
            "severity": "CRITICAL",
            "finding_count": len(critical),
            "findings": [f.to_dict() for f in critical],
        }
        alert_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.critical(
            "[ACO-Integrity] Alert geschrieben: %s (%d kritische Befunde)",
            alert_path, len(critical),
        )
    except Exception as exc:
        logger.debug("[ACO-Integrity] Alert-File-Schreiben fehlgeschlagen: %s", exc)

    # Audit-Event
    try:
        from corvin_console import audit as console_audit
        console_audit.action_performed(
            action="aco.integrity_alert",
            details={
                "tenant_id": tenant_id,
                "critical_count": len(critical),
                "checks_failed": [f.check_name for f in critical],
            },
        )
    except Exception:
        pass


# ── Öffentliche API ───────────────────────────────────────────────────────────

# ── Check 5: License-Pubkey-Integrität ───────────────────────────────────────

def check_license_pubkey_integrity() -> list[IntegrityFinding]:
    """Erkennt Manipulation des eingebetteten License-Signing-Keys.

    Der Pubkey ist in corvin_license/pubkey.pem eingebettet und sein SHA256
    im Quellcode fest verdrahtet (ADR-0093 M1.1). Wenn der Key ausgetauscht
    wurde, schlägt load_pubkey() mit RuntimeError fehl — das ist CRITICAL,
    da alle License-Prüfungen kompromittiert wären.
    """
    findings: list[IntegrityFinding] = []
    try:
        from corvin_license.verifier import load_pubkey  # type: ignore
        load_pubkey()
        # OK — kein Finding
    except RuntimeError as exc:
        msg = str(exc)
        findings.append(IntegrityFinding(
            severity="CRITICAL",
            check_name="license_pubkey_integrity",
            message=f"License-Pubkey manipuliert: {msg[:200]}",
            detail={"error": msg[:500]},
        ))
        logger.critical(
            "[ACO-Integrity] CRITICAL: License-Pubkey-Integrität verletzt — "
            "License-Verifikation kompromittiert! %s",
            msg[:200],
        )
    except FileNotFoundError:
        # Kein Pubkey = Free-Tier (kein installiertes corvin_license-Paket)
        pass
    except ImportError:
        pass  # License-Paket nicht installiert — kein Problem
    except Exception as exc:
        logger.debug("[ACO-Integrity] Pubkey-Check fehlgeschlagen: %s", exc)

    return findings


def check_license_file_presence() -> list[IntegrityFinding]:
    """Erkennt plötzliches Verschwinden der license.jwt-Datei.

    Wenn das System in einem vorherigen Zyklus eine License hatte und die
    Datei jetzt fehlt, ist das ein HIGH-Befund — könnte Löschangriff sein.
    """
    findings: list[IntegrityFinding] = []
    try:
        from corvin_license.verifier import license_file_path  # type: ignore
        lic_path = license_file_path()

        state = _load_checksum_state("_default")
        had_license = state.get("license_file_present", False)
        has_license = lic_path.exists()

        # State aktualisieren
        state["license_file_present"] = has_license
        _save_checksum_state("_default", state)

        if had_license and not has_license:
            findings.append(IntegrityFinding(
                severity="HIGH",
                check_name="license_file_presence",
                message=(
                    f"License-Datei verschwunden: {lic_path} "
                    "existierte im letzten Zyklus, fehlt jetzt"
                ),
                detail={"license_path": str(lic_path)},
            ))
            logger.warning(
                "[ACO-Integrity] License-Datei fehlt: %s — war im letzten Zyklus vorhanden",
                lic_path,
            )

    except ImportError:
        pass  # License-Paket nicht installiert
    except Exception as exc:
        logger.debug("[ACO-Integrity] License-Presence-Check fehlgeschlagen: %s", exc)

    return findings


# ── Check 6: Compliance-Gate-Integrität ──────────────────────────────────────

# Kritische Compliance-Dateien, deren Checksum überwacht wird.
# Pfade sind relativ zum Repo-Root (werden beim ersten Zyklus berechnet).
_COMPLIANCE_FILES = [
    # House-Rules L44 — darf nie deaktiviert werden
    ("operator/bridges/shared/house_rules.py", "house_rules"),
    # Consent Gate L16
    ("operator/bridges/shared/consent.py", "consent_gate"),
    # Disclosure (EU AI Act Art. 50)
    ("operator/bridges/shared/disclosure.py", "disclosure_gate"),
    # Audit-Chain (L16)
    ("operator/bridges/shared/audit.py", "bridge_audit"),
    # Path-Gate (L10)
    ("operator/bridges/shared/path_gate.py", "path_gate"),
]


def _repo_root() -> Path | None:
    """Findet den Repo-Root via git oder durch Heuristik."""
    try:
        import subprocess
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=3,
            cwd=str(Path(__file__).parent),
        )
        if r.returncode == 0:
            return Path(r.stdout.strip())
    except Exception:
        pass
    # Heuristik: 5 Ebenen nach oben (aco/ → corvin_console/ → console/ → core/ → CorvinOS/)
    candidate = Path(__file__).resolve().parents[4]
    if (candidate / "operator").is_dir():
        return candidate
    return None


def check_compliance_gate_integrity() -> list[IntegrityFinding]:
    """Erkennt Manipulation kritischer Compliance-Gate-Dateien.

    Berechnet SHA256 der Schlüssel-Compliance-Dateien beim ersten Zyklus
    als Baseline. Jede nachfolgende Abweichung ist ein HIGH-Befund.
    GDPR Art. 5/17 und EU AI Act Art. 50 sind strukturell verletzt, wenn
    diese Dateien verändert wurden ohne Audit-Spur.
    """
    findings: list[IntegrityFinding] = []
    root = _repo_root()
    if root is None:
        return findings

    state = _load_checksum_state("_default")
    compliance_hashes: dict = state.get("compliance_file_hashes", {})
    changed_hashes: dict = {}
    baseline_updated = False

    for rel_path, key in _COMPLIANCE_FILES:
        abs_path = root / rel_path
        if not abs_path.exists():
            continue

        current_hash = _sha256_file(abs_path)
        if current_hash is None:
            continue

        stored = compliance_hashes.get(key)
        if stored is None:
            # Erste Messung — Baseline
            compliance_hashes[key] = current_hash
            baseline_updated = True
        elif stored != current_hash:
            changed_hashes[key] = rel_path
            findings.append(IntegrityFinding(
                severity="HIGH",
                check_name="compliance_gate_integrity",
                message=(
                    f"Compliance-Gate-Datei verändert: {rel_path} "
                    f"({key}) — unerwartete Änderung erkannt"
                ),
                detail={
                    "file": rel_path,
                    "stored_hash": stored[:16] + "…",
                    "current_hash": current_hash[:16] + "…",
                },
            ))
            logger.warning(
                "[ACO-Integrity] Compliance-Datei geändert: %s (%s) — "
                "prüfe ob Änderung autorisiert ist.",
                rel_path, key,
            )
            # Neue Baseline setzen (nach Alert)
            compliance_hashes[key] = current_hash
            baseline_updated = True

    if baseline_updated:
        state["compliance_file_hashes"] = compliance_hashes
        _save_checksum_state("_default", state)

    return findings


# ── Öffentliche API ───────────────────────────────────────────────────────────

INTEGRITY_CHECKS = [
    check_audit_chain_integrity,
    check_config_integrity,
    check_engine_config_safe,
    check_global_acs_anomaly,
    lambda t: check_license_pubkey_integrity(),
    lambda t: check_license_file_presence(),
    lambda t: check_compliance_gate_integrity(),
]


def run_integrity_scan(tenant_id: str = "_default") -> list[IntegrityFinding]:
    """Führt alle Integritätsprüfungen für einen Tenant aus.

    Entry-Point, der vom Boot-Healer bei jedem Zyklus aufgerufen wird —
    VOR dem Engine-Check und dem Session-Scan (Step 0).

    Bei CRITICAL-Befunden wird automatisch alert_operator() aufgerufen.
    Alle Checks sind fail-safe: ein fehlerhafter Check liefert kein Finding.
    """
    findings: list[IntegrityFinding] = []

    for check_fn in INTEGRITY_CHECKS:
        try:
            result = check_fn(tenant_id)
            findings.extend(result)
        except Exception as exc:
            logger.debug(
                "[ACO-Integrity] Check %s fehlgeschlagen: %s",
                check_fn.__name__, exc, exc_info=True,
            )

    critical_count = sum(1 for f in findings if f.severity == "CRITICAL")
    high_count = sum(1 for f in findings if f.severity == "HIGH")

    if findings:
        logger.info(
            "[ACO-Integrity] Scan abgeschlossen tenant=%s — %d CRITICAL, %d HIGH, %d total",
            tenant_id, critical_count, high_count, len(findings),
        )
    else:
        logger.debug("[ACO-Integrity] Scan clean tenant=%s — keine Befunde", tenant_id)

    # Operator alarmieren wenn CRITICAL
    if critical_count > 0:
        try:
            alert_operator(findings, tenant_id)
        except Exception as exc:
            logger.debug("[ACO-Integrity] Alert-Eskalation fehlgeschlagen: %s", exc)

    return findings


def get_alert_count() -> int:
    """Gibt die Anzahl ungelesener Integrity-Alerts zurück (für Badge in der UI)."""
    try:
        alerts_dir = _corvin_home() / _ALERTS_DIR
        if not alerts_dir.exists():
            return 0
        return sum(1 for f in alerts_dir.iterdir() if f.name.startswith("integrity-"))
    except Exception:
        return 0
