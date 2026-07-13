# Layer 33 — Session Artifact Memory — full reference

ADR: `Corvin-ADR: decisions/0040-session-artifact-memory.md`
Library: `operator/forge/forge/artifacts.py`
MCP handlers: `operator/forge/forge/mcp_server.py`
Auto-register hook: `operator/voice/hooks/path_gate.py` (PostToolUse branch)
Tests: `operator/forge/tests/test_artifacts.py`, `operator/forge/tests/test_artifact_e2e.py`

This file covers what the ADR doesn't: per-tool semantics, error
contracts, the auto-register decision tree, the pre-warn protocol,
and the troubleshooting matrix.

---

## Storage layout in detail

```
<corvin_home>/tenants/<tid>/
├── sessions/<bridge>:<chat>/
│   └── artifacts/
│       ├── .manifest.jsonl              # append-only, fcntl-locked
│       ├── .manifest.lock               # fcntl file (zero bytes)
│       └── <2-char-shard>/<sha-prefix>_<sanitized-name>
├── global/
│   └── artifacts/                       # project-scope (pinned)
│       ├── .manifest.jsonl              # separate chain
│       └── ...
```

**Why sha-prefix sharding:** prevents `ls`-blowups when one session
holds hundreds of artifacts and keeps inode-density per dir below
typical FS thresholds (~10k).

**Why sanitized name in the path:** human-readable when an operator
inspects the FS directly. The actual key for retrieval is `name` in
the manifest entry — collisions on `name` get a numeric suffix.

---

## Manifest schema

```python
@dataclass(frozen=True)
class ArtifactEntry:
    ts: float                 # epoch seconds, time.time()
    name: str                 # caller-supplied, may collide → suffix appended
    sha256: str               # 64-char hex
    size: int                 # bytes
    mime: str                 # libmagic-detected
    path_rel: str             # relative to artifacts/
    by_tool: str              # e.g. "forge.gen_pdf" or "voice.user_upload"
    run_id: str               # bridge run-id; empty for manual register
    description: str          # Haiku-4.5-generated, PII-redacted
    tags: list[str]           # operator hints, free-form
    pinned: bool              # False in session manifest, True in global
```

**Append-only invariant:** entries are never edited in place. A pin
operation writes a new entry to the **global** manifest; the
session-side entry remains until session reset. Purge writes a tombstone
event to the audit chain but never rewrites the manifest.

---

## MCP tool reference

### `artifact_list(after_ts=None, mime=None, limit=20)`

Returns metadata-only. Sorted by `ts` descending.

```json
{
  "artifacts": [
    {"name": "...", "mime": "...", "size": ..., "ts": ..., "description": "..."}
  ],
  "truncated": false
}
```

Capped at 20 by default to keep tool-response tokens bounded. If
`truncated=true`, the caller paginates via `after_ts`.

### `artifact_search(query, scope="session")`

`scope` ∈ {`session`, `global`, `all`}. Queries the `artifact_summary`
class of the active tenant's `recall.db` FTS5 index. Returns:

```json
{
  "hits": [
    {"name": "...", "snippet": "...", "rank": -2.34, "scope": "session"}
  ]
}
```

`snippet` is a 60-char window around the FTS5 match. `rank` is the
BM25-style negative score (lower = better).

### `artifact_get(name, max_bytes=65536, encoding="auto")`

`encoding` ∈ {`auto`, `text`, `base64`}.

- `auto`: text-decode if MIME starts with `text/` or is JSON-ish;
  base64 otherwise.
- Hard cap at `max_bytes`. Larger artifacts return:

```json
{
  "too_large": true,
  "size": 1234567,
  "mime": "application/pdf",
  "hint": "Use artifact_extract with range='pages:1-3' or specify max_bytes."
}
```

### `artifact_extract(name, range)`

`range` syntax:

- `pages:N-M` — PDFs, via `pdftotext`. Errors if not PDF.
- `lines:N-M` — text artifacts.
- `bytes:N-M` — raw byte range, base64-encoded.
- `meta` — EXIF / PDF metadata only, no content.

### `artifact_register(path, description=None, tags=None)`

Manual fallback. The caller is responsible for placing `path` already
under `<session>/artifacts/` — the handler refuses paths outside.
Generates description via Haiku-4.5 if `description=None`.

### `artifact_pin(name)`

- Moves the file from session-scope to `global/artifacts/`.
- Appends `pinned: true` entry to global manifest.
- Emits `artifact.pinned` audit event.
- Returns `{"new_path": "..."}`.

---

## Auto-register decision tree

```
PostToolUse(tool, output_path):
    if not path_gate_allowed(output_path):
        return  # Layer 10 already denied; nothing to do
    if path_gate_protected(output_path):
        return  # forge/, skill-forge/, audit chain — never auto-register
    if output_path.is_under(<session>/artifacts/):
        register(output_path, source="path-convention")
        return
    mime = libmagic.from_file(output_path)
    if mime in ARTIFACT_MIMES:
        # Move into <session>/artifacts/ (atomic rename), then register.
        new_path = move_into_session_artifacts(output_path)
        register(new_path, source="mime-detect")
```

`ARTIFACT_MIMES` is configurable in
`<corvin_home>/global/artifacts.config.json::auto_register_mimes`,
defaults to the set listed in the ADR.

**Description generation runs async** in a daemon-thread so the
PostToolUse hook returns immediately. The manifest entry is written
twice: first with `description=""`, then updated (append + tombstone)
once Haiku returns. Readers always see a consistent state.

---

## /reset pre-warn protocol

When `/new` / `/clear` / `/reset` fires and the session has
**unpinned** artifacts:

1. Bridge sends inbox message back to the chat:

   > "⚠️ Diese Session hat 3 ungepinnte Artefakte:
   >   • q3_budget.pdf (184 KB, 2026-05-19 14:23)
   >   • chart.png (42 KB, 2026-05-19 14:30)
   >   • report.csv (8 KB, 2026-05-19 14:35)
   > Pinne mit `/pin <name>` oder bestätige Reset mit `/reset ack`."

2. Reset is deferred until `/reset ack` is received (TTL 5 min, then
   auto-cancel with audit event `artifact.reset_cancelled`).

3. On `/reset ack` → `artifact.session_purged` (CRITICAL) → rmtree.

Operator escape: `/reset force` skips the warn step but still emits
the CRITICAL event.

---

## Configuration

`<corvin_home>/global/artifacts.config.json` (mode 0600, path-gate protected):

```json
{
  "storage_backend": "jsonl",
  "session_artifact_ttl_days": 7,
  "auto_register_mimes": [
    "application/pdf", "image/png", "image/jpeg", "image/webp",
    "image/svg+xml", "text/csv", "text/html",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
  ],
  "description_model": "haiku-4.5",
  "description_max_tokens": 60,
  "description_language": "auto",
  "manifest_lock_timeout_ms": 5000,
  "max_artifact_size_bytes": 104857600,
  "pre_warn_on_reset": true
}
```

`description_language` ∈ {`auto`, `en`, `de`}. `auto` lets Haiku detect
from the first 4 KB of the artifact.

---

## Privacy guarantees

1. **Audit chain never contains:** description text, file content,
   absolute paths outside the artifact tree, MIME-detected language.
2. **`recall.db` description-indexing:** passes through Layer 28's
   `pii_redact()` first. Emails, phone numbers, IBANs, names matched
   by spaCy NER → redacted before INSERT.
3. **MCP tool responses:** `artifact_list` returns `description` —
   that's by design (LLM needs it to find the right artifact). The
   PII-redaction at description-generation time is the load-bearing
   defense.
4. **Pin operation:** the global manifest gets the same description
   that the session manifest had — PII-redaction already applied.

---

## Troubleshooting matrix

| Symptom | Likely cause | Fix |
|---|---|---|
| `artifact_list` returns empty but files exist | Manifest missing or corrupt | `forge.artifacts.reconcile_manifest(<session>)` rebuilds from filesystem |
| Auto-register doesn't fire for new PDFs | MIME not in `auto_register_mimes` | Add MIME to config; restart adapter |
| Description always empty | Haiku helper-model misconfigured | Check `helper_model.default_model` in Layer 29.5 config |
| `artifact.session_purged` missing on /reset | Reset happened before the audit-first hook | Bug; file issue with chain dump |
| FTS5 search misses obvious matches | recall.db not indexed by adapter | Adapter restart triggers `_reindex_artifacts_on_boot` |
| `manifest_lock_timeout_ms` hit | Another writer holding lock | Inspect `lsof <manifest.lock>`; usually a stuck PostToolUse hook |
| Chat artifact card shows a broken-image icon, download also fails | Nested artifact path (imagegen's `outputs/…`, ACS's `acs/runs/.../output/…`) serialized with the OS-native separator (`str(Path)` on Windows → backslash) instead of forward slashes | Fixed 2026-07-13 via `Path.as_posix()` in `chat_runtime.py`'s artifact emitters — see the workdir-route section below |

---

## Self-test hooks (Layer 33 entries)

Added to `operator/bridges/shared/self_test.py`:

| Check | Severity |
|---|---|
| `artifacts.config_readable` | INFO |
| `artifacts.session_root_writable` | WARNING (only on active session probe) |
| `artifacts.manifest_parseable` | CRITICAL (if manifest exists) |
| `artifacts.mcp_handlers_registered` | CRITICAL |
| `artifacts.recall_class_present` | WARNING (only after first artifact_register) |

---

## REST endpoints — session workdir access

These console routes expose the session's working directory to the web UI.

### `GET /v1/console/chat/sessions/{sid}/workdir/{filepath:path}`

Serves a file from the session workdir.  Authentication: `require_session`
(session cookie, `SameSite=Strict`).  All MIME types are served; the
`Content-Disposition` header enables browser download for binary types.

**Nested-path fix (2026-07-13):** `filepath` may contain forward-slash
subdirectories (imagegen's `outputs/…`, ACS's `acs/runs/<id>/output/…`) — the
route already supported this (`{filepath:path}` + a forward-slash-inclusive
`_SAFE_SUBPATH` regex), but `chat_runtime.py`'s artifact-event emitter
serialized the relative path via `str(Path)`, which uses the **OS-native**
separator. On a Windows-hosted console this embedded a literal backslash in
the `"path"` field sent to the browser — the frontend's `filePath.split("/")`
and the route's own forward-slash-only regex both then rejected it, so the
artifact card rendered (the file *was* found by the workdir scan) but its
`<img>`/download URL 404'd with no visible error beyond a broken-image icon.
Fixed by using `Path.as_posix()` instead of `str(Path)` at every artifact-path
emission site in `chat_runtime.py` (the direct subprocess path, the ACS live
path, and the ACS post-run scan). Regression coverage:
`core/console/tests/test_workdir_route.py::test_nested_subdirectory_image_served_inline`
and `::test_deeply_nested_acs_output_served_inline` — the confirmed gap before
this fix was that every existing test in that file served a file sitting flat
at workdir root, never a nested one.

### `GET /v1/console/chat/sessions/{sid}/workdir-path`

Returns the server-side filesystem path of the session workdir and optionally
opens it in the OS file manager.

| Query param | Type | Default | Description |
|---|---|---|---|
| `reveal` | bool | `false` | If `true`, opens the workdir in the OS file manager on the server: `os.startfile()` on Windows, `open` on macOS, `xdg-open` elsewhere |

Response (JSON):
```json
{"ok": true, "path": "/home/…/.corvin/tenants/_default/sessions/web:abc/", "opened": true}
```

**Important:** `reveal=true` opens the file manager on the **server machine**,
not the user's browser. On a cloud/remote deploy the call is harmless (no
useful window opens). `opened` reflects whether the OS-level launch actually
succeeded — a failure (e.g. no shell handler registered) is logged
server-side (`logger.warning`, not silently swallowed) and reported back as
`opened: false`; the frontend banner then tells the user to copy the path
manually instead of implying a window opened when it didn't.

**Windows note (fixed 2026-07-12):** the win32 branch used to shell out to
`explorer.exe` directly via `subprocess.Popen`, which depends on it being
resolvable on the launching process's PATH and could fail without raising
anything the bare `except: pass` surfaced anywhere — the failure mode this
whole route exists to avoid. It now uses `os.startfile()`, the standard
ShellExecute-backed stdlib call for "open this path with its default
handler", which is what Explorer itself uses internally and doesn't depend
on a PATH lookup.

---

## Anti-patterns

- **Reading the manifest as a list of records to display in a UI.** The
  manifest is **append-only**; do `forge.artifacts.list_active()`
  which dedupes by `name` (latest-wins).
- **Generating descriptions synchronously in the PostToolUse hook.**
  Blocks the tool's return; the daemon-thread async path is correct.
- **Storing user PII in `tags`.** `tags` is unredacted — it's free-form
  operator metadata. Don't put emails/IDs there.
- **Calling `artifact_get` without `max_bytes` for an unknown artifact.**
  Use `artifact_list` first to see the size, then choose.
