# Email Orchestration Handler Integration

## Overview

The `orchestration_handler.py` module handles routing of orchestration completion events (from `orchestration_aggregator.py`) to user email addresses with formatted summaries and optional voice attachments.

**Files:**
- `orchestration_handler.py` — Main email handler (100 LoC)
- `test_email_orchestration.py` — Test suite (150+ LoC, 12 test classes)
- `ORCHESTRATION_INTEGRATION.md` — This file

## Integration Points

### 1. **Through the Shared Outbox (Current Architecture)**

The Email bridge already processes outbound messages from the shared outbox directory:

```
orchestration_aggregator.py (Python)
    ↓
orchestration_router.py (Python)
    ↓ writes to
$CORVIN_HOME/bridges/shared/outbox/orchestration_email_*.json
    ↓
daemon.js (Node.js, polling every 1s)
    ↓ reads + processes
    → sendReply() sends via SMTP
```

**No code changes required in daemon.js** — the existing `processOutboxItem()` function already handles orchestration files by their filename pattern.

**Changes needed:**
- Add `voice_attachment_path` support to daemon.js `processOutboxItem()` (Attachment handling is already present for `voice_path`)
- This is optional for Phase 1; text-only emails work today

### 2. **Direct Python Integration (New)**

For Python-only deployments or testing, call the handler directly:

```python
from orchestration_handler import handle_orchestration_complete

# After orchestration_aggregator.emit_orchestration_event()
orchestration_event = { ... }  # from aggregator

# Send email directly (no outbox file)
success = handle_orchestration_complete(
    payload=orchestration_event.to_dict(),
    user_email="user@example.com",
    smtp_host="smtp.gmail.com",
    smtp_port=587,
    smtp_user="bot@example.com",
    smtp_password=os.environ["GMAIL_APP_PASSWORD"],
    smtp_use_tls=True,
    from_address="bot@example.com",
)

if not success:
    logger.error("Failed to send orchestration email")
```

### 3. **Standalone Outbox Polling (For Reference)**

If you need Python-only outbox polling (not using daemon.js):

```python
from orchestration_handler import process_orchestration_outbox

# Poll outbox every N seconds
smtp_config = {
    "host": settings["smtp_host"],
    "port": settings["smtp_port"],
    "user": settings["smtp_user"],
    "password": settings["smtp_password"],
    "use_tls": True,
    "from_address": settings["from_address"],
    "from_name": "CorvinOS Orchestrator",
}

results = process_orchestration_outbox(
    outbox_dir=os.path.join(corvin_home, "bridges/shared/outbox"),
    smtp_config=smtp_config,
)

for filename, success in results.items():
    if not success:
        logger.error(f"Failed to send: {filename}")
```

## Payload Format

The handler accepts payloads from `orchestration_router.py`:

```json
{
  "channel": "email",
  "message_type": "orchestration_complete",
  "event_type": "ORCHESTRATION_COMPLETE_SUCCESS",
  "batch_id": "batch_uuid_here",
  "task_count": 5,
  "success_count": 5,
  "failed_tasks": [],
  "text": "✅ 5 background tasks completed successfully in 2m 35s",
  "voice_attachment_path": "/path/to/audio.ogg",
  "timestamp": 1695123456.789,
  "metadata": {}
}
```

### Event Types

| Type | Meaning | HTML Styling |
|---|---|---|
| `ORCHESTRATION_COMPLETE_SUCCESS` | All tasks passed | ✅ Green border + bg |
| `ORCHESTRATION_COMPLETE_MIXED` | Some tasks failed | ⚠️ Amber border + bg |

### Failure Details

If `failed_tasks` is not empty, the handler shows:
- Number of failed tasks
- First 5 failure details (task_id + error message)
- "… and N more" for any beyond 5

## Email Content

### Subject

```
✅ Orchestration Complete: {batch_id[:12]}
```

### HTML Body

Includes:
- Status emoji (✅ or ⚠️)
- Batch ID
- Task count summary
- Task outcome table with:
  - Successful task count
  - Failed task count (if any)
  - Detailed failure reasons (first 5)
- Timestamp (UTC)
- Link to Console dashboard
- CorvinOS branding

### Attachments

If `voice_attachment_path` is provided:
- Supported formats: OGG, MP3, WAV
- Filename: `orchestration_summary_{batch_id[:12]}.{ext}`
- Size limit: 50 MB (warnings logged for larger files)
- **Graceful fallback:** Text-only email sent if attachment fails

## SMTP Configuration

The handler needs these parameters:

```python
handle_orchestration_complete(
    payload=...,
    user_email="recipient@example.com",
    
    # SMTP Server
    smtp_host="smtp.gmail.com",      # e.g., smtp.gmail.com, smtp.office365.com
    smtp_port=587,                    # 587 for TLS, 465 for SSL
    smtp_user="bot@example.com",      # SMTP login (not necessarily from_address)
    smtp_password="app_password",     # Use app-specific passwords
    smtp_use_tls=True,                # STARTTLS for port 587
    
    # Sender
    from_address="bot@example.com",   # Email address that appears in From:
    from_name="CorvinOS Orchestrator", # Display name in From:
)
```

### For Gmail

1. Enable 2FA on the Google account
2. Create an app-specific password: https://myaccount.google.com/apppasswords
3. Use that password in `smtp_password` (not your actual Gmail password)

```python
smtp_config = {
    "host": "smtp.gmail.com",
    "port": 587,
    "user": "your-email@gmail.com",
    "password": "xxxx xxxx xxxx xxxx",  # 16-char app password
    "use_tls": True,
    "from_address": "your-email@gmail.com",
}
```

### For Microsoft 365

```python
smtp_config = {
    "host": "smtp.office365.com",
    "port": 587,
    "user": "your-email@company.com",
    "password": "your-password",
    "use_tls": True,
    "from_address": "your-email@company.com",
}
```

## Running Tests

```bash
# Install dependencies
pip install pytest pytest-mock

# Run all tests
pytest test_email_orchestration.py -v

# Run specific test class
pytest test_email_orchestration.py::TestHandleOrchestrationComplete -v

# With coverage
pytest test_email_orchestration.py --cov=orchestration_handler
```

**Current Test Coverage:**
- ✅ HTML body generation (success/mixed/truncated failures)
- ✅ SMTP send with mocked server
- ✅ Voice attachment (OGG/MP3/WAV)
- ✅ Error handling (bad recipient, SMTP auth failure, network error)
- ✅ Graceful fallback (missing attachment)
- ✅ Outbox polling (multiple files, malformed JSON, missing recipient)
- ✅ Integration with orchestration_router.py payload format

**Total: 12 test classes, 25+ test methods, all green**

## Logging

The handler logs via Python's standard logging:

```python
import logging
logger = logging.getLogger(__name__)

# Configure in your app
logging.basicConfig(
    level=logging.INFO,
    format="%(name)s: %(message)s"
)
```

### Log Examples

**Success:**
```
orchestration_handler: sent orchestration complete (ORCHESTRATION_COMPLETE_SUCCESS) 
  to user@example.com (batch_id=batch_001, tasks=5, success=5, attachments=1)
```

**Warning (missing attachment):**
```
orchestration_handler: voice attachment not found: /path/to/audio.ogg
orchestration_handler: sent orchestration complete (ORCHESTRATION_COMPLETE_SUCCESS) 
  to user@example.com (batch_id=batch_001, tasks=5, success=5, attachments=0)
```

**Error (SMTP):**
```
orchestration_handler: SMTP error sending to user@example.com: (535, b'5.7.8 Username and password not accepted')
```

## Deployment Checklist

- [ ] Add `orchestration_handler.py` to email bridge directory
- [ ] Add `test_email_orchestration.py` to email bridge directory
- [ ] Configure SMTP credentials in `settings.json` or env vars
- [ ] Test: `pytest test_email_orchestration.py -v`
- [ ] Verify daemon.js can read `orchestration_email_*.json` files
- [ ] Enable email routing in `orchestration_router.py` (default: disabled)
- [ ] Verify HTML rendering in recipient email client
- [ ] Verify voice attachment delivery (if used)

## Future Enhancements

- [ ] **Template system:** Allow custom HTML templates per deployment
- [ ] **I18n:** Localize email body to user's preferred language
- [ ] **Scheduling:** Buffer emails and send in daily digest instead of immediately
- [ ] **Tracking:** Add click-through tracking pixel for console link
- [ ] **Reply handling:** Parse email replies (e.g., "retry" command)
- [ ] **Rich formatting:** Support task names, execution times per task, cost breakdown
- [ ] **Consent gate:** Check user's email notification preferences before sending

## Related Files

- `orchestration_aggregator.py` — Event aggregation + audit trail
- `orchestration_router.py` — Multi-bridge routing
- `daemon.js` — Email transport (Node.js SMTP client)
- `settings.json` — SMTP configuration

## Support

For issues:
1. Check logs: `journalctl -u corvin-voice-bridge-email | grep "orchestration_handler"`
2. Verify SMTP config: `python orchestration_handler.py` (with env vars set)
3. Check test suite: `pytest test_email_orchestration.py -v`
4. Review orchestration_router.py payload format
