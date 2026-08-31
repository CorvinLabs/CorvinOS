"""
Alert notification channels: Slack, Console, Email.

Provides pluggable alert delivery backends. Each channel is independent and
handles failures gracefully (fail-closed: channel error never crashes alerting).

Channels:
- SlackChannel: Send to Slack webhook (primary for on-call)
- ConsoleChannel: Emit to audit trail + session console
- EmailChannel: Send to configured email addresses (fallback)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, List
import logging
import asyncio
from datetime import datetime
from urllib.parse import urljoin

try:
    import aiohttp
except ImportError:
    aiohttp = None

try:
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
except ImportError:
    smtplib = None


logger = logging.getLogger(__name__)


class AlertChannel(ABC):
    """Base class for alert channels."""

    @abstractmethod
    def send(self, alert: "AlertEvent") -> bool:
        """
        Send alert through this channel.

        Returns True if successful, False if failed.
        """
        pass


@dataclass
class SlackConfig:
    """Slack webhook configuration."""
    webhook_url: str
    channel: str = "#alerts"
    username: str = "CorvinOS Alerts"
    icon_emoji: str = ":bell:"


class SlackChannel(AlertChannel):
    """Send alerts to Slack via webhook."""

    def __init__(self, config: SlackConfig):
        self.config = config
        self.session: Optional[aiohttp.ClientSession] = None

    def send(self, alert: "AlertEvent") -> bool:
        """Send alert to Slack."""
        if not aiohttp:
            logger.warning("aiohttp not available, Slack alerts disabled")
            return False

        try:
            # Run async send in a synchronous context
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(self._send_async(alert))
            loop.close()
            return result
        except Exception as e:
            logger.error(f"Slack alert failed: {e}", exc_info=True)
            return False

    async def _send_async(self, alert: "AlertEvent") -> bool:
        """Async implementation of Slack send."""
        color_map = {
            "info": "#36a64f",
            "warning": "#ff9900",
            "critical": "#ff0000",
        }
        color = color_map.get(alert.severity.value, "#999999")

        payload = {
            "channel": self.config.channel,
            "username": self.config.username,
            "icon_emoji": self.config.icon_emoji,
            "attachments": [
                {
                    "color": color,
                    "title": f"{alert.severity.value.upper()}: {alert.slo_name}",
                    "text": alert.message,
                    "fields": [
                        {
                            "title": "Measured Value",
                            "value": f"{alert.measured_value:.4f}",
                            "short": True,
                        },
                        {
                            "title": "Threshold",
                            "value": f"{alert.threshold:.4f}",
                            "short": True,
                        },
                        {
                            "title": "Target",
                            "value": f"{alert.target_value:.4f}",
                            "short": True,
                        },
                        {
                            "title": "Time",
                            "value": alert.timestamp.isoformat(),
                            "short": True,
                        },
                    ],
                }
            ],
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.config.webhook_url, json=payload) as resp:
                    if resp.status == 200:
                        logger.info(f"Slack alert sent for {alert.slo_name}")
                        return True
                    else:
                        logger.error(
                            f"Slack returned {resp.status}: {await resp.text()}"
                        )
                        return False
        except Exception as e:
            logger.error(f"Slack send failed: {e}", exc_info=True)
            return False


class ConsoleChannel(AlertChannel):
    """
    Emit alerts to audit trail and console.

    Writes to:
    - Alert history (for dashboard)
    - Audit trail (for compliance)
    - Console stderr (for operator visibility)
    """

    def __init__(self, audit_writer=None, console_out=None):
        self.audit_writer = audit_writer
        self.console_out = console_out or __import__("sys").stderr

    def send(self, alert: "AlertEvent") -> bool:
        """Send alert to console."""
        try:
            # Write to audit trail if available
            if self.audit_writer:
                try:
                    self.audit_writer.write_event(
                        event_type="alert.triggered",
                        data=alert.to_dict(),
                    )
                except Exception as e:
                    logger.warning(f"Failed to write alert to audit: {e}")

            # Write to console stderr
            msg = (
                f"[{alert.timestamp.isoformat()}] "
                f"[{alert.severity.value.upper()}] "
                f"{alert.slo_name}: {alert.message}\n"
            )
            self.console_out.write(msg)
            self.console_out.flush()

            logger.info(f"Alert emitted to console: {alert.slo_name}")
            return True

        except Exception as e:
            logger.error(f"Console alert failed: {e}", exc_info=True)
            return False


@dataclass
class EmailConfig:
    """Email configuration."""
    smtp_host: str
    smtp_port: int = 587
    use_tls: bool = True
    username: str = ""
    password: str = ""
    from_addr: str = "alerts@corvin.local"
    to_addrs: List[str] = None
    subject_prefix: str = "[CorvinOS Alert]"


class EmailChannel(AlertChannel):
    """Send alerts via email."""

    def __init__(self, config: EmailConfig):
        self.config = config
        if self.config.to_addrs is None:
            self.config.to_addrs = []

    def send(self, alert: "AlertEvent") -> bool:
        """Send alert via email."""
        if not smtplib:
            logger.warning("smtplib not available, email alerts disabled")
            return False

        if not self.config.to_addrs:
            logger.warning("No email addresses configured, skipping email alert")
            return False

        try:
            # Create message
            msg = MIMEMultipart("alternative")
            msg["Subject"] = (
                f"{self.config.subject_prefix} "
                f"[{alert.severity.value.upper()}] {alert.slo_name}"
            )
            msg["From"] = self.config.from_addr
            msg["To"] = ", ".join(self.config.to_addrs)

            # Text body
            text_body = (
                f"Alert: {alert.slo_name}\n"
                f"Severity: {alert.severity.value.upper()}\n"
                f"Message: {alert.message}\n\n"
                f"Measured Value: {alert.measured_value:.4f}\n"
                f"Threshold: {alert.threshold:.4f}\n"
                f"Target: {alert.target_value:.4f}\n"
                f"Time: {alert.timestamp.isoformat()}\n"
            )

            # HTML body
            html_body = (
                f"<html><body>"
                f"<h2>{alert.slo_name}</h2>"
                f"<p><strong>Severity:</strong> {alert.severity.value.upper()}</p>"
                f"<p><strong>Message:</strong> {alert.message}</p>"
                f"<table border='1'>"
                f"<tr><td>Measured Value</td><td>{alert.measured_value:.4f}</td></tr>"
                f"<tr><td>Threshold</td><td>{alert.threshold:.4f}</td></tr>"
                f"<tr><td>Target</td><td>{alert.target_value:.4f}</td></tr>"
                f"<tr><td>Time</td><td>{alert.timestamp.isoformat()}</td></tr>"
                f"</table>"
                f"</body></html>"
            )

            msg.attach(MIMEText(text_body, "plain"))
            msg.attach(MIMEText(html_body, "html"))

            # Send via SMTP
            with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port) as server:
                if self.config.use_tls:
                    server.starttls()

                if self.config.username and self.config.password:
                    server.login(self.config.username, self.config.password)

                server.sendmail(
                    self.config.from_addr,
                    self.config.to_addrs,
                    msg.as_string(),
                )

            logger.info(f"Email alert sent for {alert.slo_name}")
            return True

        except Exception as e:
            logger.error(f"Email alert failed: {e}", exc_info=True)
            return False
