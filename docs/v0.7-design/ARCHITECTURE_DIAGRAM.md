# CorvinOS v0.7 Architecture Diagrams

**Release:** Plugin Ecosystem v0.7  
**Status:** Design Phase  
**Purpose:** Visual architecture documentation for plugin sandboxing, marketplace, and governance.

---

## 1. Plugin Lifecycle Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        MARKETPLACE DISCOVERY                        │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Console UI: Plugin Marketplace Browse                       │   │
│  │  - Search/Filter by category                                │   │
│  │  - View ratings, reviews, downloads                         │   │
│  │  - Brain recommendation engine (affinity-based)             │   │
│  │  - Click "Install" button                                   │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│              PLUGIN REGISTRY & METADATA STORE                       │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Plugin Registry (sqlite)                                    │   │
│  │  - id, name, version, category                              │   │
│  │  - author_id, rating, review_count                          │   │
│  │  - source_url (git repo or tarball)                         │   │
│  │  - manifest.json (API version, required perms)              │   │
│  │  - installed_at, enabled, config                            │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│          SANDBOX BOOTSTRAP & CAPABILITY DROPPING                    │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ 1. Download plugin (signed tarball, SHA256 verified)        │   │
│  │ 2. Extract to ~/.corvin/plugins/<plugin_id>/                │   │
│  │ 3. Create UID mapping (plugin runs as unique UID)           │   │
│  │ 4. Load seccomp ruleset (per plugin type)                   │   │
│  │ 5. Drop capabilities (CAP_SYS_ADMIN, CAP_NET_ADMIN, etc.)  │   │
│  │ 6. Mount /tmp with noexec, nosuid, nodev                    │   │
│  │ 7. Set cgroup limits (CPU 20%, RAM 512MB)                   │   │
│  │ 8. Spawn plugin process (isolated)                          │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│              PLUGIN EXECUTION (SANDBOXED)                           │
│  ┌──────────────────┐  ┌──────────────────┐  ┌─────────────────┐   │
│  │ Whitelisted RW   │  │ Denied Syscalls  │  │ Resource Limits │   │
│  │                  │  │                  │  │                 │   │
│  │ • /tmp           │  │ • execve()       │  │ • CPU: 20%      │   │
│  │ • ~/operator/...│  │ • fork()         │  │ • RAM: 512MB    │   │
│  │ • plugin cache   │  │ • open(/proc)   │  │ • FD: 512       │   │
│  │ • config dir     │  │ • socket()      │  │ • Timeout: 30s  │   │
│  │                  │  │ • ptrace()      │  │                 │   │
│  │ [read-only]      │  │ • unshare()     │  │                 │   │
│  │ • core libs      │  │ • mount()       │  │                 │   │
│  │ • plugin code    │  │ • modprobe()    │  │                 │   │
│  └──────────────────┘  └──────────────────┘  └─────────────────┘   │
│                                                                      │
│  Plugin API: Plugin → Brain via IPC (Unix socket)                   │
│  - Call: brain.suggest_task()                                       │
│  - Call: brain.get_operator_affinity()                              │
│  - Call: brain.log_event(type, payload)                             │
│  - Response: JSON via socket (timeout 5s)                           │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│          SANDBOX ESCAPE VERIFICATION (ADVERSARIAL)                  │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ For each plugin:                                            │   │
│  │  1. Try 100 exploits (buffer overflow, TOCTOU, etc.)       │   │
│  │  2. Monitor with strace (verify deny-all syscalls)         │   │
│  │  3. Verify process isolation (no cross-plugin file access) │   │
│  │  4. Verify resource limits enforced (OOM killed at 512MB)  │   │
│  │  5. Verify no privilege escalation (UID unchanged)         │   │
│  │  6. If ANY exploit succeeds: BLOCK plugin, alert operator  │   │
│  │  7. If ALL denied: Mark plugin as SANDBOX-VERIFIED         │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│         PLUGIN REGISTRY & COMMUNITY GOVERNANCE                      │
│  ┌──────────────────────┐  ┌──────────────────────┐                │
│  │ Plugin Developer     │  │ Community Moderation │                │
│  │                      │  │                      │                │
│  │ • Upload plugin code │  │ • Auto-scan for CVE  │                │
│  │ • Publish to mkpt.   │  │ • Human review board │                │
│  │ • See usage metrics  │  │ • Flag malicious     │                │
│  │ • Receive royalties  │  │ • Remove in <1 day   │                │
│  └──────────────────────┘  └──────────────────────┘                │
│                                                                      │
│  Rating: 1-5 stars (operator votes)                                 │
│  Crash reports: Auto-collected, aggregated                          │
│  Revenue: Author gets 10% of plugin purchase price                  │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 2. Seccomp Filter Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Plugin Process (UID 2000+, sandboxed)                      │
│  Running: python plugin.py                                  │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼ (syscall)
┌─────────────────────────────────────────────────────────────┐
│  Seccomp Whitelist Filter                                   │
│  (Compiled BPF, kernel enforced)                            │
│                                                              │
│  ALLOWED:                                                    │
│  ├─ read(), write(), pread64(), pwrite64()                  │
│  ├─ open(), openat() [whitelist: /tmp, ~/operator/]        │
│  ├─ close(), fstat(), lstat()                               │
│  ├─ mmap(), mprotect(), brk() [memory mgmt]                │
│  ├─ clock_gettime(), gettimeofday()                         │
│  ├─ select(), poll(), epoll_*() [I/O multiplexing]         │
│  ├─ connect() [to Unix socket only, no TCP/UDP]            │
│  ├─ send(), recv() [socket I/O only]                        │
│  ├─ prctl() [limited: only get_dumpable, get_seccomp]      │
│  ├─ exit_group(), exit()                                    │
│  └─ (30+ other safe syscalls)                               │
│                                                              │
│  DENIED (EACCES / SIGSYS):                                 │
│  ├─ execve(), fork(), clone() [no code execution]          │
│  ├─ open(/proc) [no process introspection]                  │
│  ├─ open(/sys) [no kernel config inspection]               │
│  ├─ socket() [TCP/UDP not allowed]                          │
│  ├─ bind() [no server ports]                                │
│  ├─ mount(), unmount() [no mount operations]                │
│  ├─ ptrace() [no debugging]                                 │
│  ├─ unshare(), setns() [no namespace escape]               │
│  ├─ capset() [no capability elevation]                      │
│  ├─ unlink(), rmdir() [no file deletion in core dirs]      │
│  ├─ chmod(), chown() [no permission changes]               │
│  ├─ modprobe(), init_module() [no kernel modules]          │
│  └─ (50+ other dangerous syscalls denied)                   │
│                                                              │
│  ACTION: If denied → SIGSYS → process terminates            │
└──────────────────────────────────────────────────────────────┘
```

---

## 3. Plugin API v2 Contract

```
┌────────────────────────────────────────────────────────────────┐
│              PLUGIN API v2 (Stable Contract)                  │
│                                                                │
│  Versioning: MAJOR.MINOR.PATCH (e.g., 2.1.3)                 │
│  Deprecation: 2-version grace period (v2 → v3 → v4 removal)  │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  EXPORTS (plugin provides):                                    │
│  ├─ class Plugin:                                              │
│  │  ├─ def initialize(config: PluginConfig) → None            │
│  │  ├─ def execute(task: Task) → Result                       │
│  │  ├─ def on_event(event: PluginEvent) → None                │
│  │  ├─ def cleanup() → None                                    │
│  │  └─ version: str = "2.0.0"                                  │
│  │                                                              │
│  │  manifest.json:                                             │
│  │  {                                                          │
│  │    "id": "plugin-auth",                                     │
│  │    "version": "2.1.0",                                      │
│  │    "api_version": "2.0",                                    │
│  │    "author": "Verifier",                                    │
│  │    "required_permissions": ["read_operator_affinity"],      │
│  │    "sandbox_required": true                                 │
│  │  }                                                          │
│                                                                │
│  IMPORTS (plugin calls):                                       │
│  ├─ brain.suggest_task(task_type: str) → List[str]            │
│  ├─ brain.get_operator_affinity() → OperatorFingerprint       │
│  ├─ brain.log_event(type: str, payload: dict) → None          │
│  └─ brain.get_config(key: str) → Any                          │
│                                                                │
│  IPC: Unix domain socket (/tmp/.brain-ipc.sock)               │
│  Timeout: 5 seconds per call                                  │
│  Protocol: JSON-RPC 2.0 over AF_UNIX                          │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

---

## 4. Community Governance Data Flow

```
┌────────────────────────────────┐
│ Plugin Author (Developer)      │
│ ├─ Write plugin code           │
│ ├─ Run locally (no sandbox yet)│
│ ├─ Create manifest.json        │
│ └─ Push to GitHub              │
└───────────────┬────────────────┘
                │
                ▼
┌────────────────────────────────┐
│ Plugin Registry                │
│ ├─ Receive git URL             │
│ ├─ Fetch + verify SHA256       │
│ ├─ Compile manifest metadata   │
│ └─ Queue for review            │
└───────────────┬────────────────┘
                │
                ▼
┌────────────────────────────────┐
│ Automated Security Scan        │
│ ├─ SAST (static analysis)      │
│ ├─ Dependency CVE check        │
│ ├─ License compliance          │
│ └─ Malware signature scan      │
└───────────────┬────────────────┘
                │ (Pass)
                ▼
┌────────────────────────────────┐
│ Human Review Board             │
│ ├─ Read plugin code (15 min)   │
│ ├─ Check for obfuscation       │
│ ├─ Verify manifest claims      │
│ ├─ Vote (Approve / Reject)     │
│ └─ 2/3 majority required       │
└───────────────┬────────────────┘
                │ (Approved)
                ▼
┌────────────────────────────────┐
│ Publish to Marketplace         │
│ ├─ Assign plugin ID            │
│ ├─ Create listing page         │
│ ├─ Enable operator installation│
│ └─ Enable analytics dashboard  │
└───────────────┬────────────────┘
                │
                ▼
┌────────────────────────────────┐
│ Operators Install & Rate       │
│ ├─ Browse marketplace          │
│ ├─ Read reviews from others    │
│ ├─ Click "Install"             │
│ ├─ Plugin runs in sandbox      │
│ ├─ Rate 1-5 stars             │
│ └─ Post feedback               │
└───────────────┬────────────────┘
                │
                ▼
┌────────────────────────────────┐
│ Malicious Plugin Detection     │
│ ├─ Crash reports (auto-sent)   │
│ ├─ Operator flags (manual)     │
│ ├─ Security team review (<1d)  │
│ ├─ Vote (Keep / Remove)        │
│ └─ If Remove: Purge from mkpt. │
└────────────────────────────────┘

Developer Revenue:
├─ Monthly: Download count × $0.10
├─ Monthly: Crash-free bonus (+10%)
└─ Quarterly: Rating bonus (5-star +20%)
```

---

## 5. Data Flow: Affinity-Based Suggestions

```
┌─────────────────────────────────┐
│ Operator Fingerprint (v0.6)     │
│ ├─ Risk Tolerance: 0.7          │
│ ├─ Speed Preference: 0.5        │
│ ├─ Communication Style: 0.8     │
│ └─ Task Affinity:               │
│    ├─ Auth: 0.85 (strong)       │
│    ├─ Data: 0.60 (neutral)      │
│    ├─ UI: 0.40 (weak)           │
│    └─ Logic: 0.70 (strong)      │
└─────────────────┬───────────────┘
                  │
                  ▼
┌─────────────────────────────────┐
│ Brain (Recommendation Engine)   │
│ ├─ Get operator affinity        │
│ ├─ Query plugin DB              │
│ │  ├─ Get plugin ratings        │
│ │  ├─ Get crash rates           │
│ │  └─ Filter by category        │
│ ├─ Score plugins:               │
│ │  score = affinity[category] × │
│ │           rating ×              │
│ │           (1 - crash_rate)    │
│ └─ Top 3 plugins ranked         │
└─────────────────┬───────────────┘
                  │
                  ▼
┌─────────────────────────────────┐
│ Console: Plugin Suggestions     │
│ ├─ Show top 3 plugins           │
│ ├─ Display reason (affinity)    │
│ ├─ Show rating + reviews        │
│ ├─ "Install" button per plugin  │
│ └─ "Dismiss" link               │
└─────────────────────────────────┘
```

---

## 6. Database Schema (Simplified)

```sql
-- Plugin Registry
CREATE TABLE plugins (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    category TEXT,
    author_id TEXT,
    git_url TEXT,
    manifest_json TEXT,  -- JSON: api_version, permissions, etc.
    sandbox_verified BOOLEAN,
    rating_count INT DEFAULT 0,
    rating_avg FLOAT DEFAULT 0,
    crash_rate FLOAT DEFAULT 0,
    downloads_total INT DEFAULT 0,
    revenue_total DECIMAL DEFAULT 0,
    published_at DATETIME,
    updated_at DATETIME
);

-- Plugin Installation per Operator
CREATE TABLE plugin_installations (
    id TEXT PRIMARY KEY,
    tenant_id TEXT,
    operator_id TEXT,
    plugin_id TEXT REFERENCES plugins(id),
    installed_at DATETIME,
    enabled BOOLEAN DEFAULT TRUE,
    config_json TEXT,  -- Plugin-specific config
    crash_count INT DEFAULT 0,
    last_crash_at DATETIME,
    audit_hash TEXT  -- Hash-chain link
);

-- Plugin Reviews & Ratings
CREATE TABLE plugin_ratings (
    id TEXT PRIMARY KEY,
    plugin_id TEXT REFERENCES plugins(id),
    operator_id TEXT,
    rating INT (1-5),
    review_text TEXT,
    rated_at DATETIME,
    helpful_count INT DEFAULT 0
);

-- Plugin Events (crash, error, etc.)
CREATE TABLE plugin_events (
    id TEXT PRIMARY KEY,
    plugin_id TEXT REFERENCES plugins(id),
    operator_id TEXT,
    tenant_id TEXT,
    event_type TEXT,  -- 'crash', 'error', 'timeout', 'success'
    error_message TEXT,
    stack_trace TEXT,
    event_at DATETIME,
    audit_hash TEXT
);
```

---

## References

- **ADRs:** 0387–0390 (plugin ecosystem architecture)
- **Concepts:** 0023–0026 (sandboxing methodology, governance)
- **Layer:** L4 Plugins (docs/claude-ref/layer-plugins.md)
- **GDPR:** Art. 5 (lawfulness), Art. 32 (security measures)

---

**Maintained by:** Claude Code  
**Last Updated:** 2026-08-18  
**Next Review:** v0.7 Week 2 (Sandbox verification complete)
