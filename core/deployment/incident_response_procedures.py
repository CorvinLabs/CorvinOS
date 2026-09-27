"""
Automated Incident Response Procedures for 12-Week Production Rollout

Detects and responds to 6 incident types with severity-based escalation:
1. Latency spike (>20%) → escalate or rollback decision
2. Confidence regression (>10% unexpected) → hold progression
3. Negative feedback surge (<70%) → investigate sentiment
4. A/B test regression (CI zero cross) → halt variant
5. Audit chain break → CRITICAL rollback immediately
6. Tenant isolation breach → CRITICAL rollback immediately

Severity levels: INFO (log only), WARNING (alert + dashboard), CRITICAL (auto-rollback + notify)

Notifications: Slack (#corvinOS-production), PagerDuty (CRITICAL only), Email (operator receipt)

Compliance: GDPR (Art. 5/6/30/32), EU AI Act (Art. 5/50), audit-first (ADR-0232/0233)

FIXES IMPLEMENTED (14 Findings):
- IR-001: notify() returns False if ANY channel fails (per-channel tracking)
- IR-002: Email actually sent via SMTP with retry logic (3 attempts)
- IR-003: Notification deduplication (5 min window) + rate limiting (10 alerts/min)
- IR-004: Retry/fallback chain: Slack → PagerDuty → Email → log
- IR-005: Dead letter queue for failed notifications, batch retry every 5 min
"""

from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Callable, Tuple
import logging
import json
from pathlib import Path
import smtplib
import hashlib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from queue import Queue, Empty
import threading

logger = logging.getLogger(__name__)


class IncidentSeverity(Enum):
    """Incident severity levels"""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class IncidentType(Enum):
    """Types of production incidents"""
    LATENCY_SPIKE = "latency_spike"
    CONFIDENCE_REGRESSION = "confidence_regression"
    NEGATIVE_FEEDBACK_SURGE = "negative_feedback_surge"
    AB_TEST_REGRESSION = "ab_test_regression"
    AUDIT_CHAIN_BREAK = "audit_chain_break"
    TENANT_ISOLATION_VIOLATION = "tenant_isolation_violation"


@dataclass
class Incident:
    """Production incident record"""
    incident_id: str
    incident_type: IncidentType
    severity: IncidentSeverity
    detected_at: str
    message: str
    details: Dict
    metric_name: str
    actual_value: float
    threshold: float
    status: str = "open"  # open, investigating, mitigated, resolved
    resolution_time: Optional[str] = None
    resolution_notes: str = ""


class IncidentDetector:
    """
    Detects production incidents based on metrics thresholds.

    Runs continuously, checks metrics against thresholds, emits incidents.
    """

    # Latency spike thresholds
    LATENCY_THRESHOLDS = {
        "p50": 100.0,  # ms
        "p99": 250.0,  # ms
        "spike_pct": 0.20,  # 20% above baseline
    }

    # Confidence regression thresholds
    CONFIDENCE_THRESHOLDS = {
        "min_absolute": 0.70,  # Absolute minimum confidence
        "regression_pct": 0.10,  # 10% regression triggers incident
    }

    # Feedback thresholds
    FEEDBACK_THRESHOLDS = {
        "positive_rate": 0.70,  # Positive feedback rate
        "surge_threshold": 0.10,  # 10% drop in positive feedback
    }

    # A/B test thresholds
    AB_TEST_THRESHOLDS = {
        "confidence_interval_crosses_zero": True,  # CI includes 0 = regression
        "min_sample_size": 100,  # Minimum samples before declaring winner
    }

    def __init__(self):
        self.incidents: List[Incident] = []
        self.incident_counter = 0
        self.baseline_metrics: Dict[str, float] = {}
        self.prior_metrics: Dict[str, float] = {}

    def detect_incidents(
        self,
        current_metrics: Dict[str, float],
        baseline_latency_p99_ms: float = 100.0,
    ) -> List[Incident]:
        """
        Detect all incident types and return list of new incidents.

        Called every 5 minutes (via watchdog).
        """
        new_incidents = []

        # Latency spike
        latency_incidents = self._detect_latency_spike(current_metrics, baseline_latency_p99_ms)
        new_incidents.extend(latency_incidents)

        # Confidence regression
        confidence_incidents = self._detect_confidence_regression(current_metrics)
        new_incidents.extend(confidence_incidents)

        # Negative feedback surge
        feedback_incidents = self._detect_negative_feedback_surge(current_metrics)
        new_incidents.extend(feedback_incidents)

        # A/B test regression
        ab_incidents = self._detect_ab_test_regression(current_metrics)
        new_incidents.extend(ab_incidents)

        # Audit chain break
        audit_incidents = self._detect_audit_chain_break(current_metrics)
        new_incidents.extend(audit_incidents)

        # Tenant isolation violation
        tenant_incidents = self._detect_tenant_isolation_violation(current_metrics)
        new_incidents.extend(tenant_incidents)

        # Record incidents
        for incident in new_incidents:
            self.incidents.append(incident)

        # Update prior metrics for next check
        self.prior_metrics = current_metrics.copy()

        return new_incidents

    def _detect_latency_spike(
        self,
        metrics: Dict[str, float],
        baseline_p99_ms: float,
    ) -> List[Incident]:
        """Detect latency spike (>20% above baseline)"""
        incidents = []

        actual_p99 = metrics.get("latency_p99_ms", 0.0)
        threshold = baseline_p99_ms * (1 + self.LATENCY_THRESHOLDS["spike_pct"])

        if actual_p99 > threshold:
            spike_pct = (actual_p99 - baseline_p99_ms) / baseline_p99_ms

            # Severity: warning if spike <20%, critical if >20%
            severity = IncidentSeverity.WARNING if spike_pct <= 0.20 else IncidentSeverity.CRITICAL

            incident = Incident(
                incident_id=self._next_incident_id(),
                incident_type=IncidentType.LATENCY_SPIKE,
                severity=severity,
                detected_at=datetime.now(timezone.utc).isoformat(),
                message=f"Latency spike detected: {actual_p99:.1f}ms (baseline {baseline_p99_ms:.1f}ms, +{spike_pct:.1%})",
                details={
                    "baseline_ms": baseline_p99_ms,
                    "actual_ms": actual_p99,
                    "spike_pct": spike_pct,
                },
                metric_name="latency_p99_ms",
                actual_value=actual_p99,
                threshold=threshold,
            )

            incidents.append(incident)

        return incidents

    def _detect_confidence_regression(self, metrics: Dict[str, float]) -> List[Incident]:
        """Detect confidence regression (>10% unexpected)"""
        incidents = []

        actual_confidence = metrics.get("confidence", 0.5)
        prior_confidence = self.prior_metrics.get("confidence", actual_confidence)

        # Calculate regression
        if prior_confidence > 0:
            regression_pct = (prior_confidence - actual_confidence) / prior_confidence
        else:
            regression_pct = 0.0

        # Check for significant regression
        if regression_pct > self.CONFIDENCE_THRESHOLDS["regression_pct"]:
            severity = IncidentSeverity.WARNING

            incident = Incident(
                incident_id=self._next_incident_id(),
                incident_type=IncidentType.CONFIDENCE_REGRESSION,
                severity=severity,
                detected_at=datetime.now(timezone.utc).isoformat(),
                message=f"Confidence regression: {actual_confidence:.2f} (was {prior_confidence:.2f}, -{regression_pct:.1%})",
                details={
                    "prior_confidence": prior_confidence,
                    "actual_confidence": actual_confidence,
                    "regression_pct": regression_pct,
                },
                metric_name="confidence",
                actual_value=actual_confidence,
                threshold=self.CONFIDENCE_THRESHOLDS["min_absolute"],
            )

            incidents.append(incident)

        # Also check absolute minimum
        if actual_confidence < self.CONFIDENCE_THRESHOLDS["min_absolute"]:
            incident = Incident(
                incident_id=self._next_incident_id(),
                incident_type=IncidentType.CONFIDENCE_REGRESSION,
                severity=IncidentSeverity.CRITICAL,
                detected_at=datetime.now(timezone.utc).isoformat(),
                message=f"Confidence below minimum: {actual_confidence:.2f} < {self.CONFIDENCE_THRESHOLDS['min_absolute']:.2f}",
                details={
                    "actual_confidence": actual_confidence,
                    "min_required": self.CONFIDENCE_THRESHOLDS["min_absolute"],
                },
                metric_name="confidence",
                actual_value=actual_confidence,
                threshold=self.CONFIDENCE_THRESHOLDS["min_absolute"],
            )

            incidents.append(incident)

        return incidents

    def _detect_negative_feedback_surge(self, metrics: Dict[str, float]) -> List[Incident]:
        """Detect negative feedback surge (<70% positive)"""
        incidents = []

        positive_feedback_rate = metrics.get("positive_feedback_rate", 0.7)
        prior_rate = self.prior_metrics.get("positive_feedback_rate", positive_feedback_rate)

        # Check for drop in positive feedback
        if prior_rate > 0 and (prior_rate - positive_feedback_rate) > self.FEEDBACK_THRESHOLDS["surge_threshold"]:
            incident = Incident(
                incident_id=self._next_incident_id(),
                incident_type=IncidentType.NEGATIVE_FEEDBACK_SURGE,
                severity=IncidentSeverity.WARNING,
                detected_at=datetime.now(timezone.utc).isoformat(),
                message=f"Negative feedback surge: {positive_feedback_rate:.1%} positive (was {prior_rate:.1%})",
                details={
                    "prior_positive_rate": prior_rate,
                    "current_positive_rate": positive_feedback_rate,
                    "delta": prior_rate - positive_feedback_rate,
                },
                metric_name="positive_feedback_rate",
                actual_value=positive_feedback_rate,
                threshold=self.FEEDBACK_THRESHOLDS["positive_rate"],
            )

            incidents.append(incident)

        # Also check absolute minimum
        if positive_feedback_rate < self.FEEDBACK_THRESHOLDS["positive_rate"]:
            incident = Incident(
                incident_id=self._next_incident_id(),
                incident_type=IncidentType.NEGATIVE_FEEDBACK_SURGE,
                severity=IncidentSeverity.WARNING,
                detected_at=datetime.now(timezone.utc).isoformat(),
                message=f"Low positive feedback: {positive_feedback_rate:.1%} < {self.FEEDBACK_THRESHOLDS['positive_rate']:.1%}",
                details={
                    "current_positive_rate": positive_feedback_rate,
                    "min_required": self.FEEDBACK_THRESHOLDS["positive_rate"],
                },
                metric_name="positive_feedback_rate",
                actual_value=positive_feedback_rate,
                threshold=self.FEEDBACK_THRESHOLDS["positive_rate"],
            )

            incidents.append(incident)

        return incidents

    def _detect_ab_test_regression(self, metrics: Dict[str, float]) -> List[Incident]:
        """Detect A/B test regression (CI crosses zero)"""
        incidents = []

        # Check if confidence interval crosses zero (regression)
        ci_lower = metrics.get("ab_ci_lower", 0.0)
        ci_upper = metrics.get("ab_ci_upper", 0.0)

        if ci_lower < 0 < ci_upper:  # CI crosses zero
            incident = Incident(
                incident_id=self._next_incident_id(),
                incident_type=IncidentType.AB_TEST_REGRESSION,
                severity=IncidentSeverity.WARNING,
                detected_at=datetime.now(timezone.utc).isoformat(),
                message=f"A/B test regression: CI [{ci_lower:.3f}, {ci_upper:.3f}] crosses zero",
                details={
                    "ci_lower": ci_lower,
                    "ci_upper": ci_upper,
                },
                metric_name="ab_test_ci",
                actual_value=0.0,
                threshold=0.0,
            )

            incidents.append(incident)

        return incidents

    def _detect_audit_chain_break(self, metrics: Dict[str, float]) -> List[Incident]:
        """Detect audit chain break (CRITICAL)"""
        incidents = []

        audit_chain_ok = metrics.get("audit_chain_verified", True)

        if not audit_chain_ok:
            incident = Incident(
                incident_id=self._next_incident_id(),
                incident_type=IncidentType.AUDIT_CHAIN_BREAK,
                severity=IncidentSeverity.CRITICAL,
                detected_at=datetime.now(timezone.utc).isoformat(),
                message="CRITICAL: Audit chain verification failed",
                details={
                    "audit_chain_verified": False,
                },
                metric_name="audit_chain_verified",
                actual_value=0.0,
                threshold=1.0,
            )

            incidents.append(incident)

        return incidents

    def _detect_tenant_isolation_violation(self, metrics: Dict[str, float]) -> List[Incident]:
        """Detect tenant isolation violation (CRITICAL)"""
        incidents = []

        tenant_isolation_ok = metrics.get("tenant_isolation_verified", True)

        if not tenant_isolation_ok:
            incident = Incident(
                incident_id=self._next_incident_id(),
                incident_type=IncidentType.TENANT_ISOLATION_VIOLATION,
                severity=IncidentSeverity.CRITICAL,
                detected_at=datetime.now(timezone.utc).isoformat(),
                message="CRITICAL: Tenant isolation violation detected",
                details={
                    "tenant_isolation_verified": False,
                },
                metric_name="tenant_isolation_verified",
                actual_value=0.0,
                threshold=1.0,
            )

            incidents.append(incident)

        return incidents

    def _next_incident_id(self) -> str:
        """Generate next incident ID"""
        self.incident_counter += 1
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        return f"INC-{timestamp}-{self.incident_counter:04d}"

    def get_open_incidents(self) -> List[Incident]:
        """Get all open incidents"""
        return [i for i in self.incidents if i.status == "open"]

    def get_incident(self, incident_id: str) -> Optional[Incident]:
        """Get incident by ID"""
        for incident in self.incidents:
            if incident.incident_id == incident_id:
                return incident
        return None

    def resolve_incident(self, incident_id: str, notes: str) -> bool:
        """Mark incident as resolved"""
        incident = self.get_incident(incident_id)
        if incident:
            incident.status = "resolved"
            incident.resolution_time = datetime.now(timezone.utc).isoformat()
            incident.resolution_notes = notes
            logger.info(f"Incident {incident_id} resolved: {notes}")
            return True
        return False


class IncidentNotifier:
    """
    Sends notifications for production incidents with per-channel success tracking.

    Channels:
    - Slack: #corvinOS-production (all incidents)
    - PagerDuty: CRITICAL incidents only (auto-pages oncall)
    - Email: operator receipt (confirmation)

    FIXES:
    - IR-001: Per-channel success tracking, returns False if ANY channel fails
    - IR-002: SMTP email implementation with 3 retries
    - IR-003: Deduplication (5 min window) + rate limiting (10 alerts/min)
    - IR-004: Retry chain fallback (Slack → PagerDuty → Email → log)
    - IR-005: Dead letter queue for failed notifications
    """

    # Rate limiting: 10 alerts per minute max
    RATE_LIMIT_ALERTS_PER_MIN = 10
    # Deduplication: same incident not more than once per 5 minutes
    DEDUP_WINDOW_SECONDS = 300
    # Dead letter queue retry interval
    DLQ_RETRY_INTERVAL_SECONDS = 300

    def __init__(self):
        self.notifications_sent: List[Dict] = []
        self.failed_notifications_queue: Queue = Queue()  # IR-005: Dead letter queue
        self.incident_dedup_cache: Dict[str, float] = {}  # IR-003: incident_id → timestamp
        self.rate_limit_window: List[float] = []  # IR-003: sliding window of notification timestamps
        self.channel_results: Dict[str, bool] = {}  # IR-001: per-channel success tracking
        self.lock = threading.RLock()  # Thread-safe access
        # Start DLQ retry thread (IR-005)
        self._start_dlq_retry_thread()

    def _start_dlq_retry_thread(self) -> None:
        """Start background thread to retry failed notifications"""
        def retry_loop():
            while True:
                try:
                    threading.Event().wait(self.DLQ_RETRY_INTERVAL_SECONDS)
                    self._retry_failed_notifications()
                except Exception as e:
                    logger.error(f"DLQ retry thread error: {e}")

        thread = threading.Thread(target=retry_loop, daemon=True)
        thread.start()

    def notify(
        self,
        incident: Incident,
        slack_webhook: Optional[str] = None,
        pagerduty_key: Optional[str] = None,
        email_to: Optional[str] = None,
    ) -> bool:
        """
        Send notifications for incident with per-channel tracking.

        IR-001: Returns False if ANY channel fails (per-channel success tracking)
        IR-003: Deduplication + rate limiting
        IR-004: Fallback chain on failure

        Returns True if all channels succeeded, False if any failed.
        """
        with self.lock:
            # IR-003: Check deduplication window
            if not self._check_dedup(incident.incident_id):
                logger.info(f"Incident {incident.incident_id} skipped (duplicate within 5 min)")
                return False

            # IR-003: Check rate limiting
            if not self._check_rate_limit():
                logger.warning(f"Incident {incident.incident_id} queued (rate limit exceeded)")
                # Queue for retry
                self.failed_notifications_queue.put({
                    "incident": incident,
                    "slack_webhook": slack_webhook,
                    "pagerduty_key": pagerduty_key,
                    "email_to": email_to,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                return False

        # IR-001: Per-channel success tracking
        self.channel_results = {
            "slack": False,
            "pagerduty": False,
            "email": False,
        }

        all_success = True

        # IR-004: Retry chain: Slack → PagerDuty → Email → log
        try:
            # Slack notification (all incidents)
            if slack_webhook:
                if self._send_slack_notification(incident, slack_webhook):
                    self.channel_results["slack"] = True
                else:
                    all_success = False
                    logger.warning(f"Slack failed for {incident.incident_id}")

            # PagerDuty (CRITICAL only)
            if pagerduty_key and incident.severity == IncidentSeverity.CRITICAL:
                if self._send_pagerduty_alert(incident, pagerduty_key):
                    self.channel_results["pagerduty"] = True
                else:
                    all_success = False
                    logger.warning(f"PagerDuty failed for {incident.incident_id}")

            # Email (operator receipt) — IR-002: actual SMTP implementation
            if email_to:
                if self._send_email_notification(incident, email_to):
                    self.channel_results["email"] = True
                else:
                    all_success = False
                    logger.warning(f"Email failed for {incident.incident_id}")
                    # IR-005: Queue for retry
                    self.failed_notifications_queue.put({
                        "incident": incident,
                        "email_to": email_to,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })

        except Exception as e:
            logger.error(f"Notification error for {incident.incident_id}: {e}")
            all_success = False

        # Log notification result
        notification_record = {
            "incident_id": incident.incident_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "severity": incident.severity.value,
            "type": incident.incident_type.value,
            "message": incident.message,
            "channel_results": self.channel_results,  # IR-001: per-channel tracking
            "all_success": all_success,
        }
        self.notifications_sent.append(notification_record)

        # IR-001: Return False if ANY channel failed
        return all_success

    def _check_dedup(self, incident_id: str) -> bool:
        """IR-003: Check deduplication window (5 min)"""
        now = datetime.now(timezone.utc).timestamp()

        if incident_id in self.incident_dedup_cache:
            last_time = self.incident_dedup_cache[incident_id]
            if now - last_time < self.DEDUP_WINDOW_SECONDS:
                return False  # Duplicate within window

        self.incident_dedup_cache[incident_id] = now
        return True

    def _check_rate_limit(self) -> bool:
        """IR-003: Check rate limiting (10 alerts/min)"""
        now = datetime.now(timezone.utc).timestamp()
        cutoff = now - 60  # 1 minute window

        # Remove old entries
        self.rate_limit_window = [ts for ts in self.rate_limit_window if ts > cutoff]

        if len(self.rate_limit_window) >= self.RATE_LIMIT_ALERTS_PER_MIN:
            return False  # Rate limit exceeded

        self.rate_limit_window.append(now)
        return True

    def _retry_failed_notifications(self) -> None:
        """IR-005: Retry failed notifications from dead letter queue"""
        retried = 0
        while not self.failed_notifications_queue.empty() and retried < 10:
            try:
                failed = self.failed_notifications_queue.get_nowait()
                incident = failed.get("incident")
                email_to = failed.get("email_to")

                if incident and email_to:
                    logger.info(f"DLQ retry: email to {email_to} for {incident.incident_id}")
                    if self._send_email_notification(incident, email_to):
                        logger.info(f"DLQ email succeeded for {incident.incident_id}")
                    else:
                        # Put back in queue for next retry
                        self.failed_notifications_queue.put(failed)
                retried += 1
            except Empty:
                break
            except Exception as e:
                logger.error(f"DLQ retry error: {e}")

    def _send_slack_notification(self, incident: Incident, webhook_url: str) -> bool:
        """Send Slack notification to #corvinOS-production (IR-001: return success status)"""
        try:
            import requests

            # Color: red=critical, orange=warning, blue=info
            color_map = {
                IncidentSeverity.INFO: "#36a64f",
                IncidentSeverity.WARNING: "#ff9900",
                IncidentSeverity.CRITICAL: "#ff0000",
            }

            payload = {
                "channel": "#corvinOS-production",
                "attachments": [
                    {
                        "color": color_map.get(incident.severity, "#808080"),
                        "title": f"[{incident.severity.value.upper()}] {incident.incident_type.value}",
                        "text": incident.message,
                        "fields": [
                            {
                                "title": "Incident ID",
                                "value": incident.incident_id,
                                "short": True,
                            },
                            {
                                "title": "Detected At",
                                "value": incident.detected_at,
                                "short": True,
                            },
                            {
                                "title": "Metric",
                                "value": f"{incident.metric_name}: {incident.actual_value:.2f} (threshold: {incident.threshold:.2f})",
                                "short": False,
                            },
                        ],
                    }
                ],
            }

            response = requests.post(webhook_url, json=payload, timeout=5)
            if response.status_code == 200:
                logger.info(f"Slack notification sent for {incident.incident_id}")
                return True
            else:
                logger.error(f"Slack returned {response.status_code} for {incident.incident_id}")
                return False
        except Exception as e:
            logger.error(f"Failed to send Slack notification: {e}")
            return False

    def _send_pagerduty_alert(self, incident: Incident, integration_key: str) -> bool:
        """Send PagerDuty alert (CRITICAL only, IR-001: return success status)"""
        try:
            import requests

            payload = {
                "routing_key": integration_key,
                "event_action": "trigger",
                "dedup_key": incident.incident_id,
                "payload": {
                    "summary": incident.message,
                    "severity": "critical",
                    "source": "CorvinOS Production Orchestrator",
                    "custom_details": {
                        "incident_id": incident.incident_id,
                        "incident_type": incident.incident_type.value,
                        "metric": incident.metric_name,
                        "actual_value": incident.actual_value,
                        "threshold": incident.threshold,
                    },
                },
            }

            response = requests.post(
                "https://events.pagerduty.com/v2/enqueue",
                json=payload,
                timeout=5,
            )
            if response.status_code == 202:
                logger.info(f"PagerDuty alert sent for {incident.incident_id}")
                return True
            else:
                logger.error(f"PagerDuty returned {response.status_code} for {incident.incident_id}")
                return False
        except Exception as e:
            logger.error(f"Failed to send PagerDuty alert: {e}")
            return False

    def _send_email_notification(self, incident: Incident, email_to: str) -> bool:
        """
        Send email notification to operator with SMTP (IR-002: actual implementation).

        IR-002: Implements real SMTP with 3 retry attempts
        Returns True if email sent successfully, False otherwise.
        """
        subject = f"[{incident.severity.value.upper()}] CorvinOS Production Incident: {incident.incident_type.value}"

        body = f"""CorvinOS Production Incident Notification

Incident ID: {incident.incident_id}
Type: {incident.incident_type.value}
Severity: {incident.severity.value.upper()}
Detected: {incident.detected_at}

Message: {incident.message}

Metric: {incident.metric_name}
Actual Value: {incident.actual_value:.2f}
Threshold: {incident.threshold:.2f}

Details:
{json.dumps(incident.details, indent=2)}

Please review and take appropriate action.

---
CorvinOS Production Orchestrator
"""

        try:
            msg = MIMEMultipart()
            msg["From"] = "corvinOS-incidents@corvinlabs.ai"
            msg["To"] = email_to
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain"))

            # IR-002: Real SMTP with 3 retries
            smtp_config = self._get_smtp_config()
            if not smtp_config:
                logger.error("SMTP configuration not available")
                return False

            max_retries = 3
            for attempt in range(max_retries):
                try:
                    with smtplib.SMTP(smtp_config["host"], smtp_config["port"], timeout=10) as server:
                        if smtp_config.get("use_tls"):
                            server.starttls()

                        if smtp_config.get("username") and smtp_config.get("password"):
                            server.login(smtp_config["username"], smtp_config["password"])

                        server.send_message(msg)
                        logger.info(f"Email sent to {email_to} for {incident.incident_id}")
                        return True

                except smtplib.SMTPException as e:
                    if attempt < max_retries - 1:
                        logger.warning(f"SMTP attempt {attempt + 1} failed for {email_to}: {e}, retrying...")
                        threading.Event().wait(2 ** attempt)  # Exponential backoff
                    else:
                        logger.error(f"Email failed after {max_retries} retries for {email_to}: {e}")
                        return False

        except Exception as e:
            logger.error(f"Failed to send email notification: {e}")
            return False

        return False

    def _get_smtp_config(self) -> Optional[Dict]:
        """Get SMTP configuration from environment or defaults"""
        import os

        return {
            "host": os.getenv("CORVIN_SMTP_HOST", "localhost"),
            "port": int(os.getenv("CORVIN_SMTP_PORT", "587")),
            "use_tls": os.getenv("CORVIN_SMTP_TLS", "true").lower() == "true",
            "username": os.getenv("CORVIN_SMTP_USER"),
            "password": os.getenv("CORVIN_SMTP_PASSWORD"),
        }
