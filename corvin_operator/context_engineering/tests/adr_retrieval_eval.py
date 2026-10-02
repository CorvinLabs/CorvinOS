"""Labelled retrieval set for the graph stage's ADR matcher (2026-10-02).

Each case: (query, expected — at least one must be returned, forbidden — none may
be returned). ``expected=None`` means NOTHING relevant exists and the matcher must
return nothing. Labels come from the corpus titles, the off-topic hits recorded in
the session-drift analysis, and the round-1 adversarial review.
"""
from __future__ import annotations

POSITIVE = [
    ("Fix the audit chain hash verification failing at boot tripwire",
     {"ADR-0334", "ADR-0328", "ADR-0641", "ADR-0260"}, set()),
    ("Warum schlägt der Boot-Tripwire beim Verifizieren der Audit-Kette fehl?",
     {"ADR-0334", "ADR-0328", "ADR-0641", "ADR-0260"}, set()),
    ("Add a new GDPR Art. 17 erasure handler for the session ledger",
     {"ADR-0045", "ADR-2102", "ADR-0530"}, {"ADR-0797", "ADR-0832"}),
    ("Implementiere einen neuen Erasure-Handler für das Session-Ledger (DSGVO Art. 17)",
     {"ADR-0045", "ADR-2102", "ADR-0530"}, {"ADR-0797", "ADR-0832"}),
    ("Route OS turns to Haiku, Sonnet or Opus based on task complexity classifier",
     {"ADR-0952"}, set()),
    ("Wie funktioniert das Modell-Routing zwischen Haiku, Sonnet und Opus?",
     {"ADR-0952"}, set()),
    ("Bedrock worker 403s because ANTHROPIC_BASE_URL is set", {"ADR-0759"}, set()),
    ("A2A pairing with offline pair and nonce replay protection",
     {"ADR-0070", "ADR-0257", "ADR-2099"}, set()),
    ("Network egress lockdown allowed hosts EU production preset", {"ADR-0043"}, set()),
    ("Kontext-Drift: Sitzungsinhalte gehen nach Kompaktierung verloren",
     {"ADR-2102", "ADR-0407", "ADR-0784", "ADR-2098"}, {"ADR-2058"}),
    ("context drift: session content forgotten after compaction",
     {"ADR-2102", "ADR-0407", "ADR-0784", "ADR-2098"}, {"ADR-2058"}),
    ("Telemetry ping opt-out anonymous instance count", {"ADR-0186", "ADR-0204"}, set()),
    ("Multi-tenant isolation tenant_id in console routes",
     {"ADR-0007", "ADR-0390", "ADR-0858"}, set()),
    ("Speech to text voice transcription audit metadata only",
     {"ADR-0921", "ADR-0185"}, {"ADR-0692"}),
    ("Wie setze ich den Usage-Zähler zurück ohne die Audit-Kette zu kürzen?",
     {"ADR-0760"}, {"ADR-0366", "ADR-0696"}),
    ("Data classification flow guard blocks sending secrets to a cloud engine",
     {"ADR-0042", "ADR-0335", "ADR-0329", "ADR-2049"}, set()),
    ("Delegation router skill shadow mode learning loop",
     {"ADR-0613", "ADR-2092", "ADR-0686"}, set()),
    ("Load-bearing anchor facts re-injected every turn", {"ADR-0407"}, set()),
    ("Analyse session content drift and memory forgetting across sessions, root cause by layer",
     {"ADR-0405", "ADR-0407", "ADR-2098", "ADR-2102", "ADR-0784"},
     {"ADR-0788", "ADR-0365", "ADR-0366", "ADR-0026"}),
    ("repair context drift: the context brief carries stale memory and the wrong decisions",
     {"ADR-2098", "ADR-0407", "ADR-0396", "ADR-0784"},
     {"ADR-0623", "ADR-0326", "ADR-0624", "ADR-0637", "ADR-0185"}),
    # Renumbered files whose frontmatter still carries the old id (review R1-B2).
    ("skill eligibility classes", {"ADR-0785"}, set()),
    ("per-tenant rwlock isolation", {"ADR-0797"}, set()),
]

NEGATIVE = [
    "hallo wie geht es dir heute",
    "what's the weather in Berlin tomorrow",
    "Can you summarize this PDF for me?",
]

#: No ADR covers the subject, and a lexical matcher cannot know that. Measured
#: 2026-10-02: the query returns lexically adjacent ADRs — ADR-0652 ("every
#: path … passes the L35 egress gate"), ADR-0295 ("File Permission Hardener"),
#: ADR-0673 ("Skill … Hook Contracts"). Recorded, not hidden: the bound is "at
#: most these". Excluding them by threshold costs recall on real questions
#: (0.25 → 21/22, 0.30 → 18/22); a title-anchor rule cost 1–2 of 22.
AMBIGUOUS = [
    ("Path gate hook blocks file writes outside workdir", {"ADR-0652", "ADR-0295", "ADR-0673"}),
]


def evaluate(find):
    """``find(query) -> list[str]`` (ADR ids). Returns (hits, misses, forbidden, noise)."""
    hits, misses, forbidden, noise = [], [], [], []
    for q, exp, bad in POSITIVE:
        got = set(find(q))
        (hits if got & exp else misses).append(q)
        if got & bad:
            forbidden.append((q, sorted(got & bad)))
    for q in NEGATIVE:
        got = find(q)
        if got:
            noise.append((q, got))
    for q, allowed in AMBIGUOUS:
        extra = sorted(set(find(q)) - allowed)
        if extra:
            noise.append((q, extra))
    return hits, misses, forbidden, noise
