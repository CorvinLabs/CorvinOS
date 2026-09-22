"""orchestration_handler.py — Email handler for orchestration events.

Routes an OrchestrationCompleteEvent to the user's email address with:
- Formatted HTML body summarizing task outcomes
- Voice attachment (OGG/MP3) if available
- Graceful fallback to text-only if attachment fails
- Full audit logging

Integration:
- Called by orchestration_aggregator.py after emit_orchestration_event()
- OR called by a Python outbox poller (daemon.py) reading orchestration_*.json
- Writes SMTP outbound via nodemailer bridge (JS daemon.js processes it)
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
import sys
import time
from dataclasses import dataclass
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OrchestrationPayload:
    """Orchestration event envelope from orchestration_router.py."""

    channel: str
    message_type: str
    event_type: str  # ORCHESTRATION_COMPLETE_SUCCESS | ORCHESTRATION_COMPLETE_MIXED
    batch_id: str
    task_count: int
    success_count: int
    failed_tasks: list  # [{task_id, error}, ...]
    text: str  # Plain-text summary
    voice_attachment_path: Optional[str]
    timestamp: float
    metadata: dict


def _format_duration(seconds: float) -> str:
    """Format duration in human-readable form (matching orchestration_router)."""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m}m {s}s" if s > 0 else f"{m}m"
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h {m}m" if m > 0 else f"{h}h"


def _generate_html_body(payload: OrchestrationPayload, console_url: str = "http://localhost:8765/console") -> str:
    """Generate HTML email body with task summary table.

    Args:
        payload: OrchestrationPayload with event data
        console_url: URL to console dashboard for "View Results" link

    Returns:
        HTML string ready for email body
    """
    # Determine status emoji and color
    if payload.event_type == "ORCHESTRATION_COMPLETE_SUCCESS":
        status_emoji = "✅"
        status_text = "All Tasks Completed Successfully"
        bg_color = "#d4edda"  # Light green
        border_color = "#28a745"  # Green
    else:
        status_emoji = "⚠️"
        status_text = "Some Tasks Failed"
        bg_color = "#fff3cd"  # Light yellow
        border_color = "#ffc107"  # Amber

    # Build task summary rows (show failed tasks prominently)
    task_rows = []

    # Success count
    if payload.success_count > 0:
        task_rows.append(f"""
        <tr>
            <td style="padding: 8px; border-bottom: 1px solid #ddd;">✅ Successful Tasks</td>
            <td style="padding: 8px; border-bottom: 1px solid #ddd; text-align: right;">{payload.success_count}</td>
            <td style="padding: 8px; border-bottom: 1px solid #ddd; text-align: center; color: #28a745; font-weight: bold;">✓</td>
        </tr>
        """)

    # Failed tasks
    if payload.failed_tasks:
        task_rows.append(f"""
        <tr>
            <td style="padding: 8px; border-bottom: 1px solid #ddd;">❌ Failed Tasks</td>
            <td style="padding: 8px; border-bottom: 1px solid #ddd; text-align: right;">{len(payload.failed_tasks)}</td>
            <td style="padding: 8px; border-bottom: 1px solid #ddd; text-align: center; color: #dc3545; font-weight: bold;">✗</td>
        </tr>
        """)

        # Detailed failure reasons
        for failed in payload.failed_tasks[:5]:  # Show first 5 failures
            task_id = failed.get("task_id", "unknown")
            error = failed.get("error", "No error message")
            task_rows.append(f"""
            <tr style="background-color: #f8f9fa;">
                <td style="padding: 8px; border-bottom: 1px solid #ddd; font-size: 0.9em;">  └ {task_id}</td>
                <td colspan="2" style="padding: 8px; border-bottom: 1px solid #ddd; font-size: 0.85em; color: #666;">{error}</td>
            </tr>
            """)

        if len(payload.failed_tasks) > 5:
            remaining = len(payload.failed_tasks) - 5
            task_rows.append(f"""
            <tr style="background-color: #f8f9fa;">
                <td style="padding: 8px; border-bottom: 1px solid #ddd; font-size: 0.9em;" colspan="3">
                    ... and {remaining} more failure(s)
                </td>
            </tr>
            """)

    task_table = "".join(task_rows) if task_rows else """
        <tr>
            <td style="padding: 8px; border-bottom: 1px solid #ddd;">📊 Task Summary</td>
            <td style="padding: 8px; border-bottom: 1px solid #ddd; text-align: right;">—</td>
            <td style="padding: 8px; border-bottom: 1px solid #ddd; text-align: center;">—</td>
        </tr>
    """

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; line-height: 1.6; color: #333;">
        <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 2px solid {border_color}; border-radius: 8px; background-color: {bg_color};">

            <h2 style="margin-top: 0; color: #1a1a1a;">{status_emoji} {status_text}</h2>

            <p style="font-size: 1.1em; margin: 16px 0;">
                <strong>Batch ID:</strong> <code style="background-color: #f5f5f5; padding: 2px 6px; border-radius: 3px;">{payload.batch_id}</code>
            </p>

            <p style="margin: 8px 0;">
                <strong>Summary:</strong> {payload.text}
            </p>

            <table style="width: 100%; margin: 16px 0; border-collapse: collapse; background-color: white; border: 1px solid #ddd; border-radius: 4px;">
                <thead>
                    <tr style="background-color: #f8f9fa; border-bottom: 2px solid {border_color};">
                        <th style="padding: 10px; text-align: left; font-weight: bold;">Task Status</th>
                        <th style="padding: 10px; text-align: right; font-weight: bold;">Count</th>
                        <th style="padding: 10px; text-align: center; font-weight: bold;">Result</th>
                    </tr>
                </thead>
                <tbody>
                    {task_table}
                </tbody>
            </table>

            <p style="margin: 16px 0; font-size: 0.95em; color: #666;">
                <strong>Timestamp:</strong> {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(payload.timestamp))}
            </p>

            <div style="margin-top: 20px; padding-top: 16px; border-top: 1px solid #ddd;">
                <a href="{console_url}" style="display: inline-block; padding: 10px 20px; background-color: {border_color}; color: white; text-decoration: none; border-radius: 4px; font-weight: bold;">
                    View Full Details in Console →
                </a>
            </div>

            <p style="margin-top: 24px; font-size: 0.85em; color: #999;">
                This is an automated orchestration summary from CorvinOS.
                <a href="https://corvinlabs.com/help" style="color: #0066cc;">Learn more →</a>
            </p>
        </div>
    </body>
    </html>
    """

    return html


def handle_orchestration_complete(
    payload: dict,
    user_email: str,
    smtp_host: str,
    smtp_port: int = 587,
    smtp_user: str = "",
    smtp_password: str = "",
    smtp_use_tls: bool = True,
    from_address: Optional[str] = None,
    from_name: str = "CorvinOS Orchestrator",
) -> bool:
    """Handle orchestration complete event — send formatted email with optional voice attachment.

    Args:
        payload: OrchestrationPayload dict from orchestration_router.py
        user_email: Recipient email address
        smtp_host: SMTP server hostname
        smtp_port: SMTP port (default 587 for TLS)
        smtp_user: SMTP username
        smtp_password: SMTP password / app-specific password
        smtp_use_tls: Use STARTTLS (default True)
        from_address: Sender email address (defaults to smtp_user)
        from_name: Sender display name

    Returns:
        True if email sent successfully, False on error
    """
    try:
        # Parse payload
        orch = OrchestrationPayload(
            channel=payload.get("channel", "email"),
            message_type=payload.get("message_type", "orchestration_complete"),
            event_type=payload.get("event_type", "ORCHESTRATION_COMPLETE_SUCCESS"),
            batch_id=payload.get("batch_id", "unknown"),
            task_count=payload.get("task_count", 0),
            success_count=payload.get("success_count", 0),
            failed_tasks=payload.get("failed_tasks", []),
            text=payload.get("text", ""),
            voice_attachment_path=payload.get("voice_attachment_path"),
            timestamp=payload.get("timestamp", time.time()),
            metadata=payload.get("metadata", {}),
        )

        # Validate recipient
        if not user_email or "@" not in user_email:
            logger.error(f"email: invalid recipient: {user_email}")
            return False

        # Validate SMTP credentials
        if not smtp_host or not smtp_user or not smtp_password:
            logger.error("email: missing SMTP credentials (host/user/password required)")
            return False

        from_addr = from_address or smtp_user

        # Generate HTML body
        html_body = _generate_html_body(orch)
        text_body = orch.text

        # Build MIME message
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"✅ Orchestration Complete: {orch.batch_id[:12]}"
        msg["From"] = f"{from_name} <{from_addr}>" if from_name else from_addr
        msg["To"] = user_email
        msg["Date"] = time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime())

        # Attach text and HTML parts
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        # Attach voice file if present
        attachment_count = 0
        if orch.voice_attachment_path:
            try:
                voice_path = Path(orch.voice_attachment_path)
                if not voice_path.exists():
                    logger.warning(f"email: voice attachment not found: {voice_path}")
                elif voice_path.stat().st_size > 50 * 1024 * 1024:  # >50MB
                    logger.warning(f"email: voice attachment too large: {voice_path} ({voice_path.stat().st_size} bytes)")
                else:
                    # Determine MIME type and filename
                    suffix = voice_path.suffix.lower()
                    if suffix == ".ogg":
                        mime_type = "audio/ogg"
                        filename = f"orchestration_summary_{orch.batch_id[:12]}.ogg"
                    elif suffix == ".mp3":
                        mime_type = "audio/mpeg"
                        filename = f"orchestration_summary_{orch.batch_id[:12]}.mp3"
                    elif suffix == ".wav":
                        mime_type = "audio/wav"
                        filename = f"orchestration_summary_{orch.batch_id[:12]}.wav"
                    else:
                        mime_type = "audio/ogg"
                        filename = f"orchestration_summary_{orch.batch_id[:12]}.ogg"

                    # Read and attach
                    with open(voice_path, "rb") as f:
                        attachment_data = f.read()

                    part = MIMEApplication(attachment_data, _subtype=mime_type.split("/")[1])
                    part.add_header("Content-Disposition", "attachment", filename=filename)
                    msg.attach(part)
                    attachment_count += 1
                    logger.info(f"email: attached voice file: {filename} ({len(attachment_data)} bytes)")

            except Exception as e:
                logger.warning(f"email: failed to attach voice file: {e}")
                # Continue sending text-only email (graceful fallback)

        # Send via SMTP
        try:
            smtp = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
            if smtp_use_tls:
                smtp.starttls()
            smtp.login(smtp_user, smtp_password)
            smtp.sendmail(from_addr, [user_email], msg.as_string())
            smtp.quit()

            logger.info(
                f"email: sent orchestration complete ({orch.event_type}) to {user_email} "
                f"(batch_id={orch.batch_id[:12]}, tasks={orch.task_count}, "
                f"success={orch.success_count}, attachments={attachment_count})"
            )
            return True

        except smtplib.SMTPException as e:
            logger.error(f"email: SMTP error sending to {user_email}: {e}")
            return False
        except OSError as e:
            logger.error(f"email: network error sending to {user_email}: {e}")
            return False

    except Exception as e:
        logger.error(f"email: unexpected error in handle_orchestration_complete: {e}", exc_info=True)
        return False


def process_orchestration_outbox(outbox_dir: str, smtp_config: dict) -> dict[str, bool]:
    """Poll for orchestration_*.json files in outbox and send emails.

    This is for standalone operation (e.g., in a Python daemon) and is NOT
    used when the JavaScript daemon.js is running. It's provided for testing
    or Python-only deployments.

    Args:
        outbox_dir: Path to shared outbox directory
        smtp_config: Dict with keys: host, port, user, password, use_tls, from_address, from_name

    Returns:
        Dict[filename, success: bool] for each processed file
    """
    results = {}
    outbox_path = Path(outbox_dir)

    if not outbox_path.exists():
        logger.debug(f"email: outbox directory not found: {outbox_path}")
        return results

    try:
        files = sorted(outbox_path.glob("orchestration_email_*.json"))
    except OSError as e:
        logger.error(f"email: failed to list outbox: {e}")
        return results

    for file_path in files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                payload = json.load(f)

            # Skip if not for email channel
            if payload.get("channel") != "email":
                continue

            recipient = payload.get("chat_id") or payload.get("to")
            if not recipient:
                logger.warning(f"email: no recipient in {file_path.name}, skipping")
                results[file_path.name] = False
                continue

            # Send email
            success = handle_orchestration_complete(
                payload=payload,
                user_email=recipient,
                smtp_host=smtp_config.get("host", ""),
                smtp_port=smtp_config.get("port", 587),
                smtp_user=smtp_config.get("user", ""),
                smtp_password=smtp_config.get("password", ""),
                smtp_use_tls=smtp_config.get("use_tls", True),
                from_address=smtp_config.get("from_address"),
                from_name=smtp_config.get("from_name", "CorvinOS Orchestrator"),
            )

            results[file_path.name] = success

            # Delete file only if sending succeeded
            if success:
                try:
                    file_path.unlink()
                    logger.debug(f"email: deleted processed file: {file_path.name}")
                except OSError as e:
                    logger.warning(f"email: failed to delete {file_path.name}: {e}")

        except json.JSONDecodeError as e:
            logger.error(f"email: invalid JSON in {file_path.name}: {e}")
            results[file_path.name] = False
        except Exception as e:
            logger.error(f"email: error processing {file_path.name}: {e}", exc_info=True)
            results[file_path.name] = False

    return results


if __name__ == "__main__":
    # Simple standalone test
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    # Example usage (for manual testing)
    test_payload = {
        "channel": "email",
        "message_type": "orchestration_complete",
        "event_type": "ORCHESTRATION_COMPLETE_SUCCESS",
        "batch_id": "test_batch_123",
        "task_count": 5,
        "success_count": 5,
        "failed_tasks": [],
        "text": "✅ 5 background tasks completed successfully in 2m 35s",
        "voice_attachment_path": None,
        "timestamp": time.time(),
        "metadata": {},
    }

    # Example SMTP config (would come from settings.json in production)
    smtp_config = {
        "host": os.environ.get("SMTP_HOST", ""),
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": os.environ.get("SMTP_USER", ""),
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "use_tls": True,
        "from_address": os.environ.get("SMTP_FROM", ""),
        "from_name": "CorvinOS Test",
    }

    # Only run if SMTP config is available
    if smtp_config["host"] and smtp_config["user"] and smtp_config["password"]:
        recipient = os.environ.get("TEST_EMAIL_RECIPIENT", "test@example.com")
        result = handle_orchestration_complete(
            payload=test_payload,
            user_email=recipient,
            smtp_host=smtp_config["host"],
            smtp_port=smtp_config["port"],
            smtp_user=smtp_config["user"],
            smtp_password=smtp_config["password"],
            smtp_use_tls=smtp_config["use_tls"],
            from_address=smtp_config["from_address"],
            from_name=smtp_config["from_name"],
        )
        print(f"Email sent: {result}")
    else:
        print("SMTP config missing (SMTP_HOST, SMTP_USER, SMTP_PASSWORD env vars)")
