---
name: corvinOS_review_e2e
description: Defines what a REAL end-to-end test IS in CorvinOS — drive the changed function through its real entry point across real layers (real DB, real LLM, real A2A, real audit) to a real observable outcome, so the review E2E gives a trustworthy LDD loss signal. Triggers: e2e, end-to-end, integration test, real data, what is an e2e.
---

# What is an End-to-End Test (CorvinOS)

Defines what counts as a REAL end-to-end test, so the code-review loop (Phase 6)
gets a trustworthy loss signal for LDD. If the E2E is weak or mocked, LDD
optimises the wrong thing — a green run must mean the *real system* works.

## When to use
- Whenever a review/fix needs an E2E — every `corvinOS.code.review` round (Phase 6).
- When deciding whether a test is "really" end-to-end or a dressed-up unit test.

## Definition (the one-sentence test)

> An E2E test drives the **actually-changed function through its real entry
> point, across every real layer it touches, to a real observable outcome** —
> with NO mock on the path the fix lives on.

If you mocked something on the fix's path, it is not an E2E for that fix.

## The five properties of a real E2E

1. **Real entry point.** Invoke the system the way a user/agent does — a bridge
   message, a console chat turn, a CLI command, an HTTP route, an A2A envelope —
   not the function in isolation.
2. **Real dependencies on the fix's path.** Real database / DataSource (real
   rows), real LLM / engine (live model call), real A2A connection (HMAC-signed
   envelope between two instances), real filesystem + bwrap sandbox, real audit
   chain. Mock ONLY what is genuinely off the path or unavailable — and name it.
3. **Real observable outcome.** Assert the artifact produced, the DB row written,
   the audit event in the hash chain, the verified A2A response signature, the
   rendered file, the HTTP status + body — never an internal variable.
4. **Reproducible signal.** Run it at least twice (`reproducibility-first`). A
   flaky E2E is a finding, not a pass. Record the loss metric (pass/fail +
   latency / correctness / coverage delta).
5. **Hits the fix.** The run must exercise the exact changed code path. If the
   fix cannot be reached end-to-end, the test scope (or the fix) is wrong.

## The "real" ladder (prefer the top; name any step down)

| Tier | Data | Model | A2A | Verdict |
|---|---|---|---|---|
| **Gold** | real prod-like DB | real cloud LLM (Claude Code) | real signed cross-instance | true E2E |
| **Silver** (named fallback) | local real DB (PG / MySQL / SQLite test instance) | local real LLM (Hermes / Ollama) | loopback A2A pair | still real protocol + subprocess |
| **Not an E2E** | mocked DB | stubbed model | faked envelope | this is a unit test — does NOT count |

Silver is acceptable ONLY when Gold is impossible (no creds, offline) — and you
MUST name the gap. Never report a clean E2E on the strength of mocks alone.

## CorvinOS anchors (use these real resources)

- **Real DataSource:** the Spotify DSI test DBs — Postgres :5433, MySQL :3307,
  SQLite file (~5k real rows). Register via DSI v1 and query for real.
- **Real LLM:** the Claude Code engine, or **Hermes** (local Ollama) for a real
  model with zero egress — ideal when data must not leave the host.
- **Real A2A:** `corvin-a2a pair` two local instances, `corvin-a2a send` a real
  TaskEnvelope, assert the ResponseEnvelope HMAC verifies + the instance-id pin holds.
- **Real audit:** assert the expected events land in the hash chain AND
  `voice-audit verify` exits 0 (chain intact).
- **Real sandbox / tools:** a forge tool runs in real bwrap; an ACS run spawns
  real workers (real engine) and yields a real artifact.

## Build procedure

1. Name the changed function, its real entry point, and the real layers it crosses.
2. Stand up the real deps: seed a real DB, start the engine, pair A2A, ready the sandbox.
3. Drive the entry point with a realistic input that exercises the fix.
4. Assert every real observable outcome + the audit trail.
5. Run twice or more — confirm a stable result; capture the loss metric.
6. Any real dep unavailable → named Silver fallback, never a silent mock.

## Why this matters for LDD

The E2E **is** the loss function. A real E2E gives an unambiguous gradient: did
the fix drive the loss to zero on the *real* path? A mocked E2E gives a fake
gradient and LDD converges on the wrong optimum. Trust the signal only as far
as the test is real.

## Definition of done (one E2E)

- [ ] Real entry point; no mock on the fix's path
- [ ] Real DB / LLM / A2A where the fix touches them (or a named Silver fallback)
- [ ] Asserts a real observable outcome + the audit trail
- [ ] Reproducible (two or more runs agree); a flake is a finding
- [ ] Demonstrably exercises the changed code

## Related
- `corvinOS.code.review` — its Phase 6 loads this skill
- `e2e-driven-iteration` — the E2E -> loss -> fix rhythm
- `per_subtask_e2e` — real-subprocess E2E for security-touching code
- `reproducibility-first` — one run is not a gradient
