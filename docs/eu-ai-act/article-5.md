# EU AI Act Art. 5 — Prohibited Practices Assessment

> **Status:** Authoritative  
> **Reviewed:** 2026-06-12  
> **Regulation:** EU AI Act 2026 (Regulation (EU) 2024/1689), Art. 5  
> **Applicable since:** 2 February 2025  
> **Conclusion:** CorvinOS does not implement any prohibited AI practice under Art. 5.
> Residual risk notes and ongoing monitoring obligations are documented below.
> **Structural enforcement (2026-06-19):** the L44 Acceptable-Use gate (ADR-0143)
> now blocks the prohibited *use* of CorvinOS for manipulation / disinformation
> (and the out-of-scope military and offensive-cyber classes) fail-closed before
> any engine spawn — moving Art. 5 non-engagement from a design statement to an
> enforced, repo-defined, non-disableable control.

---

## Scope and method

This document is the formal Art. 5 assessment for the CorvinOS platform. It examines
each prohibited practice listed in Art. 5(1) and (2) against CorvinOS's architecture,
feature set, and configuration defaults. Where a feature has a superficial resemblance
to a prohibited practice, the distinction is stated explicitly.

The assessment was performed against CorvinOS v0.14.0 (commit range: initial → HEAD as
of the review date). Any feature introduced after this date must be reviewed against
this document before merging.

---

## Art. 5(1)(a) — Subliminal or manipulative techniques

**Text (summary):** Prohibits AI systems that deploy subliminal techniques beyond
a person's consciousness, or purposefully manipulative or deceptive techniques,
to materially distort a person's behavior in a way that causes, or is reasonably
likely to cause, harm.

### Feature under review: LDD learning blocks and metaphors

CorvinOS injects optional structured blocks into AI responses:

- **Learning blocks** (`[LEARNING: ...]`) — flag unexpected outcomes in a conversation
- **Metaphors** (`[METAPHOR: ...]`) — add a metaphorical restatement of the response

**Assessment: NOT prohibited**

These blocks operate **above** the user's consciousness level: they are visible in the
chat output and in the voice/web console. The user can observe, question, or ignore them.
Art. 5(1)(a) targets techniques that bypass conscious perception (e.g. subliminal image
flashes, imperceptible audio encoding, micro-targeted emotional priming invisible to the
subject). Visible pedagogical blocks do not meet this criterion.

Additionally, no material behavioral distortion is effected: the blocks describe the AI's
reasoning structure, not instructions to the user. There is no personalization targeting
that exploits psychological vulnerabilities, no variable-reward scheduling, and no dark
pattern design.

**Mitigation in place:** The operator can disable both features via persona configuration.
The Discord bridge strips both blocks from voice synthesis (voice-only path), preventing
any subliminal repetition concern via audio modality.

### Feature under review: Persona system (cowork personas)

CorvinOS supports multiple AI personas (DataAnalyst, BoardWriter, etc.) that can be
configured with distinct identities, tone, and response styles.

**Assessment: NOT prohibited**

The persona system does not deceive users about AI nature. Art. 50 §1 disclosure fires
*before* any persona-shaped interaction. The persona is a surface-layer presentation
layer; the AI-nature disclosure is structurally prior. Users cannot be misled into
believing they interact with a human.

The persona system does not exploit psychological vulnerabilities, does not monitor
emotional states, and does not adapt its strategy based on inferred affective states.

**Mitigation in place:** `is_ai: true` is hardcoded in every ActorDocument (L39); the
disclosure card names the system as an AI regardless of persona.

---

## Art. 5(1)(b) — Exploitation of vulnerabilities of specific groups

**Text (summary):** Prohibits AI systems that exploit vulnerabilities of persons or
groups of persons due to their age, disability, or specific social or economic situation,
to materially distort behavior in a way that causes harm.

**Assessment: NOT applicable / NOT prohibited**

CorvinOS does not include:
- Age detection or age-group targeting
- Disability-status inference or adaptive exploitation
- Economic vulnerability scoring
- Targeted manipulation strategies adapted to inferred group membership

No CorvinOS feature varies its strategy based on group membership in a way that exploits
vulnerability. The engine-allowlist and data-classification matrix constrain outputs based
on data sensitivity, not user vulnerability.

**Residual risk note:** An operator could theoretically configure a persona to target
vulnerable users. This is addressed by `OPERATOR-OBLIGATIONS.md § Prohibited Use`,
which explicitly prohibits deployment for manipulation targeting vulnerable groups.
The operator declaration gate (`L40`) includes a `permitted_use` assertion that operators
must sign.

---

## Art. 5(1)(c) — Biometric categorisation inferring sensitive attributes

**Text (summary):** Prohibits AI systems that categorise natural persons based on biometric
data to deduce race, political opinions, trade union membership, religious or philosophical
beliefs, sex life or sexual orientation. (Note: separate from real-time biometric ID under
Art. 5(1)(d).)

**Assessment: NOT applicable**

CorvinOS does not process biometric data. Specifically:
- Voice-to-text transcription (L23) converts speech to text; the transcription output
  is not used to infer demographic or protected attributes
- No facial recognition, gait analysis, or physiological signal processing
- No speaker diarization for identity re-identification

**Voice transcription note:** The STT pipeline (L23) outputs only text. No voice feature
vector, speaker embedding, or biometric identifier is stored. Audit events from L23
contain only metadata (duration, provider, language code). The transcript text itself
is never persisted. This is verified structurally: `voice.transcribed` events do not
accept a `transcript` field in the audit allowlist.

---

## Art. 5(1)(d) — Real-time remote biometric identification

**Text (summary):** Prohibits the use of real-time remote biometric identification
systems in publicly accessible spaces for law enforcement purposes (with very limited
exceptions requiring prior authorisation).

**Assessment: NOT applicable**

CorvinOS is not a biometric identification system. It does not identify persons by
face, voice print, or other biometric modality. It processes text messages and optionally
speech-to-text input. The STT pipeline is for accessibility and convenience; it does
not perform speaker identification.

---

## Art. 5(1)(e) — Social scoring by public authorities

**Text (summary):** Prohibits AI systems that evaluate or classify natural persons or
groups thereof based on their social behavior or personal characteristics, leading to
detrimental treatment unrelated to the original data context, or disproportionate/unjustified
treatment.

**Assessment: NOT applicable**

CorvinOS does not implement social scoring. Its quota system (L20) tracks message
counts per user per day for rate-limiting purposes — this is a technical resource-management
mechanism, not a behavioral score. It does not influence access to services outside the
CorvinOS deployment, does not persist beyond the configured TTL, and does not aggregate
across contexts.

The user recall system (L28) stores conversational context to improve continuity within
a single deployment. It does not produce scores, ranks, or ratings of users, and does not
share data across tenants.

---

## Art. 5(1)(f) — Emotion recognition in workplace and education

**Text (summary):** Prohibits AI systems that infer emotions of natural persons in the
context of workplace or educational institution settings, with limited exceptions for
medical or safety purposes.

**Assessment: NOT prohibited, with monitoring obligation**

CorvinOS does not perform explicit emotion detection. However:

1. **Voice modality:** The STT pipeline processes speech audio. It does not extract
   prosodic features, sentiment, or emotional valence. The output is plain text.
   The model (Whisper-based) is a speech recognition model, not an emotion classifier.

2. **LLM responses:** The underlying LLM (Claude, Hermes, etc.) may infer emotional
   context from message text as part of natural language understanding. This is general
   NLU, not an emotion classification system deployed for workplace monitoring purposes.

3. **No workplace monitoring deployment:** CorvinOS does not include features designed
   for employee monitoring, performance evaluation, or productivity tracking via emotional
   state inference.

**Ongoing monitoring obligation:** If an operator deploys CorvinOS in a workplace
context where voice transcription is used, the operator must assess whether the
deployment constitutes emotion recognition under Art. 5(1)(f). CorvinOS does not
perform this assessment on the operator's behalf; the `OPERATOR-OBLIGATIONS.md`
pre-deployment checklist includes this item.

---

## Art. 5(1)(g) — Real-time biometric identification / facial recognition database creation

**Text (summary):** Prohibits untargeted scraping of facial images from the internet
or CCTV to create or expand facial recognition databases.

**Assessment: NOT applicable**

CorvinOS does not process images or video for facial recognition. It does not
scrape the internet. The web console and bridge adapters do not handle image
content for biometric purposes.

---

## Art. 5(1)(h) — AI-generated content for electoral manipulation

**Text (summary):** Prohibits AI systems, including deep fakes, used to influence
electoral outcomes through disinformation or manipulated synthetic content without
transparent disclosure.

**Assessment: NOT prohibited**

CorvinOS is a general-purpose assistant framework. It does not include features
designed for electoral influence. All AI-generated content carries a `provenance`
block in the message envelope (`ai_generated: true`), satisfying the transparency
requirement.

The operator declaration gate prohibits deployment for "influence/manipulation
campaigns targeting behavior" (see `OPERATOR-OBLIGATIONS.md § Prohibited Use`).
This is a structural policy constraint, not just a terms-of-service provision.

---

## Summary

| Art. 5 prohibition | Applicable? | CorvinOS finding |
|---|---|---|
| 5(1)(a) — subliminal manipulation | Marginally relevant (LDD blocks, personas) | NOT prohibited — all features visible and opt-out capable |
| 5(1)(b) — exploitation of vulnerable groups | Not applicable | No group-targeting or vulnerability exploitation |
| 5(1)(c) — biometric categorisation for sensitive attributes | Not applicable | No biometric data processed |
| 5(1)(d) — real-time biometric ID | Not applicable | No biometric identification system |
| 5(1)(e) — social scoring | Not applicable | Quota is technical rate-limiting, not social scoring |
| 5(1)(f) — emotion recognition in workplace | Monitored | No emotion recognition; operator obligation to assess workplace deployment |
| 5(1)(g) — facial recognition database | Not applicable | No image/video processing for biometric purposes |
| 5(1)(h) — electoral manipulation | Not applicable | Provenance marking + prohibited-use gate |

**Overall finding: No prohibited practice under Art. 5.**

---

## Review cadence

This assessment must be re-evaluated:
- When a new feature involves voice analysis, image processing, or user behavioral modeling
- When a new LLM provider is added to the engine allowlist
- When CorvinOS is deployed in a new high-risk context (employment, education, critical infrastructure)
- Annually for routine drift check

Next scheduled review: 2027-06-12.
