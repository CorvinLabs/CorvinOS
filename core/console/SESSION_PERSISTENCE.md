# Session Persistence & Recovery Guide

## Overview

CorvinOS Console now features **robust session persistence** and automatic recovery. Sessions survive server restarts, and corrupted sessions are handled gracefully.

## Architecture

### Session Storage
- **Location:** `~/.corvin/global/console/sessions/`
- **Format:** JSON files (one per session)
- **Naming:** `{sid}.json` (Session ID, 43 characters)
- **Permissions:** 0o600 (read/write by owner only)
- **Atomic Writes:** Tempfile + rename (prevents corruption on write)

### Session Lifecycle

```
┌─────────────┐
│  Create     │  user: POST /v1/console/auth/local-login
└──────┬──────┘
       │
       ▼
┌─────────────────────────────────────────┐
│  Persist                                │
│  - Write JSON to disk                   │
│  - Set HttpOnly cookie                  │
│  - Emit audit event                     │
└──────┬──────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────┐
│  Load (per request)                     │
│  - Read cookie                          │
│  - Load JSON from disk                  │
│  - Validate (expiry, permissions)       │
│  - Bump last_seen_at                    │
└──────┬──────────────────────────────────┘
       │
       ▼
┌─────────────┐
│  Expire     │  (idle timeout or absolute)
└──────┬──────┘
       │
       ▼
┌─────────────────────────────────────────┐
│  Clean (periodic or on shutdown)        │
│  - Delete expired sessions              │
│  - Emit audit event                     │
└─────────────────────────────────────────┘
```

## Startup Recovery

On app startup (FastAPI lifespan event):

1. **SessionManager.bootstrap()** is called
2. Scans `~/.corvin/global/console/sessions/` directory
3. For each `{sid}.json`:
   - Validate SID format
   - Parse JSON
   - Check permissions (0o600 on POSIX)
   - Check expiry (absolute + idle timeout)
   - Count statistics
4. Log recovery summary (total, active, expired, corrupted)
5. Return control to app

**Audit Trail:**
- `AUDIT[session.bootstrap]`: Initial scan results
- Individual session loads/skips logged with SID fingerprint

### Example Startup Log

```
🚀 CorvinOS Console starting up...
SessionManager bootstrap starting...
✅ SessionManager bootstrap complete: recovered 5 sessions, 4 active, 1 expired, 0 corrupted
✅ App ready. Sessions: total=5 active=4 expired=1 corrupted=0
```

## Shutdown Cleanup

On app shutdown:

1. **SessionManager.cleanup_expired_sessions()** is called
2. Scans all sessions
3. Deletes files where:
   - File mtime > max_age_s ago (default: 86400s = 24h)
   - Session is expired (last_seen_at or absolute timeout exceeded)
4. Log results

## Robustness Features

### Corruption Handling
- **Invalid SID format:** Skipped, marked as corrupted
- **Malformed JSON:** Caught, file ignored, logged
- **Wrong permissions (0o700, 0o644):** Detected, rejected
- **Concurrent writes:** Atomic rename prevents partial writes
- **Concurrent reads:** File lock-free (immutable after close)

### Retry Logic
- Session write failures counted per SID fingerprint
- After 3 consecutive failures: Error logged
- Request still proceeds (using stale last_seen_at)
- Counter resets on successful write

### Windows Compatibility
- Mode check (0o600) skipped on Windows (ACL-based)
- Atomic rename with exponential backoff on file-in-use

## Configuration

### Environment Variables
- `CORVIN_LOCAL_AUTOLOGIN=1` (default): Enable localhost auto-login
- `CORVIN_LOCAL_AUTOLOGIN=0`: Disable auto-login (requires external auth)
- `CORVIN_HOME` (default: `~/.corvin`): Session storage root

### Timeout Constants (in auth.py)
```python
IDLE_TIMEOUT_S = 60 * 60                  # 1 hour
ABSOLUTE_TIMEOUT_S = 8 * 60 * 60          # 8 hours
PERSISTENT_TIMEOUT_S = 90 * 24 * 60 * 60  # 90 days ("remember me")
```

## Audit Events

### Session Creation
```
AUDIT[session.created] sid_fp=abc123 tenant=_default tier=owner via=local-login persistent=false
```

### Session Load
```
AUDIT[session.loaded] sid_fp=abc123 tenant=_default idle_age=123.5
```

### Session Ended
```
AUDIT[session.ended] sid_fp=abc123 reason=logout
```

### Bootstrap
```
AUDIT[session.bootstrap] recovered=5 active=4 expired=1 corrupted=0
```

## Troubleshooting

### Sessions Lost After Restart

**Symptom:** User gets "no session" error after server restart

**Cause:** Session files deleted or corrupted

**Solution:**
1. Check if `~/.corvin/global/console/sessions/` directory exists
2. Check permissions: `ls -la ~/.corvin/global/console/sessions/`
3. Check for corrupted files: `for f in *.json; do python3 -m json.tool $f > /dev/null || echo "CORRUPT: $f"; done`
4. Check server logs for bootstrap errors: `grep -i "session\|bootstrap" ~/.corvin/logs/corvin.log`

### Sessions Stuck in "Expired" State

**Symptom:** Session loads but immediately rejected as expired

**Cause:** System clock skew or corrupted expires_at value

**Solution:**
1. Verify system time: `date` should match server
2. Inspect session file: `python3 -m json.tool ~/.corvin/global/console/sessions/{sid}.json | grep expires_at`
3. If expires_at is in the past, either restart (cleanup will remove it) or delete manually

### "Permission Denied" on Session File (POSIX)

**Symptom:** Sessions created but can't be read

**Cause:** Mode is not 0o600 (readable/writable by owner only)

**Solution:**
1. Check permissions: `ls -la ~/.corvin/global/console/sessions/`
2. Fix manually: `chmod 600 ~/.corvin/global/console/sessions/*.json`
3. Check umask: `umask` should be 0o077 or tighter

## Performance Notes

- **Recovery time:** O(N) where N = number of session files
  - Typical: <100ms for 1000 sessions
  - Includes JSON parse + validation + expiry check
- **Concurrent requests:** No lock contention (each session isolated)
- **Disk I/O:** Atomic writes use tempfile + rename (safe on all OSes)
- **Memory:** Sessions loaded on-demand, not cached in RAM

## Future Improvements

Planned enhancements (ADR-0540 Phase B):
- [ ] In-memory cache of active sessions (optional)
- [ ] Session encryption at rest
- [ ] Session compression for large-scale deployments
- [ ] TTL-based automatic cleanup (cron job)
- [ ] Session transfer for zero-downtime restart
- [ ] Session replication for HA clusters

## Related Code

- `auth.py`: Core session creation, loading, validation
- `session_manager.py`: Lifecycle manager, recovery, cleanup, audit
- `routes/auth_routes.py`: HTTP endpoints (login, logout, whoami)
- `app.py`: FastAPI lifespan integration
- `deps.py`: Session dependency injection (require_session)
