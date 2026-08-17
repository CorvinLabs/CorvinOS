"""ACO Layer 4 — Autonomous Diagnosis.

Maps anomalies from Layer 3 to root-cause hypotheses, corvin layer assignments,
and structured bug reports. This is a pure reasoning/mapping module — it does
NOT execute code changes (that is Layer 5, which is opt-in and not yet shipped).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .anomaly_detector import Anomaly, SEVERITY_CRITICAL, SEVERITY_HIGH


# ── Layer mapping ─────────────────────────────────────────────────────────────

# Maps anomaly_class → (layer_ids, root_cause_hypothesis, repro_steps, adr_refs)
_CLASS_TO_DIAGNOSIS: dict[str, dict] = {
    "stalled_turn": {
        "layers": ["L22-worker-engine", "chat_runtime.py"],
        "hypothesis": (
            "stream_turn() raised an unhandled exception after turn.start "
            "was emitted but before the turn completed — turn.done was never written. "
            "Most likely: ACS CancelledError, engine timeout, or unguarded "
            "exception in the delegation branch."
        ),
        "repro_steps": [
            "Look for traceback in console logs immediately after the stall timestamp.",
            "Search chat_debug.jsonl for 'acs.run.done' between the stall turn.start and next turn.start.",
            "Check if `_dbg(sess.workdir, 'turn.done', ...)` is inside a finally: block.",
        ],
        "adr_refs": ["ADR-0174", "ADR-0107"],
    },
    "delegation_orphan": {
        "layers": ["chat_runtime.py", "L29-delegation"],
        "hypothesis": (
            "delegation.decision was True but acs.run.start was not emitted. "
            "Possible causes: (1) exception between the decision and the ACS "
            "run start, (2) ACS budget gate rejected the run silently, "
            "(3) race condition in the delegation flag check."
        ),
        "repro_steps": [
            "Find the delegation.decision event and check if any error event follows it.",
            "Check acs_runtime.py budget gate — does it emit an error on rejection?",
            "Verify delegation_enabled flag in tenant.corvin.yaml.",
        ],
        "adr_refs": ["ADR-0114", "ADR-0174"],
    },
    "acs_error_rate": {
        "layers": ["L22-worker-engine", "L25-compute-worker", "acs_runtime.py"],
        "hypothesis": (
            "ACS workers are failing at a rate above the threshold. "
            "Common causes: engine model unavailable, quota exhaustion, "
            "L34 data-classification rejection, or unguarded exception "
            "in the worker task body."
        ),
        "repro_steps": [
            "Check acs.run.done events for recurring error messages.",
            "Run `bash operator/bridges/run-all-tests.sh` to verify base ACS health.",
            "Check L34 data classification thresholds for the worker engine.",
        ],
        "adr_refs": ["ADR-0107", "ADR-0173"],
    },
    "latency_outlier": {
        "layers": ["L22-worker-engine", "adapter.py"],
        "hypothesis": (
            "Turn latency is anomalously high. Likely causes: engine "
            "cold start, large prompt, ACS worker spawning overhead, "
            "or network/disk I/O contention."
        ),
        "repro_steps": [
            "Check acs.run.done elapsed_s vs turn.done elapsed_ms ratio.",
            "Profile with CORVIN_DEBUG_PERF=1 if available.",
            "Check worker_timeout_seconds in tenant config vs actual elapsed.",
        ],
        "adr_refs": ["ADR-0174"],
    },
    "ws_instability": {
        "layers": ["WebSocket", "adapter.py", "operator/bridges/"],
        "hypothesis": (
            "WebSocket connections are closing frequently. Likely causes: "
            "(1) uvicorn idle timeout (default 300 s), (2) proxy/load-balancer "
            "killing idle connections, (3) bridge restart expiring sessions."
        ),
        "repro_steps": [
            "Check ws.close events for code (1006=abnormal, 1000=clean, 4401=auth).",
            "Verify heartbeat interval in chat-registry.ts (currently 25 s).",
            "Check uvicorn --timeout-keep-alive configuration.",
        ],
        "adr_refs": ["ADR-0174"],
    },
    "empty_response_error": {
        "layers": ["adapter.py", "L22-worker-engine"],
        "hypothesis": (
            "The engine produced no output before the stream error. "
            "Likely causes: engine initialization failure, auth token error, "
            "context-window overflow, or network partition mid-stream."
        ),
        "repro_steps": [
            "Check stream.error message field for engine error text.",
            "Verify API key / CORVIN_CLAUDE_BIN path is valid.",
            "Check if prompt_len in turn.start is near the model's context limit.",
        ],
        "adr_refs": ["ADR-0159", "ADR-0174"],
    },
}


# ── Bug report ────────────────────────────────────────────────────────────────

@dataclass
class BugReport:
    anomaly_class: str
    severity: str
    layers: list[str]
    hypothesis: str
    repro_steps: list[str]
    adr_refs: list[str]
    evidence_events: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "anomaly_class": self.anomaly_class,
            "severity": self.severity,
            "layers": self.layers,
            "hypothesis": self.hypothesis,
            "repro_steps": self.repro_steps,
            "adr_refs": self.adr_refs,
            "evidence_count": len(self.evidence_events),
        }


def diagnose_anomaly(anomaly: Anomaly) -> BugReport:
    """Map an Anomaly to a structured BugReport with root-cause hypothesis."""
    diag = _CLASS_TO_DIAGNOSIS.get(anomaly.anomaly_class)
    if diag is None:
        diag = {
            "layers": ["unknown"],
            "hypothesis": (
                f"No pre-built diagnosis for '{anomaly.anomaly_class}'. "
                "Manual investigation required."
            ),
            "repro_steps": ["Read anomaly evidence and inspect surrounding log events."],
            "adr_refs": ["ADR-0174"],
        }
    return BugReport(
        anomaly_class=anomaly.anomaly_class,
        severity=anomaly.severity,
        layers=diag["layers"],
        hypothesis=diag["hypothesis"],
        repro_steps=diag["repro_steps"],
        adr_refs=diag["adr_refs"],
        evidence_events=anomaly.evidence,
    )


def diagnose_all(anomalies: list[Anomaly]) -> list[BugReport]:
    """Diagnose all anomalies. Only CRITICAL and HIGH get full diagnosis."""
    reports: list[BugReport] = []
    for anomaly in anomalies:
        if anomaly.severity in (SEVERITY_CRITICAL, SEVERITY_HIGH):
            reports.append(diagnose_anomaly(anomaly))
    return reports


def diagnose_session(workdir: Any) -> dict:
    """Full Layer 3 + Layer 4 pipeline for a session."""
    from .anomaly_detector import scan_session
    anomalies = scan_session(workdir)
    reports = diagnose_all(anomalies)
    return {
        "anomaly_count": len(anomalies),
        "diagnosed_count": len(reports),
        "reports": [r.to_dict() for r in reports],
    }
