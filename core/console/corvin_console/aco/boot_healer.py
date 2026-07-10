"""ACO Boot Healer — autonomous startup + periodic self-repair (ADR-0174).

Runs as a non-blocking asyncio background task.  On every cycle it:
  1. Discovers ALL session workdirs across ALL channels for every known tenant
     via filesystem scan (chat_debug.jsonl presence).  Covers web, discord,
     voice, cli, and any future bridge — NOT limited to chat_runtime.list_sessions()
     which only returns web sessions.
  2. Scans each workdir for HIGH/CRITICAL anomalies.
  3. Applies Layer 5 self-repair to any session with findings.
  4. Writes an aco.boot_heal event to the global audit chain.

Contract:
  * NEVER blocks the Gateway lifespan.
  * NEVER propagates exceptions — all errors are silently logged.
  * NEVER modifies code, restarts processes, or touches the L16 audit chain
    beyond a single aco.boot_heal event per cycle.
  * Stops cleanly when the asyncio task is cancelled (on Gateway shutdown).

Usage (in gateway lifespan)::

    from corvin_console.aco.boot_healer import start_boot_healer
    healer_task = start_boot_healer()
    yield
    healer_task.cancel()
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# How long to wait after gateway boot before the first heal cycle.
_BOOT_DELAY_SECS: float = 8.0

# Interval between heal cycles (5 minutes default).
_CYCLE_INTERVAL_SECS: float = 300.0

# Max sessions repaired per cycle (safety cap).
_MAX_REPAIRS_PER_CYCLE: int = 50


def start_boot_healer(
    boot_delay: float = _BOOT_DELAY_SECS,
    cycle_interval: float = _CYCLE_INTERVAL_SECS,
) -> asyncio.Task[None]:
    """Schedule the boot-healer background task and return it.

    The returned task can be cancelled on Gateway shutdown to stop the healer
    cleanly without waiting for the current sleep to expire.
    """
    task = asyncio.ensure_future(_healer_loop(boot_delay, cycle_interval))
    task.set_name("aco-boot-healer")
    logger.info("[ACO] Boot-healer started — first cycle in %.0fs, then every %.0fs",
                boot_delay, cycle_interval)
    return task


async def _healer_loop(boot_delay: float, cycle_interval: float) -> None:
    """Main healer loop — runs indefinitely until cancelled."""
    try:
        await asyncio.sleep(boot_delay)
    except asyncio.CancelledError:
        return

    while True:
        try:
            await _heal_cycle()
        except Exception:
            logger.exception("[ACO] Boot-healer cycle failed — will retry in %.0fs",
                             cycle_interval)
        try:
            await asyncio.sleep(cycle_interval)
        except asyncio.CancelledError:
            logger.info("[ACO] Boot-healer cancelled — shutting down")
            return


async def _heal_cycle() -> None:
    """Single heal cycle: nervous system scan + integrity + engine/voice + sessions.

    Step N: Nervous System scan — unified signal aggregation across ALL registered
            fibers (built-ins + entry-point plugins + local ~/.corvin/nerve_fibers/).
            New layers self-register; the healer picks them up automatically.
    Step 0: Security Integrity Scan (Immunsystem) per tenant.
            Checks audit chain, config tampering, license pubkey, compliance gates.
            Alerts operator on CRITICAL findings.
    Step 1: Proactive engine + voice readiness check per tenant.
            Starts Ollama if offline, installs edge-tts if missing.
    Step 2: Reactive session scan via filesystem discovery.
            Covers web, discord, voice, cli, and all future bridge channels.
    """
    from ..aco.anomaly_detector import scan_session, SEVERITY_CRITICAL, SEVERITY_HIGH
    from ..aco.repair import repair_session

    tenants = _discover_tenants()
    if not tenants:
        return

    # ── Step N: Nervous System scan ──────────────────────────────────────
    try:
        from ..aco.nerve import NerveRegistry, write_signals_to_audit, summarize_signals
        signals = await asyncio.get_event_loop().run_in_executor(
            None, NerveRegistry.scan_all
        )
        if signals:
            summary = summarize_signals(signals)
            if summary["critical"] or summary["high"]:
                logger.warning(
                    "[ACO] Nervous system: %d CRITICAL, %d HIGH signals across %d fibers",
                    summary["critical"], summary["high"], len(summary["fibers"]),
                )
                # Reparatur-Versuch für alle HIGH/CRITICAL-Signale
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: NerveRegistry.repair_all(signals)
                )
            # Schreibe CRITICAL+HIGH in Audit-Chain
            for tenant_id in tenants:
                write_signals_to_audit(signals, tenant_id)
                break  # Audit einmal pro Zyklus, nicht pro Tenant dupliziert
    except ImportError:
        pass
    except Exception:
        logger.debug("[ACO] Nervous-system scan failed", exc_info=True)

    # ── Step L5: Actuating local self-repair (ADR-0178, Tier LOCAL) ──────
    # Bounded to CORVIN_HOME (code-immutable), reversible, loss-gated, audited.
    # Respects the CORVIN_ACO_L5_OFF kill switch internally. NEVER commits code
    # or reaches main (that is L6, gated by a signed maintainer capability).
    try:
        from ..aco.repair_actions import run_local_repairs, RepairContext
        from forge import paths as _paths
        _home = _paths.corvin_home()
        for tenant_id in tenants:
            outcomes = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda t=tenant_id: run_local_repairs(
                    RepairContext(corvin_home=_home, tenant_id=t)),
            )
            applied = [o for o in (outcomes or []) if o.status == "applied"]
            if applied:
                logger.info("[ACO] L5 self-repair: %d action(s) applied (tenant=%s)",
                            len(applied), tenant_id)
    except ImportError:
        pass
    except Exception:
        logger.debug("[ACO] L5 actuating repair failed", exc_info=True)

    # ── Step L5.1: default-ON (opt-out) error telemetry (ADR-0179/0180) ──────
    # A machine that cannot fix its own code (no maintainer capability) still
    # helps fix bugs everywhere: UNLESS the operator opted OUT, it ships SCRUBBED,
    # CONTENT-FREE error signatures — never prompts/user data — to the maintainer
    # intake, who synthesizes a proven fix and releases it via PyPI. Default-ON,
    # opt-out (consent_granted): an explicit opt-out makes this step a no-op. The
    # safety guarantee is content-freeness, not consent. Best-effort, never blocks.
    try:
        from ..aco import telemetry as _tel
        from forge import paths as _paths_t
        _home_t = _paths_t.corvin_home()
        if _tel.consent_granted(_home_t):
            def _emit():
                import time as _t
                rep = _tel.collect_local(_home_t)
                if not rep:
                    return None
                _tel.write_outbox(_home_t, rep, stamp=str(int(_t.time())))
                return _tel.submit(_home_t)
            res = await asyncio.get_event_loop().run_in_executor(None, _emit)
            if res and res.get("sent"):
                logger.info("[ACO] telemetry: %d scrubbed report(s) submitted", res["sent"])
    except ImportError:
        pass
    except Exception:
        logger.debug("[ACO] telemetry emit failed", exc_info=True)

    # ── Step 0: Security Integrity Scan ──────────────────────────────────
    try:
        from ..aco.integrity_monitor import run_integrity_scan
        for tenant_id in tenants:
            try:
                findings = await asyncio.get_event_loop().run_in_executor(
                    None, lambda t=tenant_id: run_integrity_scan(t)
                )
                critical = [f for f in findings if f.severity == "CRITICAL"]
                high = [f for f in findings if f.severity == "HIGH"]
                if critical or high:
                    _write_integrity_audit(tenant_id, findings)
            except Exception:
                logger.debug("[ACO] Integrity scan failed for tenant=%s",
                             tenant_id, exc_info=True)
    except ImportError:
        pass  # integrity_monitor not yet available — skip

    # ── Step 1: Proactive engine + voice readiness ────────────────────────
    try:
        from ..aco.engine_healer import run_readiness_check
        for tenant_id in tenants:
            try:
                heal_result = await asyncio.get_event_loop().run_in_executor(
                    None, lambda t=tenant_id: run_readiness_check(t)
                )
                if heal_result.engine_action not in ("none", "") or heal_result.warnings:
                    _write_engine_audit(tenant_id, heal_result)
            except Exception:
                logger.debug("[ACO] Engine readiness check failed for tenant=%s",
                             tenant_id, exc_info=True)
    except ImportError:
        pass  # engine_healer not yet available — skip

    # ── Step 1.5: Chat subsystem liveness ────────────────────────────────
    # Verifies that chat_runtime, voice routes, and STT/TTS are importable
    # and that list_sessions() runs without errors.  Fills the monitoring
    # gap where the /healthz watchdog only confirms the FastAPI router is
    # mounted, not that the chat/voice subsystems are actually functional.
    try:
        from .. import chat_runtime as _cr
        for tenant_id in tenants:
            try:
                _cr.list_sessions(tenant_id)
            except Exception as _e:
                logger.warning(
                    "[ACO] chat_runtime.list_sessions failed for tenant=%s: %s",
                    tenant_id, _e,
                )
                try:
                    from .. import audit as _a
                    _a.action_failed(
                        tenant_id=tenant_id,
                        sid_fingerprint="healer",
                        action="chat.health_check",
                        target_kind="chat_runtime",
                        target_id="list_sessions",
                        reason=str(_e)[:200],
                    )
                except Exception:
                    pass
    except ImportError:
        pass
    except Exception:
        logger.debug("[ACO] Chat subsystem liveness check failed", exc_info=True)

    # Verify voice STT resolver is importable (beyond just binary checks).
    try:
        from operator.voice.scripts.stt import resolver as _stt_res  # noqa: PLC0415
        _ = _stt_res.DEFAULT_CHAIN  # read attribute — confirms module is functional
    except ImportError:
        pass  # stt resolver not installed — engine_healer already reported this
    except Exception:
        logger.debug("[ACO] STT resolver import check failed", exc_info=True)

    # ── Step 2: Reactive session scan + repair ────────────────────────────
    total_sessions = 0
    total_repaired = 0
    total_delta    = 0

    for tenant_id in tenants:
        workdirs = _find_all_workdirs(tenant_id)
        if not workdirs:
            continue

        for workdir in workdirs[:_MAX_REPAIRS_PER_CYCLE]:
            total_sessions += 1
            try:
                anomalies = scan_session(workdir)
                actionable = [
                    a for a in anomalies
                    if a.severity in (SEVERITY_CRITICAL, SEVERITY_HIGH)
                ]
                if not actionable:
                    continue

                result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda w=workdir: repair_session(w, dry_run=False),
                )
                if result.events_written > 0:
                    total_repaired += 1
                    total_delta    += result.delta_loss
                    logger.info(
                        "[ACO] Boot-heal: repaired %s (tenant=%s) "
                        "delta_loss=%d convergence=%s",
                        workdir.name, tenant_id, result.delta_loss,
                        result.convergence_reached,
                    )
            except Exception:
                logger.debug("[ACO] Boot-healer: repair failed for %s",
                             workdir, exc_info=True)

    if total_repaired > 0 or total_sessions > 0:
        logger.info(
            "[ACO] Boot-heal cycle complete — sessions=%d repaired=%d total_delta=%d",
            total_sessions, total_repaired, total_delta,
        )
        _write_audit(total_sessions, total_repaired, total_delta)


def _find_all_workdirs(tenant_id: str) -> list[Path]:
    """Return all session workdirs that contain a chat_debug.jsonl.

    Scans the tenant's sessions/ directory recursively, so every channel
    (web:, discord:, voice/, cli:, and future ones) is included automatically.
    A workdir is the *parent directory* of chat_debug.jsonl.
    """
    try:
        from forge import paths as _fp
        sessions_root = Path(_fp.corvin_home()) / "tenants" / tenant_id / "sessions"
        if not sessions_root.is_dir():
            return []
        return [p.parent for p in sessions_root.rglob("chat_debug.jsonl")]
    except Exception:
        logger.debug("[ACO] Boot-healer: workdir discovery failed for tenant=%s",
                     tenant_id, exc_info=True)
        return []


def _discover_tenants() -> list[str]:
    """Return known tenant_ids from the on-disk tenants directory."""
    try:
        from forge import paths as _fp
        tenants_root = Path(_fp.corvin_home()) / "tenants"
        if not tenants_root.is_dir():
            return ["_default"]
        ids = [
            d.name
            for d in tenants_root.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ]
        return ids or ["_default"]
    except Exception:
        return ["_default"]


def _write_audit(sessions: int, repaired: int, delta: int) -> None:
    """Write a single aco.boot_heal event to the console audit chain."""
    try:
        from .. import audit as console_audit
        console_audit.action_performed(
            action="aco.boot_heal",
            details={
                "sessions_scanned": sessions,
                "sessions_repaired": repaired,
                "total_delta_loss": delta,
            },
        )
    except Exception:
        pass  # audit is best-effort from the healer


def _write_integrity_audit(tenant_id: str, findings: list) -> None:
    """Write an aco.integrity_scan audit event when findings are detected."""
    try:
        from .. import audit as console_audit
        critical = [f for f in findings if f.severity == "CRITICAL"]
        high = [f for f in findings if f.severity == "HIGH"]
        console_audit.action_performed(
            action="aco.integrity_scan",
            details={
                "tenant_id": tenant_id,
                "critical_count": len(critical),
                "high_count": len(high),
                "checks_failed": [f.check_name for f in findings if f.severity in ("CRITICAL", "HIGH")],
            },
        )
    except Exception:
        pass


def _write_engine_audit(tenant_id: str, heal_result: object) -> None:
    """Write an aco.engine_heal audit event when an action was taken or a warning issued."""
    try:
        from .. import audit as console_audit
        console_audit.action_performed(
            action="aco.engine_heal",
            details={"tenant_id": tenant_id, **heal_result.to_audit_details()},
        )
    except Exception:
        pass
