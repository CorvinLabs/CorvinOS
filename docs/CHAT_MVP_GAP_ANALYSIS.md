# Chat MVP Gap-Analyse: Agent Hub Integration
**Status:** 2026-09-26  
**Ziel:** Dokumentiere, was zum Chat MVP noch fehlt — Soll vs. Ist Vergleich  
**Kritische ADRs:** ADR-2065 (Chat MVP, PROPOSED), ADR-2063 (A2A Feed, ACCEPTED), ADR-0133 (CLAG, ACCEPTED)

---

## 📊 EXECUTIVE SUMMARY

| Aspekt | Status | Blocker | Priorität |
|--------|--------|---------|-----------|
| **ADR-2065 (Collab Chat Architecture)** | ⏳ PROPOSED, NOT STARTED | Design Review pending | 🔴 CRITICAL |
| **SQLite Schema (ChatMessage, Threads)** | ❌ MISSING | Need DB init | 🔴 CRITICAL |
| **WebSocket /collab/threads/{id}/stream** | ❌ MISSING | Need endpoint | 🔴 CRITICAL |
| **Audit-First Pipeline** | ⏳ PARTIAL | `collab.*` events defined but not implemented | 🟡 HIGH |
| **A2A Friendship Token Integration** | ✅ READY | ADR-0070 + a2a_friendship.py exist | 🟢 GREEN |
| **A2A Feed Store (messages.jsonl)** | ✅ LIVE | ADR-2063 ACCEPTED, a2a_feed.py wired | 🟢 GREEN |
| **CLAG Chain Integrity (ADR-0133)** | ✅ ACCEPTED | May need collab-specific wiring | 🟡 CHECK |
| **Consent Gate (L34)** | ⏳ PARTIAL | L34 exists, needs connection-import wiring | 🟡 HIGH |
| **React UI (collab-chat component)** | ❌ MISSING | Pages/components not started | 🟡 HIGH |
| **E2E Tests** | ❌ MISSING | Need full flow tests | 🟡 MEDIUM |

**Fazit:** ADR-2065 ist **architekturiert aber komplett nicht implementiert**. Foundation (A2A, Chat Runtime) ist vorhanden, aber Collab Layer fehlt vollständig.

---

## 🔍 DETAILLIERTE GAP-ANALYSE

### 1. **ADR-2065 Status & Design Readiness**

#### ✅ **Was erfüllt ist:**
- Architecture design complete (8 decision points dokumentiert)
- Audit events defined (`collab.thread_created`, etc. — 6 events)
- Paths identified (`core/console/corvin_console/collab/`, `routes/collab.py`, React components)
- Alternatives considered (JSONL vs SQLite decided → SQLite)
- Failure modes & rollback documented

#### ❌ **Was fehlt — IMPLEMENTATION:**

| Component | Soll (ADR-2065) | Ist (Code) | Gap |
|-----------|-----------------|-----------|-----|
| **collab/ Verzeichnis** | ✅ Planned | ❌ Does not exist | Need to create |
| **ChatMessage Table (SQLite)** | author_kind, text, tenant_id, etc. | ❌ Schema not defined | Write schema + init |
| **Threads Table** | thread_id, created_by, updated_at | ❌ Not in code | Write schema |
| **Connection_requests Table** | For A2A peer pairing | ❌ Not in code | Write schema |
| **Feed_events Table** | For agent activity stream | ❌ Not in code | Write schema |
| **WS Endpoint** | `WS /v1/console/collab/threads/{tid}/stream` | ❌ Not in routes | Implement route |
| **REST Endpoints** | 8 endpoints (POST /send, GET /threads, DELETE /feed, etc.) | ❌ Not in routes | Implement route.py |
| **TaskPubSub Integration** | One queue per (tenant_id, thread_id) | ⏳ TaskPubSub exists globally | Wire for collab |
| **Audit-First emit()** | `CollabAudit.emit(event_type, ...)` | ❌ Class missing | Create auditor class |
| **Connection Request Import** | `POST /v1/console/collab/connections/import` | ❌ Not in routes | Implement route |
| **Consent Gate (L34) Wiring** | In import route, fail-closed | ⏳ L34 exists, not in collab | Wire `check_l34_consent()` |

#### 🟡 **Design Issues to Clarify Before Implementation:**

1. **SQLite Lock Strategy (WAL mode)**
   - ADR-2065: "WAL mode, Mode 0o600"
   - ❓ Is WAL conflict resolution with GDPR erasure (L36) tested?
   - ❓ Concurrent writes + idempotency (`UNIQUE(thread_id, client_msg_id)`) — documented but not coded

2. **Multi-tenant Isolation**
   - ADR-2065: "every query filters WHERE tenant_id = ?"
   - ❓ Tests exist? (`tests/unit/collab_chat_tenant_isolation.py` ?)
   - ❓ Audit chain per-tenant or cross-tenant? (per CLAUDE.md: one chain per tenant)

3. **Metadata-Only Audit Chain vs SQLite Content**
   - ADR-2065: "Chat text in SQLite, hashes in audit chain"
   - ❓ If SQLite corrupted, audit_hash field still points to non-existent content
   - ❓ Recovery procedure documented? (mentions L37 backup but no procedure code)

4. **Outbound DLP (L34/L35)**
   - ADR-2065 says: "If Collab needs DLP on outbound A2A, wire check_l34/check_l44"
   - ❓ Decision: Is outbound DLP needed or deferred to v1.1?

---

### 2. **A2A Foundation — Status & Integration**

#### ✅ **What's Implemented:**
- **ADR-2063 (A2A Feed):** ACCEPTED
  - `a2a_feed.py` — message store + blob retrieval ✅
  - `RemoteTriggerSender` + `RemoteTriggerReceiver` — ready to use ✅
  - Console route `/v1/console/a2a/feed` — wired ✅
  - Tests: 50+ E2E tests for A2A (test_a2a_hub_ten_peers_e2e.py) ✅

- **ADR-0070 (Friendship Token):** Already integrated
  - `a2a_friendship.py:create_friendship_token()` ✅
  - `activate_connection()` exists ✅
  - Tests: `test_a2a_friendship_security.py` (36K lines!) ✅

- **ADR-0133 (CLAG - Chain Integrity):** ACCEPTED
  - `clag.py` — Chain Integrity Token issuing ✅
  - Shadow hashes + epoch anchors implemented ✅
  - Tests: Extensive (test_layer_integrity_a2a.py) ✅

#### ❌ **What's Missing — Collab-Specific Wiring:**

| Mechanism | Soll | Ist | Gap |
|-----------|------|-----|-----|
| **CLAG in /collab/send** | `clag.gate("L38.collab_send")` before `RemoteTriggerSender` | ❌ Route not written | Wire CLAG gate in send path |
| **Friendship Token in /collab/import** | Verify token, activate, record consent | ❌ Route not written | Implement import endpoint |
| **A2A Dispatch from @mention** | `MentionRouter.dispatch()` calls `RemoteTriggerSender` | ⏳ Router exists? Check dispatch | Verify router integration |
| **Feed Events to A2A Stream** | `feed_events` table → subscribe to phase updates | ⏳ Unclear | Clarify feed streaming |

---

### 3. **Chat Runtime — What Exists, What Needs Extension**

#### ✅ **Existing (Works for Single-User):**
- `chat_runtime.py` — subprocess-based chat session
- `chat.py` routes — WebSocket streaming
- `chat_router.py` — routing logic
- `task_pubsub.py` — in-process pub/sub (TaskPubSub)

#### ❌ **What's Missing for Collab:**

| Feature | Needed for Collab | Status |
|---------|------------------|--------|
| **Multi-author thread** | Yes — collab.py needs new session model | ❌ Not started |
| **Agent activity feed** | Yes — stream reasoning/tool-calls | 🟡 Partial (exists for single user, needs multi-user) |
| **@mention routing** | Yes — dispatch to peer/worker | ⏳ Needs MentionRouter class |
| **Idempotency (client_msg_id)** | Yes — prevent duplicates on reconnect | ⏳ Needs implementation in route |
| **Audit hash reference** | Yes — link message to audit chain | ❌ Not in schema |
| **Connection requests UI** | Yes — "add peer" button → token dialog | ❌ Not started |

---

### 4. **React UI — Missing Entirely**

#### ❓ Planned (per ADR-2065 paths):
- `web-next/src/components/collab-chat/` — chat message list, input, feed
- `web-next/src/pages/collab.tsx` — main page (or under agent-hub)

#### ❌ **Current State:**
```
web-next/src/
  ├─ components/agent-hub/
  │   ├─ chat-interface.tsx  (single-user chat, not collab)
  │   └─ live-feed.tsx       (A2A metadata feed, not message content)
  ├─ pages/
  │   └─ agent-hub.tsx       (main agent hub page)
  │
  └─ [NO collab-chat/ OR collab.tsx]
```

#### 🔴 **Missing UI Components:**

1. **Thread List** — show all collab threads
2. **Message List** — unified human + agent + A2A peer messages
3. **Message Input** — @mention resolution, attachment picker
4. **Agent Activity Stream** — reasoning, tool calls, results (live via WebSocket)
5. **Connection Request Dialog** — paste token, import peer
6. **Participant Sidebar** — show active humans, local agents, A2A peers

#### **Tests:**
- `web-next/tests/e2e/chat-*.spec.ts` exist for single-user chat
- No `collab-*.spec.ts` test suite

---

### 5. **Audit Events — Defined but Not Emitted**

Per ADR-2065, 6 audit events are defined:
```
- collab.thread_created
- collab.message_sent
- collab.message_received
- collab.agent_dispatched
- collab.feed_event_emitted
- collab.connection_request_issued
- collab.connection_request_imported
- collab.connection_established
```

#### ❌ **Status:**
- ✅ Event types defined in ADR-2065 frontmatter
- ❌ `security_events.EVENT_SEVERITY` — **not registered**
- ❌ `security_events._EVENT_ALLOWLIST` — **not registered**
- ❌ No `CollabAudit.emit()` class — **not implemented**

#### **Risk:**
If events are not registered in allowlist, they will be **silently dropped** by the audit chain (see L16/L23 audit-first design).

---

### 6. **Critical Blocker: ADR-0133 (CLAG) Wiring**

ADR-0133 mandates:
> Before any operation in L38 (A2A) may proceed, it must call `clag.gate(path, layer_id)`. gate() fails-closed if the chain is broken.

#### ❓ **Is Collab Wired?**

**Current A2A Users (Verified):**
- `remote_trigger_receiver.py::receive()` — ✅ calls `clag.gate()`
- `remote_trigger_sender.py::send()` — ✅ calls `clag.gate()`
- Tests: `test_layer_integrity_a2a.py` proves it works

**Collab Users (Planned):**
- `/collab/send` endpoint (not yet written) — ❓ Will it call `clag.gate()`?
- `/collab/connections/import` (not yet written) — ❓ Will it call `clag.gate()`?
- `MentionRouter.dispatch()` → `RemoteTriggerSender` — ⏳ Unclear if gate will be hit transitively

#### **Gap:**
Without explicit `clag.gate()` call in collab routes, **compliance requirement is not met** (ADR-0133 is ACCEPTED, failure to implement is a violation).

---

### 7. **Consent Gate (L34) — Needs Collab-Specific Wiring**

ADR-2065 § 8 states:
> Connection Request = Explicit Consent
> - Import endpoint: `POST /v1/console/collab/connections/import` requires `consent_ack: true` (checkbox)
> - No silent pairing

#### ❓ **Current State:**
- ✅ L34 (Consent) layer exists in CorvinOS
- ✅ Consent store implemented
- ❌ `/collab/connections/import` route — **not written**
- ❌ Integration with consent gate — **not wired**

#### **Implementation Needed:**
```python
# pseudo-code
async def import_connection(req: ImportRequest, session: Session):
    # 1. Verify friendship token
    peer = parse_and_verify_token(req.token)
    
    # 2. CHECK CONSENT GATE (L34 — FAIL-CLOSED)
    consent_result = check_l34_consent(
        action="a2a_peer_import",
        peer_id=peer.id,
        tenant_id=session.tenant_id
    )
    if not consent_result.granted:
        return 403 Forbidden
    
    # 3. Record audit event
    CollabAudit.emit("collab.connection_request_imported", ...)
    
    # 4. Activate peer in collab participants
    add_participant(thread_id, peer_id, author_kind="a2a_peer")
```

---

### 8. **Dependencies & Blockers Summary**

#### 🟢 **READY (No blockers):**
- ADR-2063 (A2A Feed) — fully implemented
- ADR-0070 (Friendship Token) — fully implemented
- ADR-0133 (CLAG) — fully implemented
- TaskPubSub — exists, can be reused
- L34 (Consent) — exists, needs wiring

#### 🟡 **PENDING REVIEW:**
- ADR-2065 (Collab Chat) — status: **PROPOSED**, awaiting "tech-lead + security review before ACCEPTED"
- **Blocker:** Cannot commit implementation until ADR-2065 is ACCEPTED

#### 🔴 **CRITICAL BLOCKER (if any):**
- None identified in ADRs
- ⚠️ Risk: CLAG (ADR-0133) compliance might be missed if not explicitly wired in new routes

---

## 📝 IMPLEMENTATION CHECKLIST

### **Phase 1: Design & Review (1-2 days)**
- [ ] ADR-2065 tech-lead review → promote to ACCEPTED
- [ ] Security review of Friendship Token + CLAG integration
- [ ] Clarify L34/L35 outbound DLP requirements (v1 vs v1.1)
- [ ] Finalize SQLite recovery procedure

### **Phase 2: Core Backend (3-4 days)**
- [ ] Create `core/console/corvin_console/collab/` directory
- [ ] Implement `collab_store.py` — SQLite schema + CRUD
  - [ ] threads table
  - [ ] messages table (with audit_hash, author_kind)
  - [ ] participants table
  - [ ] connection_requests table
  - [ ] feed_events table
- [ ] Implement `collab_audit.py` — audit event emission
  - [ ] Register 8 events in `security_events.py`
  - [ ] Implement `CollabAudit.emit(event_type, ...)`
- [ ] Create `routes/collab.py` — REST endpoints
  - [ ] POST `/collab/threads` — create thread
  - [ ] GET `/collab/threads/{tid}` — fetch thread + messages
  - [ ] POST `/collab/threads/{tid}/send` — send message (audit-first, CLAG gate)
  - [ ] DELETE `/collab/threads/{tid}/feed` — clear feed
  - [ ] POST `/collab/connections/requests` — initiate connection request
  - [ ] POST `/collab/connections/import` — import token (L34 consent gate)
- [ ] Wire WebSocket endpoint `WS /collab/threads/{tid}/stream`
  - [ ] TaskPubSub subscription per (tenant, thread_id)
  - [ ] Heartbeat (25s ping, 60s timeout)
  - [ ] Reconnect catch-up via REST

### **Phase 3: Integration (2-3 days)**
- [ ] Wire `clag.gate()` in `/collab/send` route
- [ ] Wire `check_l34_consent()` in `/collab/connections/import` route
- [ ] Wire `RemoteTriggerSender` in `/collab/send` for A2A @mentions
- [ ] Integrate with A2A feed store (a2a_feed.py)
- [ ] Add `collab_chat/` to L36 erasure handlers (follow-up task 4.2 in ADR)

### **Phase 4: React UI (2-3 days)**
- [ ] Create `web-next/src/components/collab-chat/`
  - [ ] `MessageList.tsx` — render unified messages (human, local, A2A)
  - [ ] `MessageInput.tsx` — @mention resolver, attachment support
  - [ ] `ParticipantSidebar.tsx` — show authors
  - [ ] `ConnectionRequestDialog.tsx` — import peer token
- [ ] Create `web-next/src/pages/collab.tsx` or integrate into agent-hub.tsx
- [ ] Wire WebSocket connection (use `use-chat-task-status.ts` pattern)
- [ ] Add feed streaming (agent reasoning, tool calls)

### **Phase 5: Testing & Hardening (2-3 days)**
- [ ] Unit tests for collab_store.py (CRUD, tenant isolation)
- [ ] Unit tests for collab_audit.py (event emission, compliance)
- [ ] Integration test: send message → audit → SQLite consistent
- [ ] E2E test: human sends message → A2A peer receives → response → feed updates
- [ ] Security tests:
  - [ ] CLAG gate is called and fails-closed
  - [ ] L34 consent gate blocks unauthorized imports
  - [ ] Tenant isolation: one tenant cannot read another's collab
  - [ ] SQLite corruption handling (503 returned)
- [ ] Adversarial tests (10-15 findings to fix)

---

## 🎯 ESTIMATED EFFORT & TIMELINE

| Phase | Days | Owner | Status |
|-------|------|-------|--------|
| **1. Design Review** | 1-2 | Tech Lead | ⏳ Not started |
| **2. Core Backend** | 3-4 | Backend | ⏳ Not started |
| **3. Integration** | 2-3 | Backend | ⏳ Not started |
| **4. React UI** | 2-3 | Frontend | ⏳ Not started |
| **5. Testing** | 2-3 | QA/Security | ⏳ Not started |
| **Total** | **10-15 days** | | |

**Parallel tracks possible:** UI (Phase 4) can start during backend integration (Phase 3).

---

## ⚠️ KEY RISKS & MITIGATION

| Risk | Impact | Mitigation |
|------|--------|-----------|
| **ADR-2065 rejected in review** | Design restart needed | Schedule tech-lead review ASAP (before coding) |
| **CLAG gate forgotten in one route** | Compliance violation (ADR-0133 breach) | Use checklist; tests must verify every route calls gate() |
| **SQLite schema migration bug** | Data loss, erasure failures | Test recovery procedure (L37 backup); add schema versioning |
| **Multi-tenant isolation bug** | Cross-tenant data leak (GDPR breach) | Mandatory integration tests; audit every query |
| **WebSocket queue overflow** | Feed stalls under load | Implement `resync_required` signal + REST fallback (documented in ADR-2065) |
| **A2A peer disappears mid-message** | Hanging thread participant | Implement timeout + `phase: failed` feed event (documented) |

---

## 📚 RELATED DOCS & REFERENCES

- **ADR-2065:** `Corvin-ADR/decisions/ADR-2065-collab-chat-architecture.md` (PROPOSED)
- **ADR-2063:** A2A Feed (ACCEPTED) — `a2a_feed.py`
- **ADR-0133:** CLAG (ACCEPTED) — `clag.py`
- **ADR-0070:** Friendship Token — `a2a_friendship.py`
- **L34 (Consent):** `core/compliance/consent.py`
- **L36 (Erasure):** `core/compliance/erasure_handlers.py`
- **Chat v1 docs:** `chat_runtime.py` docstring (ADR-0037 § Iteration 3a)

---

## 🔗 NEXT STEPS

1. **Assign tech-lead for ADR-2065 review** — target 1-2 days
2. **Once ACCEPTED:** Create implementation issues for each Phase 2-5 task
3. **Parallel:** Start Phase 4 (React UI) component sketches during Phase 3 backend work
4. **Quality gate:** All 8 audit events must be registered + tested before merge

---

**Generated:** 2026-09-26 · **Status:** Gap-Analyse komplett, Implementierung ausstehend
