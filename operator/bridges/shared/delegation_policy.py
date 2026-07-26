"""The shared worker-engine routing RULE, one source of truth.

The routing decision must be identical on every surface — Console chat, all
messenger bridges, remote triggers — so the pure decision lives here and every
surface calls it. No surface may carry its own routing rule (CLAUDE.md
§ Worker Engine Selection).

The rule is intentionally a pure function of already-computed booleans, not of
the raw prompt: the big-data signal and the pool/availability checks are
surface-specific to compute (the console peeks its own pool, the bridge its
own), but the DECISION they feed must be identical everywhere. Keep it a pure
function so the routing matrix stays unit-testable and both callers cannot
diverge.

History: ADR-0221 P1 introduced this module with the ADR-0217 rule (TDE is the
default delegation engine). The operator-selectable worker engine replaces that
constant with `mode` — see `worker_engine_target`. The default is now
``native``: a stock install does the work in Claude Code and delegates nothing
but big-data-shaped work.
"""
from __future__ import annotations

import re

#: The operator-selectable worker engines. ``native`` is the default.
WORKER_ENGINE_MODES: tuple[str, ...] = ("native", "acs", "tde")
WORKER_ENGINE_DEFAULT = "native"


def worker_engine_target(
    *,
    mode: str,
    force_delegate: bool,
    is_big_data: bool,
    tde_available: bool,
    quota_ok: bool,
) -> str:
    """Which engine runs this turn: ``"native"`` | ``"acs"`` | ``"tde"``.

    ``mode`` is the operator's ``spec.web_chat.worker_engine`` selection; the
    remaining arguments are the surface's already-computed signals. Callers
    invoke this for turns the delegation pre-filter considered delegation-worthy
    — the rule may still answer ``native``, and then the turn runs in-process.

    The ladder:

    1. An explicit ``/delegate`` → ACS, in every mode. Explicit user commands
       beat every classifier (delegation-routing.md §6 invariant).
    2. Big-data-shaped work → ACS, in every mode including ``native``. The
       manager/worker fan-out's per-worker context isolation genuinely beats a
       single full-context turn on volume; this is the ONLY auto-delegation a
       ``native`` install performs.
    3. ``mode == "native"`` → ``native``. Claude Code does the work in-process.
    4. ``mode == "acs"`` → ACS.
    5. ``mode == "tde"`` → TDE, but only while TDE is importable AND the shared
       pool has headroom; otherwise ``native``.

    Every degrade ends at ``native``, never at another delegation engine: an
    unavailable or exhausted engine must not silently swap the operator's
    selection for a different one. An unknown ``mode`` degrades to ``native``
    for the same reason.
    """
    if force_delegate:
        return "acs"
    if is_big_data:
        return "acs"
    if mode == "acs":
        return "acs"
    if mode == "tde":
        return "tde" if (tde_available and quota_ok) else "native"
    return "native"


def delegation_engine_target(
    *,
    force_delegate: bool,
    is_big_data: bool,
    tde_available: bool,
    quota_ok: bool,
) -> str:
    """DEPRECATED — the pre-worker-engine ADR-0217 rule (``"tde"`` | ``"acs"``).

    Kept so an out-of-tree caller that has not been migrated to
    ``worker_engine_target`` keeps its old behavior rather than silently
    changing engines. It hard-codes ``mode="tde"`` and folds the old
    "TDE unavailable → ACS" degrade, which the new rule deliberately routes to
    ``native`` instead. New code MUST call ``worker_engine_target``.
    """
    target = worker_engine_target(
        mode="tde",
        force_delegate=force_delegate,
        is_big_data=is_big_data,
        tde_available=tde_available,
        quota_ok=quota_ok,
    )
    return "acs" if target == "native" else target


# ── Big-data signal (shared with every surface) ───────────────────────────
#
# Lived in chat_runtime.py until 2026-07-26 and was therefore unavailable to
# the bridge adapter — which is why a big-data task on Discord/WhatsApp could
# not be routed to ACS at all. Moved here so both callers classify identically;
# duplicating it would be exactly the drift this module exists to prevent.
# The ReDoS bound (_BIG_DATA_MAX_SCAN) and the hardware/recurrence carve-outs
# are load-bearing — see the 2026-07-24 round-3 refutation.
# Hardware nouns that make a volume NOT about data ("2 TB SSD", "3 GB RAM").
_HW_NOUN_RE = re.compile(
    r"\b(?:ram|arbeitsspeicher|vram|ssds?|hdds?|festplatten?|disks?|drives?|"
    r"speicher)\b",
    re.IGNORECASE,
)
_BIGDATA_VOCAB_RE = re.compile(
    r"\bbig[\s\-]?data\b|\bdata[\s\-]*lakes?\b|\bdata[\s\-]*warehouses?\b|"
    r"\b(?:riesige[nrms]?|gewaltige[nrms]?|huge|massive|large[\s\-]*scale)\s+"
    r"(?:datenmengen?|datens[äa]tze?n?|datasets?|logfiles?|corpus|korpus)\b",
    re.IGNORECASE,
)
# TB/PB volume token — bounded digits, no trailing window (no backtracking).
_TBPB_RE = re.compile(
    r"\b\d{1,6}(?:[.,]\d{1,3})?\s?(?:tb|tib|pb|pib|terabytes?|petabytes?)\b",
    re.IGNORECASE,
)
_GB_RE = re.compile(
    r"\b\d{1,7}(?:[.,]\d{1,3})?\s?(?:gb|gib|gigabytes?)\b",
    re.IGNORECASE,
)
# A "big count" token: magnitude words, grouped ≥1e6, bare ≥7-digit run, or a
# k/m magnitude suffix (NOT followed by a letter, so "km"/"3m fertig" that is
# a unit/word is excluded). All bounded → each is linear-time.
_BIG_COUNT_RE = re.compile(
    r"\b(?:million(?:en)?|mio\.?|mrd\.?|milliarden?|billions?|millions?)\b"
    r"|\b\d{1,3}(?:[.,]\d{3}){2,6}\b"
    r"|\b\d{7,15}\b"
    r"|\b\d{1,6}(?:[.,]\d{1,3})?[km](?![a-z])",
    re.IGNORECASE,
)
_CLAUSE_DELIMS = ".!?;,:\n\r"


def _clause_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """[lo, hi) of the clause containing [start, end): extend to the nearest
    clause delimiter on each side."""
    lo = start
    while lo > 0 and text[lo - 1] not in _CLAUSE_DELIMS:
        lo -= 1
    hi = end
    n = len(text)
    while hi < n and text[hi] not in _CLAUSE_DELIMS:
        hi += 1
    return lo, hi


def _clause_around(text: str, start: int, end: int) -> str:
    lo, hi = _clause_bounds(text, start, end)
    return text[lo:hi]




# ── ADR-0217 — TDE-first delegation: big-data discriminator ───────────────────
# Maintainer decision 2026-07-24: within the delegated branch, TDE (ADR-0214)
# is the DEFAULT engine; the ACS manager/worker fan-out remains ONLY for
# (a) the explicit `/delegate` override and (b) big-data-shaped tasks, where
# the manager/worker pattern's per-worker context isolation genuinely beats
# TDE's full-context steps. Deterministic, 0 ms, no API — same contract as the
# rest of the triage (§6 invariant: the triage path never spawns a subprocess).
# Big-data detection (ADR-0217). REBUILT 2026-07-24 (round-2 refutation): the
# earlier single mega-regex (bounded volume/count token + "[^.!?]{0,30}" window
# + data noun) had catastrophic O(n²) backtracking — a pasted digit blob froze
# the whole console event loop for tens of seconds. This version instead uses
# small, individually non-backtracking token regexes and does the "is a data
# noun nearby?" proximity test in Python against the CLAUSE the token sits in
# (bounded by . ! ? ; , : and newlines). No regex ever combines a variable-
# length run with a trailing window, so there is no backtracking blowup, and
# clause-scoping stops "3 GB RAM, welche Dateien?" from binding "Dateien"
# across the comma (the round-2 false-positive class).

# A data noun — the thing a big-data volume/count is ABOUT. Two tiers:
#  (a) an anchored regex (\b…\b) for the short / English / ambiguous nouns
#      where a compound-suffix match would be a false positive
#      (blogs→"logs", arrows→"rows", profiles→"files");
#  (b) plain substring checks for the German data HEADS that legitimately form
#      compounds (Kundentransaktionen, Verkaufsdaten, Messwerte) — done with
#      `in` on the lowercased clause, which is linear and cannot backtrack
#      (a "\b\w*(head)\b" regex would reintroduce the O(n²) blowup).
_DATA_NOUN_RE = re.compile(
    r"\b(?:logs?|logfiles?|logdatei\w*|serverlogs?|clickstreams?|"
    r"records?|rows?|zeilen|eintr[äa]ge?n?|entries|events?|"
    r"dokumente?n?|documents?|dateien|files?|exporte?\w*|dumps?|"
    r"datasets?|corpus|korpus|transactions|measurements|"
    r"backups?|buckets?|s3)\b",
    re.IGNORECASE,
)
_DATA_SUBSTR = (
    # Compound-prone data HEADS. "messung" is deliberately EXCLUDED — it is a
    # substring of the unrelated "Vermessung" (surveying), a false-positive
    # that would burn a quota unit; the more data-specific "messwert" is kept.
    "daten", "transaktion", "messwert",
    "datensatz", "datensätz", "datenbank", "datenmeng",
)
# Common words that END in "…daten" but are NOT data (Kandidaten, Mandaten,
# Soldaten, Sedaten). Stripped before the "daten" substring test so
# "5 Millionen Kandidaten" is not mis-read as a big-data task (2026-07-24
# round-3 refutation, "daten" false-friend class).
_DATA_FALSE_FRIENDS_RE = re.compile(
    r"kandidaten|mandaten|soldaten|sedaten|pedanten", re.IGNORECASE)


def _clause_has_data_noun(clause: str) -> bool:
    if _DATA_NOUN_RE.search(clause):
        return True
    low = _DATA_FALSE_FRIENDS_RE.sub("", clause.lower())
    return any(s in low for s in _DATA_SUBSTR)


_ABBREV_DOT_RE = re.compile(r"\b(mio|mrd)\.", re.IGNORECASE)
# The big-data signal (a volume/count + a nearby data noun) is a property of the
# TASK DESCRIPTION, which comes first; anything past this is pasted data, which
# needn't be scanned to know the task is big-data-shaped. Capping the scanned
# length bounds the routine to O(cap): without it, a delimiter-free numeric blob
# with many count tokens made the per-match clause scans O(n²) overall — ~2 min
# CPU on a 128 KB paste, on the async event loop (2026-07-24 round-3 refutation).
_BIG_DATA_MAX_SCAN = 2000


def _is_big_data_task(prompt: str) -> bool:
    """Deterministic big-data signal for the TDE-vs-ACS split (ADR-0217).

    Bounded: only the first _BIG_DATA_MAX_SCAN chars are scanned, so the whole
    routine (including the per-match clause scans) is O(_BIG_DATA_MAX_SCAN²)
    worst case (~a few hundred K ops), constant in the real prompt length — no
    O(n²) blowup on a pasted numeric blob (2026-07-24 round-3 refutation)."""
    prompt = prompt[:_BIG_DATA_MAX_SCAN]
    # Drop the period in "Mio."/"Mrd." so it isn't read as a clause boundary
    # that would split the magnitude word off its data noun.
    prompt = _ABBREV_DOT_RE.sub(r"\1", prompt)
    if _BIGDATA_VOCAB_RE.search(prompt):
        return True
    # TB/PB: big data unless the clause names hardware (SSD/HDD/…).
    for m in _TBPB_RE.finditer(prompt):
        lo, hi = _clause_bounds(prompt, m.start(), m.end())
        if not _HW_NOUN_RE.search(prompt[lo:hi]):
            return True
    # GB and big counts: require a data noun in the SAME clause. Dedup by
    # clause: once a clause has been checked and lacked a data noun, skip the
    # other volume/count tokens inside it — this makes a delimiter-free numeric
    # blob (all tokens in one clause) O(n) instead of O(n²) even before the
    # length cap (round-3 refutation).
    for rx in (_GB_RE, _BIG_COUNT_RE):
        _checked_hi = -1
        for m in rx.finditer(prompt):
            if m.start() < _checked_hi:
                continue  # same clause as a previous no-data-noun match
            lo, hi = _clause_bounds(prompt, m.start(), m.end())
            if _clause_has_data_noun(prompt[lo:hi]):
                return True
            _checked_hi = hi
    return False



def is_big_data_task(prompt: str) -> bool:
    """Public name for the shared big-data signal (see `_is_big_data_task`)."""
    return _is_big_data_task(prompt)
