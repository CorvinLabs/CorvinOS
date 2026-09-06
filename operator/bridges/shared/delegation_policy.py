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

#: The explicit user override that beats every classifier (ADR-0255). Shared so
#: a bridge caller and the console parse the SAME directive instead of a second
#: hand-rolled prefix check drifting from the console's word-boundary guard.
DELEGATE_PREFIX = "/delegate"


def strip_delegate_prefix(prompt: str) -> tuple[str, bool]:
    """Detect + strip an explicit ``/delegate`` routing directive (ADR-0255).

    Word-boundary match: the prefix must be the WHOLE token, so
    ``"/delegatex foo"`` is a plain prompt, not a command (2026-07-24 console
    review, mirrored here so a bridge caller uses the identical rule). Returns
    ``(possibly-stripped prompt, whether the directive was present)``.

    Every caller must call this BEFORE computing routing signals AND before
    handing the prompt to any turn — delegated or not: the raw "/delegate "
    text must never reach an LLM, even on the degrade-to-native path.
    """
    p = prompt.strip()
    pl = p.lower()
    force_delegate = pl == DELEGATE_PREFIX or pl.startswith(DELEGATE_PREFIX + " ")
    if not force_delegate:
        return prompt, False
    return p[len(DELEGATE_PREFIX):].strip(), True


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


def permitted_engines(*, mode: str, bundled: str) -> frozenset[str]:
    """What an ``engine.engine_selection`` hook may answer (ADR-0251 D2).

    A hook is an INPUT to this module's rule, never a replacement for it, so the
    only admissible answers are:

    * ``bundled`` — the answer the rule already produced (confirm it), and
    * ``"native"`` — the universal degrade floor.

    That is deliberately narrow, and the narrowness is the decision. A hook may
    **de-escalate but never escalate**. Consider what the alternatives permit:

    * allowing ``mode`` itself would let a hook re-assert ``tde`` on a turn the
      rule routed to ``native`` because TDE was unavailable or out of quota —
      the hook would be overriding an availability degrade it cannot observe;
    * allowing any member of :data:`WORKER_ENGINE_MODES` would let a plugin
      route into an engine the operator never selected, which CLAUDE.md
      § Worker Engine Selection forbids outright.

    ``mode`` is accepted and unused on purpose: it is what makes the refusal
    message and the audit record intelligible ("the operator selected X"), and
    leaving it out would invite a later caller to re-derive the permitted set
    from the mode, which is the widening this function exists to prevent.
    """
    return frozenset({bundled, WORKER_ENGINE_DEFAULT})


def _acp_shadow_route(
    *,
    mode: str | None,
    engine: str,
    force_delegate: bool,
    is_big_data: bool,
    tenant_id: str,
) -> None:
    """L5 ACP wiring in SHADOW (advisory) mode — ADR-0532 Phase 1 call site.

    Until 2026-09-06 ``os.delegation_router`` had zero production callers: the
    Skill was booted, audited and "E2E-tested", and no real routing decision
    ever reached it, so the ADR-0314 learning loop had no source signal
    (adversarial review F1). This is the one shared decision function every
    surface routes through, so it is the honest place to attach the Skill.

    Shadow means: the bundled rule's answer (``engine``) STANDS. The Skill is
    executed with the same signals, its decision lands in the hash-chained
    audit trail (``skill_executed``) and the learning store, carrying both the
    bundled engine and its own advice so agreement can be measured — and it
    changes nothing on the wire. Promoting the advice to an actual override
    goes through the ``engine.engine_selection`` extension point and its
    permitted-engines bound, never through this function.

    Never raises and never delays the turn beyond the Skill's own timeout: a
    stripped install without ``core.skills``, an un-booted registry, or a Skill
    failure all degrade to "no shadow record" and the caller's routing is
    untouched.
    """
    try:
        from core.skills import skill_registry_phase1 as _reg  # noqa: PLC0415
    except Exception:  # noqa: BLE001 — bridge-only deployment without core.skills
        return
    try:
        registry = getattr(_reg, "_global_registry", None)
        if registry is None or registry.get("os.delegation_router") is None:
            return  # not booted in this process → nothing to attribute to
        complexity = 8 if is_big_data else (7 if force_delegate else 4)
        task_type = "delegate" if force_delegate else ("big_data" if is_big_data else "chat")
        registry.execute(
            "os.delegation_router",
            {
                "complexity": complexity,
                "task_type": task_type,
                "user_context": {"mode": mode or "n/a"},
                "tenant_id": tenant_id,
                "shadow": True,
                "bundled_engine": engine,
            },
            timeout_ms=1000,
            lom="operator/bridges/shared/delegation_policy.py:_acp_shadow_route",
            tenant_id=tenant_id,
        )
    except Exception as exc:  # noqa: BLE001 — advisory only; routing already decided
        import logging as _log  # noqa: PLC0415

        _log.getLogger(__name__).debug("ACP shadow route skipped: %s", type(exc).__name__)


def resolve_worker_engine(
    *,
    mode: str,
    force_delegate: bool,
    is_big_data: bool,
    tde_available: bool,
    quota_ok: bool,
    tenant_id: str = "_default",
) -> str:
    """:func:`_resolve_worker_engine` plus the ACP L5 shadow record.

    The routing answer is computed by :func:`_resolve_worker_engine` (the rule
    and the extension-point hook). Afterwards — never before, so no Skill can
    delay or alter the decision — ``os.delegation_router`` is executed in
    shadow mode with the same signals (see :func:`_acp_shadow_route`).
    """
    engine = _resolve_worker_engine(
        mode=mode,
        force_delegate=force_delegate,
        is_big_data=is_big_data,
        tde_available=tde_available,
        quota_ok=quota_ok,
        tenant_id=tenant_id,
    )
    _acp_shadow_route(
        mode=mode,
        engine=engine,
        force_delegate=force_delegate,
        is_big_data=is_big_data,
        tenant_id=tenant_id,
    )
    return engine


def _resolve_worker_engine(
    *,
    mode: str,
    force_delegate: bool,
    is_big_data: bool,
    tde_available: bool,
    quota_ok: bool,
    tenant_id: str = "_default",
) -> str:
    """:func:`worker_engine_target`, plus the ``engine.engine_selection`` hook.

    The pure rule above stays pure — it is the unit-testable routing matrix and
    every surface's source of truth. This wrapper is the ADR-0251 D1 call site:
    it runs the rule, offers the answer to a hook, and enforces D2 by refusing
    anything outside :func:`permitted_engines`.

    The refusal is made HERE rather than in the bus on purpose. The bus knows a
    hook returned a `str`; only this module knows which strings are engines and
    which of them this operator selected. Pushing the check into the bus would
    put routing policy in a generic dispatcher (ADR-0251 D2).

    With ``plugin_extension_points`` off — the default — the bus returns the
    bundled answer without consulting anything, so this is the pre-feature path
    with one function call in front of it.
    """
    bundled = worker_engine_target(
        mode=mode,
        force_delegate=force_delegate,
        is_big_data=is_big_data,
        tde_available=tde_available,
        quota_ok=quota_ok,
    )
    try:
        from corvin_plugins import extension_points as _ep  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        # No plugin package in this install (stripped wheel, bridge-only
        # deployment). The bundled answer IS the pre-feature behaviour, so this
        # is a quiet path, not a degradation.
        return bundled

    chosen = _ep.invoke(
        "engine.engine_selection",
        {
            "mode": mode,
            "bundled": bundled,
            "force_delegate": force_delegate,
            "is_big_data": is_big_data,
        },
        default=bundled,
        tenant_id=tenant_id,
    )
    if chosen == bundled:
        return bundled

    allowed = permitted_engines(mode=mode, bundled=bundled)
    if chosen in allowed:
        return chosen

    _audit_refusal(
        "plugin.extension_engine_refused",
        {
            "point": "engine.engine_selection",
            "tenant_id": tenant_id,
            "operator_mode": mode,
            "bundled": bundled,
            # An engine id is a closed enum in this module's own vocabulary, so
            # recording the rejected value carries no user data. Recorded
            # because "a hook tried to route to tde" is the whole content of the
            # event; without it the record says only that something was refused.
            "refused": chosen if chosen in WORKER_ENGINE_MODES else "<not-an-engine>",
        },
        tenant_id=tenant_id,
    )
    return bundled


def resolve_delegation_route(
    bundled_delegate: bool,
    *,
    tenant_id: str = "_default",
    request: dict | None = None,
) -> bool:
    """:func:`_resolve_delegation_route` plus the ACP L5 shadow record for
    turns that STAY native.

    Every turn passes through this triage; only delegation-worthy turns go on
    to :func:`resolve_worker_engine` (which carries its own shadow record). A
    turn the classifier keeps in-process never reaches an engine decision, so
    without this branch the learning store would only ever see the minority of
    turns that are shaped for the ACS fan-out (live E2E, 2026-09-06). Exactly
    one shadow record per turn results: here when the answer is "native",
    there when an engine is actually chosen.
    """
    verdict = _resolve_delegation_route(bundled_delegate, tenant_id=tenant_id, request=request)
    if not verdict:
        _acp_shadow_route(
            mode=None,
            engine=WORKER_ENGINE_DEFAULT,
            force_delegate=False,
            is_big_data=False,
            tenant_id=tenant_id,
        )
    return verdict


def _resolve_delegation_route(
    bundled_delegate: bool,
    *,
    tenant_id: str = "_default",
    request: dict | None = None,
) -> bool:
    """The classifier's verdict, plus the ``delegation.route_selection_policy`` hook.

    ADR-0251 D1's third call site. The bundled classifier answers a boolean —
    "is this task shaped for the ACS fan-out?" — while the point's declared type
    is a route string, so this function is where the two vocabularies meet:

    ===================  ==========================
    classifier says      the route it means
    ===================  ==========================
    ``True``             ``"acs"``
    ``False``            ``"native"``
    ===================  ==========================

    **A hook may only ever SUPPRESS delegation.** Permitted answers are the
    bundled route (confirm) and ``"native"`` (suppress); everything else is
    refused and audited. So a hook can stop a turn from being delegated and can
    never cause one to be delegated that the classifier did not select.

    That is deliberately one-directional, and it follows from CLAUDE.md rather
    than from taste: every degrade ladder must end at ``native``, and a hook that
    could answer ``"acs"`` or ``"tde"`` on a turn the classifier declined would
    be a plugin routing work into a delegation engine on its own authority —
    spending the operator's quota through a decision the operator's own
    classifier refused.

    Returns a bool because that is what the classifier's callers consume; the
    route vocabulary exists for the hook's benefit, not theirs.
    """
    bundled_route = "acs" if bundled_delegate else WORKER_ENGINE_DEFAULT
    try:
        from corvin_plugins import extension_points as _ep  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return bundled_delegate

    chosen = _ep.invoke(
        "delegation.route_selection_policy",
        dict(request or {}, bundled=bundled_route),
        default=bundled_route,
        tenant_id=tenant_id,
    )
    if chosen == bundled_route:
        return bundled_delegate
    if chosen == WORKER_ENGINE_DEFAULT:
        return False

    _audit_refusal(
        "plugin.extension_route_refused",
        {
            "point": "delegation.route_selection_policy",
            "tenant_id": tenant_id,
            "bundled": bundled_route,
            "refused": chosen if chosen in WORKER_ENGINE_MODES else "<not-a-route>",
        },
        tenant_id=tenant_id,
    )
    return bundled_delegate


def _audit_refusal(event_type: str, details: dict, *, tenant_id: str) -> None:
    """Best-effort audit that must never cost the turn.

    This module is imported by the bridge adapter, where a missing audit writer
    is a normal deployment shape rather than a fault, so an ImportError here is
    not a compliance failure — the refusal has already taken effect by the time
    this is called, and the routing decision is unaffected either way.
    """
    try:
        from audit import audit_event  # noqa: PLC0415

        audit_event(event_type, details=details, tenant_id=tenant_id)
    except Exception:  # noqa: BLE001
        pass


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


# ── Structured-data gate (maintainer decision 2026-07-28) ─────────────────────
# The auto-ACS route is now spelled out affirmatively: it fires on the three
# shapes the maintainer named — CSV/spreadsheet FILES, DATABASE work, and
# genuine TABULAR mass data — plus the pre-existing "volume + data noun" clause.
# An ordinary conversational request, prose, or a normal coding task must never
# reach it: every ACS run burns one compute_units_per_day, and the console chat
# that triggered this change (2026-07-27) showed how expensive a wrong positive
# is. Each regex below is anchored and bounded — no variable-length run followed
# by a window — so the ReDoS properties of _is_big_data_task are preserved.

# (a) A tabular/columnar data FILE or container. Extensions are matched with a
#     leading dot so "csv" inside a word ("csvinjection") is not a source.
_DATA_FILE_RE = re.compile(
    r"\.(?:csv|tsv|psv|parquet|avro|orc|feather|xlsx?|xlsm|ods|jsonl|ndjson)\b"
    r"|\b(?:csv|tsv|parquet|avro|jsonl|ndjson|excel|xlsx)[\s\-]?"
    r"(?:datei\w*|files?|export\w*|dump\w*|tabellen?|sheets?)\b"
    r"|\b(?:spreadsheets?|tabellenkalkulation\w*|data[\s\-]?frames?|dataframes?)\b",
    re.IGNORECASE,
)
# (b) A database / query operation. Deliberately NOT a bare "Tabelle": the word
#     alone is ordinary German ("erstelle eine Tabelle" is a formatting wish,
#     asserted non-big-data since the 2026-07-24 round-3 refutation) — only a
#     DB-qualified table counts.
_DB_RE = re.compile(
    r"\b(?:datenbank\w*|databases?|sql|nosql|postgre(?:s|sql)?|mysql|mariadb|"
    r"sqlite|mongodb|clickhouse|bigquery|redshift|snowflake|duckdb|oracle[\s\-]?db|"
    r"dwh|olap)\b"
    r"|\b(?:select|insert\s+into|update|delete\s+from)\b[^\n]{0,80}?\bfrom\b"
    r"|\b(?:inner|left|right|outer|cross)\s+joins?\b"
    r"|\bgroup\s+by\b|\border\s+by\b"
    r"|\b(?:db|sql|datenbank)[\s\-]?(?:tabellen?|tables?|schemas?|queries|query|"
    r"abfrage\w*|dumps?|migration\w*)\b",
    re.IGNORECASE,
)
# (c) A BULK data-processing verb. The discriminator that keeps a mere mention
#     of a database or a CSV from fanning out: "Wie verbinde ich mich mit
#     MySQL?" and "Fasse die Datenbank-Migration zusammen" name a source but ask
#     for prose. Summarise/explain/document verbs are deliberately absent.
_DATA_WORK_VERB_RE = re.compile(
    r"\b(?:analysier\w*|analyz\w*|analys\w*|auswert\w*|werte\s+\w+\s+aus|"
    r"aggregier\w*|aggregat\w*|gruppier\w*|pivot\w*|"
    r"importier\w*|import\w*|exportier\w*|"
    r"bereinig\w*|dedupliz\w*|deduplicat\w*|"
    r"joine?\w*|verkn[üu]pf\w*|merge[nrs]?\b|"
    r"abfrag\w*|quer(?:y|ies|ie)\w*|"
    r"verarbeit\w*|process(?:es|ed|ing)?\b|"
    r"durchsuch\w*|scanne?\w*|parse[nrs]?\b|"
    r"statistik\w*|kennzahl\w*|auswertung\w*)\b",
    re.IGNORECASE,
)
# (d) Code context — a volume/count that is ABOUT source code is a coding task,
#     and "Coding never routes into the ACS fan-out" (delegation-routing.md §6).
#     "2 Millionen Zeilen Code refaktorieren" used to fan out because "Zeilen"
#     is a data noun; clause-scoped like the hardware carve-out above.
_CODE_NOUN_RE = re.compile(
    r"\b(?:code|quellcode|sourcecode|source\s?code|codezeilen|loc|"
    r"lines?\s+of\s+code|repositor(?:y|ies)|codebase|commits?)\b",
    re.IGNORECASE,
)
# (e) A genuinely TABULAR paste: a pipe/markdown table. A three-row table inside
#     an ordinary question is not mass data, so a real row FLOOR is required.
_TABLE_MIN_ROWS = 10
# Table rows are the PAYLOAD, so they are scanned past _BIG_DATA_MAX_SCAN (which
# bounds the task DESCRIPTION). Own, larger cap: the row scan is one linear
# anchored pass, but an unbounded paste should still not be re-scanned in full.
_TABLE_MAX_SCAN = 200_000
_TABLE_ROW_RE = re.compile(r"^[ \t]*\|.*\|[ \t]*$", re.MULTILINE)
# A markdown separator row (|---|---|) is structure, not data.
_TABLE_SEP_ROW_RE = re.compile(r"^[ \t]*\|[\s:\-|]+\|[ \t]*$")


def _table_row_count(text: str) -> int:
    """How many content rows a pipe/markdown table in ``text`` contributes.

    Markdown separator rows (``|---|---|``) are pure structure and excluded; a
    header row is counted, which is why the floor is 10 rather than a number
    tuned to exact record counts.
    Linear in ``len(text)``: one anchored MULTILINE scan, no backtracking.
    """
    return sum(
        1 for m in _TABLE_ROW_RE.finditer(text[:_TABLE_MAX_SCAN])
        if not _TABLE_SEP_ROW_RE.match(m.group(0))
    )


def _has_volume_signal(prompt: str) -> bool:
    """A TB/PB/GB volume (not hardware) or a big count, anywhere in ``prompt``.

    Used only once a structured SOURCE is already established, so — unlike the
    legacy clause-proximity path below — no data noun is required; the hardware
    carve-out still applies so "die Postgres-VM hat 64 GB RAM" is not a volume.
    """
    for rx in (_TBPB_RE, _GB_RE):
        for m in rx.finditer(prompt):
            lo, hi = _clause_bounds(prompt, m.start(), m.end())
            if not _HW_NOUN_RE.search(prompt[lo:hi]):
                return True
    return bool(_BIG_COUNT_RE.search(prompt))


_ABBREV_DOT_RE = re.compile(r"\b(mio|mrd)\.", re.IGNORECASE)
# The big-data signal (a volume/count + a nearby data noun) is a property of the
# TASK DESCRIPTION, which comes first; anything past this is pasted data, which
# needn't be scanned to know the task is big-data-shaped. Capping the scanned
# length bounds the routine to O(cap): without it, a delimiter-free numeric blob
# with many count tokens made the per-match clause scans O(n²) overall — ~2 min
# CPU on a 128 KB paste, on the async event loop (2026-07-24 round-3 refutation).
_BIG_DATA_MAX_SCAN = 2000


def _is_big_data_task(prompt: str) -> bool:
    """Deterministic structured-data signal — the ONE auto-delegation to ACS.

    Four affirmative shapes, in cost order (maintainer decision 2026-07-28,
    narrowing ADR-0217's original "any volume + any data noun" rule):

    1. Self-describing big-data vocabulary ("Big Data", "Data Lake", "riesige
       Datenmengen") — the shape ADR-0217 was written for.
    2. A genuinely TABULAR paste: a pipe/markdown table of at least
       ``_TABLE_MIN_ROWS`` data rows. The table IS the mass data.
    3. A named structured SOURCE — a CSV/spreadsheet-class file or a
       database/SQL operation — PAIRED with a bulk data-processing verb or a
       volume. The pairing is load-bearing: naming a database is not doing
       database work, and "Wie verbinde ich mich mit MySQL?" must stay a
       normal in-process turn.
    4. The legacy clause-proximity rule: a volume/count (GB/TB/PB, millions,
       grouped counts) tied to a data noun in the SAME clause — now with a
       CODE carve-out, so "2 Millionen Zeilen Code refaktorieren" stays on the
       sequential direct turn as delegation-routing.md §6 requires.

    Everything else — ordinary questions, prose, normal coding requests — is
    False, and a False here means the turn runs natively and costs no quota.

    Bounded: only the first _BIG_DATA_MAX_SCAN chars are scanned, so the whole
    routine (including the per-match clause scans) is O(_BIG_DATA_MAX_SCAN²)
    worst case (~a few hundred K ops), constant in the real prompt length — no
    O(n²) blowup on a pasted numeric blob (2026-07-24 round-3 refutation)."""
    full = prompt
    prompt = prompt[:_BIG_DATA_MAX_SCAN]
    # Drop the period in "Mio."/"Mrd." so it isn't read as a clause boundary
    # that would split the magnitude word off its data noun.
    prompt = _ABBREV_DOT_RE.sub(r"\1", prompt)
    # (1) Self-describing big-data vocabulary.
    if _BIGDATA_VOCAB_RE.search(prompt):
        return True
    # (2) A pasted table with enough rows to BE mass data. Counted on the
    # UNTRUNCATED prompt: a big table's rows are the payload, and the task
    # description that _BIG_DATA_MAX_SCAN bounds sits above them.
    if _table_row_count(full) >= _TABLE_MIN_ROWS:
        return True
    # (3) A named CSV/spreadsheet or database source doing actual bulk work.
    if _DATA_FILE_RE.search(prompt) or _DB_RE.search(prompt):
        if _DATA_WORK_VERB_RE.search(prompt) or _has_volume_signal(prompt):
            return True
    # (4) TB/PB: big data unless the clause names hardware (SSD/HDD/…) or code.
    for m in _TBPB_RE.finditer(prompt):
        lo, hi = _clause_bounds(prompt, m.start(), m.end())
        _clause = prompt[lo:hi]
        if not _HW_NOUN_RE.search(_clause) and not _CODE_NOUN_RE.search(_clause):
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
            _clause = prompt[lo:hi]
            if _clause_has_data_noun(_clause) and not _CODE_NOUN_RE.search(_clause):
                return True
            _checked_hi = hi
    return False



def is_big_data_task(prompt: str) -> bool:
    """Public name for the shared big-data signal (see `_is_big_data_task`)."""
    return _is_big_data_task(prompt)
