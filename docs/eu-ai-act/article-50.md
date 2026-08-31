# Art. 50 — Transparency obligations for certain AI systems

> **Short summary:** Art. 50 §1 requires disclosure when an AI interacts with natural persons.
> Art. 50 §4 requires machine-readable content marking. Both are enforced structurally —
> the code enforces them; there is no configuration that disables them.

---

## Art. 50 §1 — Bot-disclosure (Layer 19)

**Legal text (summary):** Deployers of chatbots must ensure natural persons are informed they
are interacting with an AI system, unless it is obvious from context or the user has consented
to the interaction.

### What Corvin does

Every user who contacts Corvin via any bridge (Discord, Telegram, WhatsApp, Matrix, etc.)
receives a **disclosure card** before any other interaction. The card:

- States clearly that this is an AI system
- Names the operator (from `tenant.corvin.yaml::spec.operator_name` or configured name)
- Provides the available commands for opt-in (`/join`), opt-out (`/pass`), and revocation (`/leave`)
- Is delivered exactly once per (channel, chat, uid) — never repeated

### Implementation: `disclosure.py`

**Key function:** `ensure_disclosed(channel, chat_key, uid, *, tenant_id=None, card_renderer=None)`

The disclosure check runs **before** any authorization check, quota check, or message processing.
The sequence in the bridge daemon is:

```
message arrives
    → check disclosure (L19) — must pass first
    → check authorization (whitelist)
    → check consent (L16)
    → process message
```

**Storage:** `<tenant>/global/disclosure/<channel>__<chat>.json` (mode 0600)

Each uid gets one entry:

```json
{
  "user_42": {
    "first_seen": 1778204770.0,
    "card_shown_at": 1778204770.0,
    "action": "joined",
    "channel": "discord"
  }
}
```

**Audit events emitted (GDPR-enforced — uid_hash, not raw uid):**

| Event | When | Chain severity |
|---|---|---|
| `disclosure.shown` | Card delivered, `action="pending"` | INFO |
| `disclosure.action` | User clicked `/join`, `/pass`, or `/leave` | INFO |
| `disclosure.joined` | User granted observer role via `/join` | INFO |

The `uid_hash` is `sha256(uid)[:8]` — enough to correlate events in the chain without storing
the raw user identifier.

### Card structure

The disclosure card (≤1500 characters, readable on a phone screen) contains:

1. **AI-nature statement** — explicit statement that this is an AI
2. **Operator name** — who runs this deployment
3. **Bot purpose** — brief description of what the bot does
4. **Available commands:**
   - `/join` — opt in (grants observer role, enables reply)
   - `/pass` — opt out without further interaction
   - `/leave` — revoke after joining
   - `/consent on` — grant standing consent for transcript sharing
5. **Timestamp** — when this card was shown (for audit correlation)

Available in English and German (`generate_card(language="en"|"de")`).

### What cannot be bypassed

These invariants are enforced by code, not by policy:

```
disclosure cannot be disabled by env var → verified by test_disclosure.py
disclosure fires before auth check        → enforced by daemon message handler
card re-shown to same uid in same chat    → prevented by idempotent storage
operator cannot skip disclosure for "trusted" users → no such concept in code
```

### Persistence resilience

The disclosure "seen" state is stored in a JSON file per (channel, chat). If `_save_store()` fails due to a transient filesystem error (disk full, permissions), the state is not persisted, and the user would receive the card again on the next message — breaking the "shown once" guarantee.

**Fix:** `_save_store()` retries up to 3 times with exponential backoff (0.1s / 0.3s / 1s). If all retries fail, the daemon queues the `mark_seen` state for retry at the next message boundary (in-memory queue, max 3 attempts). On permanent failure, `disclosure.persist_failed` (CRITICAL) is emitted — surfaced in `bridge.sh doctor` output.

### Operator name requirement

Art. 50 §1 requires the disclosure to be "clear" — naming an operator as `(owner)` or leaving the operator field empty does not satisfy "clearly and distinguishably" informing users who operates the AI. Corvin emits a boot WARNING when `operator_name` resolves to the placeholder `(owner)` or empty string. The `bridge.sh doctor` output includes the resolved operator name so operators can verify it before going live.

The `operator_name` field must be set in `tenant.corvin.yaml::spec.operator_name` or the channel-specific settings. Required for Art. 50 compliance in production deployments.

**Test coverage:** `operator/bridges/shared/test_content_marking.py` (14 tests)

---

## Art. 50 §4 — Machine-readable content marking

**Legal text (summary):** AI-generated synthetic content (audio, video, images, text) must be
marked in a machine-readable format, and where technically feasible, a human-readable disclosure
must accompany the content.

### What Corvin does

Every **final** message delivered to a user carries a `provenance` block in the message envelope.
This block is machine-readable, present in every response, and cannot be removed by the model,
a skill, or any persona.

### Implementation: `adapter.py` `_envelope()`

The `_envelope()` closure in `adapter.py` wraps every outgoing message. For final messages
(`_final=True`), it injects:

```python
e["provenance"] = {
    "ai_generated": True,           # always True; never overridable
    "generator_id": "corvin_os",   # system identity
    "persona":      persona_name,   # active persona (e.g. "research", "coder")
    "session_id":   f"{channel}:{chat_key}",   # scoped to this conversation
    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
}
```

**Key invariants (verified by tests):**

| Invariant | Test |
|---|---|
| `ai_generated` is always `True` — cannot be overridden by `extra` dict | `test_provenance_ai_generated_always_true` |
| Progress messages have no provenance (only final messages carry it) | `test_progress_message_has_no_provenance` |
| Heartbeat messages have no provenance | `test_heartbeat_has_no_provenance` |
| `generator_id` is always `"corvin_os"` | `test_provenance_generator_id` |
| Content of the message never appears in the provenance block | `test_no_content_in_provenance` |
| Voice (TTS) final messages carry provenance too | `test_voice_final_has_provenance` |
| `session_id` format is `<channel>:<chat_key>` | `test_provenance_session_id` |
| Persona is empty string when no profile active | `test_provenance_empty_persona_without_profile` |

**Test coverage:** `operator/bridges/shared/test_content_marking.py` (14 tests, all green)

### What "final message" means

Corvin distinguishes three message types in the envelope:

| Type | Has provenance? | `_final` flag |
|---|---|---|
| Progress update (streaming partial) | No | `_progress: True` |
| Heartbeat (keep-alive) | No | `_heartbeat: True` |
| Final answer (complete response) | **Yes** | `_final: True` |

Only final messages are delivered to the user as a complete AI response. Only those carry
the `provenance` block.

### Compliance with "machine-readable" requirement

The `provenance` block is a structured JSON object embedded in the message envelope. Any
downstream system receiving Corvin messages can:

1. Parse `message.provenance.ai_generated` — always `True` for AI output
2. Extract `message.provenance.generator_id` — identifies the generating system
3. Correlate `message.provenance.session_id` with audit chain entries
4. Verify `message.provenance.timestamp_utc` against the audit log

The format is forward-compatible: new fields can be added without breaking existing parsers.

---

## Re-issuance policy for returning users

Art. 50(1) requires that natural persons are informed "before the interaction" that they
are talking to an AI. The "shown once" guarantee in `disclosure.py` meets this for the
initial interaction. However, the regulation's "before the interaction" framing creates
an obligation to re-issue the card in scenarios where a reasonable person would no longer
remember the prior disclosure.

### When re-issuance is required

The disclosure card must be re-shown to a returning user when any of the following applies:

| Trigger | Rationale |
|---|---|
| ≥ 12 months have passed since `card_shown_at` | After one year, a reasonable person may not recall the prior disclosure |
| `operator_name` has changed in `tenant.corvin.yaml` | The user was informed about a different operator; the new operator must disclose itself |
| The bot's primary purpose has materially changed | A bot that was a support assistant but is now a decision-support system carries different implications |
| The user invokes `/leave` and subsequently re-contacts the system | The user explicitly ended their session; re-engagement is a new interaction |

### What re-issuance means in practice

Re-issuance is identical to the initial disclosure: the same card (≤1500 chars, AI-nature
statement, operator name, available commands) is shown at the next message boundary.

The `disclosure.py` `ensure_disclosed()` function handles this by comparing the current
timestamp against `card_shown_at` and the current `operator_name` against the name
recorded at disclosure time. If either check triggers, `mark_seen` is cleared for that
uid and the card is re-issued on the next message.

### What does NOT require re-issuance

- Normal gaps in activity (days, weeks, months below 12)
- Engine changes (user is interacting with CorvinOS, not a specific LLM)
- Persona changes within the same session (same disclosure covers all personas in a deployment)
- Bridge migration (e.g., from Discord to Telegram) — treated as a new (channel, chat, uid) tuple,
  which always triggers fresh disclosure automatically

### Configuration

```yaml
# tenant.corvin.yaml
spec:
  disclosure:
    reissue_after_days: 365      # default: 365 (1 year)
    reissue_on_operator_change: true   # default: true
```

Setting `reissue_after_days: 0` disables the time-based re-issuance check. This is
permitted in deployment contexts where the operator can confirm by other means that all
users are continuously aware of the AI nature (e.g., a closed enterprise environment with
mandatory onboarding). It does not disable the initial disclosure.

### Audit trail

Re-issuance emits a `disclosure.reissued` event to the hash chain with the trigger reason
(`expired`, `operator_changed`, `user_left_and_returned`). The uid_hash and channel are
recorded; the raw uid is not.

---

## Compliance manifest rule

Both obligations are tracked in `compliance/eu-ai-act.yaml`:

```yaml
- id: eua.art50.1.disclosure
  article: "Art. 50 §1"
  severity: critical
  implemented_by:
    - layer: L19
      file: operator/bridges/shared/disclosure.py
      test: operator/bridges/shared/test_disclosure.py

- id: eua.art50.1.disclosure_persistence
  article: "Art. 50 §1"
  severity: high
  implemented_by:
    - layer: L19
      file: operator/bridges/shared/disclosure.py
      test: operator/bridges/shared/test_disclosure.py
      note: "retry logic ensures seen-state persists under transient FS errors"

- id: eua.art50.4.content_marking_format
  article: "Art. 50 §4"
  severity: critical
  implemented_by:
    - layer: L19
      file: operator/bridges/shared/adapter.py
      test: operator/bridges/shared/test_content_marking.py
```

Both rules are checked by `bridge.sh doctor` and blocked by the GitHub Actions
`compliance-check.yml` gate if a PR violates them.
