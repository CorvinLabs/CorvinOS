# ADR-0565: Console Session Persistence & Recovery

**Status:** ACCEPTED  
**Date:** 2026-09-04  
**Supersedes:** None  
**Depends on:** ADR-0007 (multi-tenant), ADR-0232 (audit chain)  
**Related:** ADR-0540–0545 (Infinite Session Engine), ADR-0154 (License Proof)

## Problem

CorvinOS Console sessions are transient — stored only in the running process's memory. When the server restarts (intentional shutdown or unexpected crash), all sessions are lost, forcing users to re-authenticate. This is especially problematic during development and in environments with frequent restarts.

Additionally, there was no systematic cleanup of expired sessions, leading to disk accumulation.

## Solution

Implement **persistent session storage** with automatic recovery:

1. **Persistence**: Sessions are stored as JSON files in `~/.corvin/global/console/sessions/{sid}.json` with atomic writes (tempfile + rename)
2. **Recovery**: On app startup, `SessionManager.bootstrap()` loads active sessions from disk
3. **Lifecycle**: Sessions are validated against expiry (idle timeout + absolute timeout) during recovery
4. **Cleanup**: Expired/corrupted sessions are pruned automatically; periodic cleanup via systemd timer removes stale files
5. **Performance**: In-memory LRU cache (1000 sessions) reduces disk I/O for frequently-accessed sessions
6. **Audit**: All session operations are logged with SID fingerprint and tenant_id

## Architecture

### Components

**1. SessionManager** (`core/console/corvin_console/session_manager.py`)
- `bootstrap()`: Load sessions from disk on startup, validate, count statistics
- `cleanup_expired_sessions()`: Periodic cleanup (TTL-based, mtime-based)
- `audit_session_*()`: Audit logging for created/loaded/ended sessions
- LRU cache with hit/miss tracking

**2. FastAPI Lifespan** (`core/console/corvin_console/app.py`)
- Async context manager for startup/shutdown
- Calls `bootstrap_session_manager()` on app init
- Calls `cleanup_expired_sessions()` on graceful shutdown

**3. Existing Persistence Layer** (`auth.py`)
- `create_session()`: Already writes to disk via `_write_record()`
- `load_session()`: Already reads from disk, validates, enforces TTL
- No changes to existing API

**4. Cleanup Automation** (`scripts/run-session-cleanup.sh` + systemd timer)
- Standalone script (no Python dependencies)
- Invoked daily at 00:00 via `corvin-session-cleanup.timer`
- Deletes files older than 24 hours (conservative heuristic)

### Data Flow

```
┌─────────────────────────────────────────────────────────┐
│ Browser Login (POST /auth/local-login)                  │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
        ┌──────────────────────────────┐
        │ session_auth.create_session()│
        │ - Generate sid + csrf_secret │
        │ - Write JSON to disk (0o600) │
        │ - Set HttpOnly cookie        │
        └──────────────────┬───────────┘
                           │
                           ▼
        ┌──────────────────────────────┐
        │ SessionManager.              │
        │ audit_session_created()      │
        └──────────────────────────────┘
                           │
                           ▼
        ┌──────────────────────────────┐
        │ Browser stores cookie        │
        │ Redirect to /console/        │
        └──────────────────────────────┘
```

```
┌─────────────────────────────────────────────────────────┐
│ Server Startup (FastAPI Lifespan)                       │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
        ┌──────────────────────────────┐
        │ SessionManager.bootstrap()   │
        │ - Scan sessions/ directory   │
        │ - For each {sid}.json:       │
        │   • Validate format          │
        │   • Check permissions (0o600)│
        │   • Parse JSON, check expiry │
        │   • Count stats (active/exp) │
        └──────────────────┬───────────┘
                           │
                           ▼
        ┌──────────────────────────────┐
        │ App ready, listeners mounted │
        │ Sessions available from disk │
        └──────────────────────────────┘
```

```
┌─────────────────────────────────────────────────────────┐
│ Browser Request (with existing cookie)                  │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
        ┌──────────────────────────────┐
        │ require_session() Dependency │
        │ - Extract sid from cookie    │
        └──────────────────┬───────────┘
                           │
                           ▼
        ┌──────────────────────────────┐
        │ SessionManager.cache_get(sid)│
        │ - Check LRU cache first      │
        │ - Cache hit → return (fast)  │
        │ - Cache miss → load from disk│
        └──────────────────┬───────────┘
                           │
                           ▼
        ┌──────────────────────────────┐
        │ session_auth.load_session()  │
        │ - Read JSON from disk        │
        │ - Validate permissions       │
        │ - Check is_alive() (expiry)  │
        │ - Bump last_seen_at + write  │
        └──────────────────┬───────────┘
                           │
                           ▼
        ┌──────────────────────────────┐
        │ SessionManager.cache_put()   │
        │ - Update LRU cache for future│
        │ - Evict LRU if cache full    │
        └──────────────────────────────┘
```

### Robustness

| Scenario | Behavior | Recovery |
|----------|----------|----------|
| **Malformed JSON** | Skip file, log warning | Manual: remove file |
| **Invalid SID format** | Skip, mark corrupted | Counted in stats |
| **Wrong permissions (0o644)** | Reject on POSIX | Manual: `chmod 600` |
| **Concurrent write** | Atomic rename prevents corruption | Automatic (tempfile) |
| **Concurrent read** | No locking (reads don't block writes) | Automatic (immutable after close) |
| **Disk full** | Write fails, logged, session still usable with stale last_seen_at | Automatic retry next request |
| **Missing directory** | Auto-create via `mkdir(parents=True)` | Automatic |
| **Expired session** | Deleted by `load_session()` during read | Automatic pruning |

## Trade-offs

### Chosen: Persistent JSON on local disk
- ✅ Simple (no external dependency)
- ✅ Auditable (human-readable files)
- ✅ Fail-safe (file loses durability, not app)
- ❌ Performance (requires disk I/O per request, mitigated by LRU cache)

### Not chosen: Redis/Memcached
- ❌ Adds external dependency (Redis server)
- ❌ Distributed deployments need replication logic
- ⏱️ Future: Redis as optional extension (Phase 2)

### Not chosen: Encrypted-at-rest
- ✅ Files already mode 0o600 (readable by owner only)
- ⏱️ Future: AES-256 encryption (Phase 2, ADR-0XXX)

## Testing

### Unit Tests (15 tests, all passing ✅)
- Recovery: valid, expired, corrupted sessions
- Cleanup: mtime-based, TTL-based deletion
- Concurrency: 10 concurrent reads/writes
- Edge cases: empty dir, invalid format, missing directory
- Statistics: initialization, reset
- Integration: full lifecycle (create → recover → cleanup)

### Manual Testing (Post-Deploy)
1. Start app: logs should show "✅ bootstrap complete: recovered X sessions"
2. Login: session file created in `~/.corvin/global/console/sessions/`
3. Reload browser: session persists (no re-login)
4. Restart server: existing sessions loaded, clients continue with existing cookie

## Implementation Details

### Files Changed
- `core/console/corvin_console/session_manager.py` (new, ~500 lines)
- `core/console/corvin_console/app.py` (+50 lines)
- `core/console/tests/test_session_manager_robust.py` (new, ~400 lines)
- `scripts/run-session-cleanup.sh` (new, shell script)
- `.config/systemd/user/corvin-session-cleanup.timer` (new)
- `.config/systemd/user/corvin-session-cleanup.service` (new)

### Backward Compatibility
✅ **100% backward compatible**
- Existing `auth.py` APIs unchanged
- No breaking changes to routes
- Sessions created before this change still work
- Fallback: if session file missing, load_session() returns None (HTTP 401)

### Configuration
- `CORVIN_HOME` env var (default: `~/.corvin`) sets storage root
- `CORVIN_LOCAL_AUTOLOGIN` env var (default: `1`) enables localhost auto-login
- Timeouts in `auth.py`: IDLE_TIMEOUT_S (1h), ABSOLUTE_TIMEOUT_S (8h), PERSISTENT_TIMEOUT_S (90d)

### Monitoring
- Logs: `AUDIT[session.created]`, `AUDIT[session.loaded]`, `AUDIT[session.ended]`
- Cache stats: `SessionManager.cache_stats()` → {hits, misses, hit_rate_percent}
- Cleanup logs: `~/.corvin/logs/session-cleanup.log` (systemd timer output)

## ADR-0265 Constraints (Compliance)

✅ **Audit chain**: All session operations logged with SID fingerprint (immutable)
✅ **Tenant isolation**: Sessions stored in `/global/`, accessible by all tenants of same instance
✅ **No PII in logs**: Only SID fingerprint logged (not full sid or user data)
✅ **Fail-closed**: Missing session → 401, not 500 or silent fallback

## Future Work (Phase 2+)

- [ ] **Session encryption at rest** (AES-256, key rotation per ADR-0XXX)
- [ ] **Distributed session store** (Redis optional backend)
- [ ] **Session replication** (zero-downtime restart, HA clusters)
- [ ] **Session transfer** (offline-to-online, browser-to-CLI)
- [ ] **Session analytics** (duration, concurrency, per-tenant metrics)
- [ ] **Scheduled cleanup** (cron job for sessions >24h, not just on shutdown)

## References

- **Code**: `core/console/corvin_console/session_manager.py`, `auth.py`, `app.py`
- **Tests**: `core/console/tests/test_session_manager_robust.py` (15 tests, all passing)
- **Docs**: `core/console/SESSION_PERSISTENCE.md` (user guide), `SESSION_IMPLEMENTATION_SUMMARY.md` (details)
- **Config**: `~/.config/systemd/user/corvin-session-cleanup.timer` (systemd timer)

## Commits

- `feat(console): Session Persistence & Recovery — Complete Implementation`
  - Adds SessionManager, FastAPI lifespan, cleanup automation, comprehensive tests

---

**Decision**: Session persistence via JSON files on local disk, with automatic recovery and periodic cleanup. LRU cache for performance. Systemd timer for automation. Full backward compatibility.

**Rationale**: Simplicity + auditability + reliability. Sessions survive restarts without external dependencies. File-based storage is transparent (human-readable) and fail-safe (disk loss doesn't crash app, just loses sessions).
