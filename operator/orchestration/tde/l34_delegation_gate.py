"""ADR-0214: L34 Data-Aware Delegation Gate (Fail-Closed).

Enforces GDPR/compliance: no data leaks allowed. Checks if a step can be
safely delegated based on data classification (PUBLIC, INTERNAL, CONFIDENTIAL, RESTRICTED).

Provides:
1. can_delegate_step() — binary decision (safe or not)
2. prescan() — engine-agnostic pre-gate over a whole context (no step)
3. filter_plan() — sanitize GlobalPlan before DelegationEnvelope
4. sanitize_snapshot() — filter statement to safe variables only

Fail-closed contract: if the injected L34 classifier raises, the variable is
treated as RESTRICTED (never silently downgraded to a heuristic guess).
The built-in fallback heuristic inspects BOTH the variable name and the
value content (secrets, e-mail, phone patterns).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

try:
    from ..initial_analysis import GlobalPlan, Step
except ImportError:  # pragma: no cover - direct sys.path import (tests)
    from initial_analysis import GlobalPlan, Step  # type: ignore

_logger = logging.getLogger(__name__)

# Content patterns → RESTRICTED (secrets must never leave the process).
# All quantifiers are BOUNDED: the round-2 refutation measured 114s of
# backtracking on a 256KB alphanumeric run with an unbounded email regex —
# every pattern here must stay linear-ish on adversarial inputs.
_SECRET_VALUE_PATTERNS = [
    re.compile(r"\bsk[-_](?:live|test|proj|ant)[-_][\w-]{1,80}", re.IGNORECASE),  # Stripe/API style keys
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                                      # AWS access key
    re.compile(r"-----BEGIN [A-Z ]{0,30}PRIVATE KEY-----"),                   # PEM keys
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,80}\b"),                          # GitHub tokens
    re.compile(r"\beyJ[\w-]{10,600}\.eyJ[\w-]{10,600}\.[\w-]{1,600}"),        # JWT
    # Keyword-anchored assignment (covers env-dump style like
    # AWS_SECRET_ACCESS_KEY=… via the mid-identifier keyword hit).
    # Round-3 refutation constraints baked in:
    # - NO leading \w{0,40} context (that made scans ~1600 ops/char — the
    #   keyword anchors by itself, the engine's literal prefix scan is linear)
    # - trailing continuation only via [_-] so "tokens"/"tokenizer" in normal
    #   code do NOT match ("max_tokens: 30000" was RESTRICTED before)
    # - value must contain a letter: pure numerics (ports, limits) are
    #   config values, not secrets
    re.compile(
        r"(?:secret|password|passwd|credential|api[-_]?key|access[-_]?key"
        r"|(?<![a-z0-9])token)s?(?:[_-]\w{1,40})?\s*[=:]\s*['\"]?(?=[^\s'\"]{0,199}[A-Za-z])\S{4,200}",
        re.IGNORECASE,
    ),
]

# Content patterns → CONFIDENTIAL (PII). Bounded (see above): an email local
# part >64 chars / label >63 chars is invalid per RFC anyway.
_EMAIL_RE = re.compile(r"[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){1,8}")
_PHONE_RE = re.compile(r"\+\d{1,3}[-.\s]?\d{3,14}[-.\s]?\d{3,14}")
# Financial / national-ID PII that carries NO e-mail/phone shape and so used to
# slip through as PUBLIC and reach a delegated worker unredacted (2026-07-24
# review, HIGH). All bounded → no ReDoS. Erring toward CONFIDENTIAL is the
# fail-closed direction: a false positive only forces the (metered) local
# claude_code engine, never a data leak.
# Separators seen in copy-pasted card / IBAN numbers: space, dash, dot, slash,
# and the non-breaking space common in EU-formatted paste (2026-07-24 round-4
# refutation — the earlier `[ -]`-only class let a real, Luhn-valid card written
# "4111.1111.1111.1111" / with nbsp slip through as PUBLIC to a worker).
_PII_SEP = r"[ \-./  ]"
_PII_SEP_RE = re.compile(_PII_SEP)
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# IBAN candidate START: 2 letters + 2 check digits, then a run of alnum split
# by any separator. Case-insensitive (copy-paste often lowercases IBANs); the
# mod-97 checksum is what prevents false positives.
#
# Round-7 refutation (2026-07-24): the earlier single greedy `{11,32}` match
# EATS the space + a following prose word ("DE89 3704 … 0130 00 please" →
# stripped includes "PLEASE") so mod-97 then fails and a REAL IBAN in a
# sentence leaked as PUBLIC. Fix: validate against the country-specific IBAN
# LENGTH — test mod-97 on exactly that prefix of the stripped candidate, so a
# trailing word is ignored. Testing one known length per country (not every
# prefix) also keeps the random-string false-positive rate at ~1/97.
_IBAN_CANDIDATE_RE = re.compile(
    r"\b[A-Za-z]{2}\d{2}(?:" + _PII_SEP + r"?[A-Za-z0-9]){11,40}")
# Official ISO 13616 IBAN registry lengths (total chars incl. country + check).
_IBAN_LENGTHS: dict[str, int] = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16,
    "BG": 22, "BH": 22, "BR": 29, "BY": 28, "CH": 21, "CR": 22, "CY": 28,
    "CZ": 24, "DE": 22, "DK": 18, "DO": 28, "EE": 20, "EG": 29, "ES": 24,
    "FI": 18, "FO": 18, "FR": 27, "GB": 22, "GE": 22, "GI": 23, "GL": 18,
    "GR": 27, "GT": 28, "HR": 21, "HU": 28, "IE": 22, "IL": 23, "IQ": 23,
    "IS": 26, "IT": 27, "JO": 30, "KW": 30, "KZ": 20, "LB": 28, "LC": 32,
    "LI": 21, "LT": 20, "LU": 20, "LV": 21, "LY": 25, "MC": 27, "MD": 24,
    "ME": 22, "MK": 19, "MR": 27, "MT": 31, "MU": 30, "NL": 18, "NO": 15,
    "PK": 24, "PL": 28, "PS": 29, "PT": 25, "QA": 29, "RO": 24, "RS": 22,
    "SA": 24, "SC": 31, "SE": 24, "SI": 19, "SK": 24, "SM": 27, "ST": 25,
    "SV": 28, "TL": 23, "TN": 24, "TR": 26, "UA": 29, "VA": 22, "VG": 24,
    "XK": 20,
}
# Payment-card candidate: 13–19 digits in the common 4-…-grouped form. Only PII
# if it ALSO passes the Luhn checksum — strips false positives (order numbers,
# coordinates) while catching real cards, which are Luhn-valid by construction.
_CARD_CANDIDATE_RE = re.compile(
    r"\b\d{4}(?:" + _PII_SEP + r"?\d{4}){2,4}(?:" + _PII_SEP + r"?\d{1,3})?\b")


def _luhn_ok(digits: str) -> bool:
    """Standard Luhn checksum; digits is a bare digit string (13–19 len)."""
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _has_payment_card(text: str) -> bool:
    for m in _CARD_CANDIDATE_RE.finditer(text):
        digits = _PII_SEP_RE.sub("", m.group())
        if digits.isdigit() and _luhn_ok(digits):
            return True
    return False


def _iban_mod97_ok(iban: str) -> bool:
    """ISO 13616 / ISO 7064 mod-97 checksum. `iban` is the separator-stripped,
    upper-cased candidate of the exact expected length. mod-97 == 1 for a real
    IBAN; a random alnum string almost never is."""
    if len(iban) < 4 or not (iban[:2].isalpha() and iban[2:4].isdigit()):
        return False
    rearranged = iban[4:] + iban[:4]
    digits = []
    for ch in rearranged:
        if ch.isdigit():
            digits.append(ch)
        elif "A" <= ch <= "Z":  # A–Z → 10–35
            digits.append(str(ord(ch) - 55))
        else:
            return False
    try:
        return int("".join(digits)) % 97 == 1
    except ValueError:
        return False


def _has_iban(text: str) -> bool:
    for m in _IBAN_CANDIDATE_RE.finditer(text):
        stripped = _PII_SEP_RE.sub("", m.group()).upper()
        cc = stripped[:2]
        # A real IBAN ALWAYS carries a registered ISO country code — so we only
        # accept those, and test mod-97 on EXACTLY that country's IBAN length.
        # This (a) ignores a trailing prose word the greedy candidate swallowed
        # ("DE89 3704 … 00 please" → test the first 22 chars, round-7 fix), and
        # (b) keeps the random-string false-positive rate at the ~1/97 checksum
        # floor (one length test, registered CC only) instead of exploding it
        # across every length for made-up country codes (round-7 refutation).
        n = _IBAN_LENGTHS.get(cc)
        if n and n <= len(stripped) and _iban_mod97_ok(stripped[:n]):
            return True
    return False

# Fail-closed size ceiling for content scans: values larger than this are
# classified RESTRICTED outright — content we did not scan cannot be proven
# safe (round-2 refutation: a secret BEYOND a scan window would previously
# ride along unredacted).
_CONTENT_SCAN_MAX_BYTES = 5 * 1024 * 1024


@dataclass
class DelegationGateResult:
    """Result of can_delegate_step() check."""
    can_delegate: bool
    reason: str


class L34DelegationGate:
    """L34 Data-Safe Gate: fail-closed enforcement."""

    # Classification levels (from L34)
    CLASSIFICATIONS = ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
    CLASSIFICATION_RANK = {c: i for i, c in enumerate(CLASSIFICATIONS)}

    def __init__(self, l34_classifier: Optional[Any] = None):
        """Initialize gate with optional L34 classifier.

        Args:
            l34_classifier: L34 Flow Guard classifier. When None, the built-in
                name+content heuristic is used. When provided and it raises,
                the variable is classified RESTRICTED (fail-closed).
        """
        self.l34_classifier = l34_classifier
        # Per-gate memo for content classification. The executor scans the
        # SAME statement values once per step (conservative required-vars
        # superset) — without the memo a multi-MB value was re-scanned for
        # every step of the plan, synchronously on the event loop (round-3
        # refutation finding). str.__hash__ is cached per object, so keys
        # are cheap for repeated lookups of the same value object.
        self._content_cache: dict[str, str] = {}

    def can_delegate_step(
        self,
        step: Optional[Step],
        statement: dict[str, Any],
        max_classification: str = "INTERNAL"
    ) -> DelegationGateResult:
        """
        Decide: can this step be delegated?

        FAIL-CLOSED: If ANY required variable exceeds max_classification,
        return False. No exceptions, no heuristics.

        Args:
            step: Step to evaluate (None = whole-context prescan)
            statement: Current statement context
            max_classification: Max allowed classification ("PUBLIC" | "INTERNAL")

        Returns:
            DelegationGateResult (can_delegate: bool, reason: str)
        """
        if max_classification not in self.CLASSIFICATION_RANK:
            # Unknown ceiling → strictest interpretation (fail-closed).
            return DelegationGateResult(
                can_delegate=False,
                reason=f"Unknown max_classification '{max_classification}'",
            )

        # Get step's required variables (from depends_on analysis)
        required_vars = self._get_required_variables(step, statement)

        # Check each variable
        for var_name in required_vars:
            if var_name not in statement:
                continue  # Variable not in statement, ignore

            var_value = statement[var_name]
            data_class = self._classify_variable(var_name, var_value)

            # Fail-closed: if exceeds max, reject
            if self._exceeds_max(data_class, max_classification):
                return DelegationGateResult(
                    can_delegate=False,
                    reason=f"Variable '{var_name}' is {data_class} (exceeds {max_classification})"
                )

        # All variables are safe
        return DelegationGateResult(
            can_delegate=True,
            reason=f"All required variables are {max_classification} or lower"
        )

    def prescan(
        self,
        context: dict[str, Any],
        max_classification: str = "INTERNAL",
    ) -> DelegationGateResult:
        """Engine-agnostic pre-gate: scan a whole context before engine selection.

        Semantically identical to can_delegate_step(None, context), but with an
        explicit name so call sites don't abuse the step API.
        """
        return self.can_delegate_step(None, context, max_classification=max_classification)

    def filter_plan(
        self,
        plan: GlobalPlan,
        max_classification: str = "INTERNAL"
    ) -> GlobalPlan:
        """
        Filter GlobalPlan to remove sensitive entities.

        Entities extracted by InitialAnalysis (e.g., customer emails, API keys)
        are classified; sensitive ones removed from step descriptions.

        Args:
            plan: Full GlobalPlan (may contain sensitive entities)
            max_classification: Max allowed classification

        Returns:
            Filtered GlobalPlan (safe for DelegationEnvelope)
        """

        filtered_steps = []
        for step in plan.steps:
            # Filter BOTH free-text fields (action label + description) —
            # the LM may embed entities (emails, keys) in either.
            filtered_step = Step(
                step=step.step,
                action=self._filter_text(step.action, max_classification),
                depends_on=step.depends_on,
                can_parallelize=step.can_parallelize,
                estimated_tokens=step.estimated_tokens,
                description=self._filter_text(step.description, max_classification),
            )
            filtered_steps.append(filtered_step)

        filtered_plan = GlobalPlan(
            steps=filtered_steps,
            estimated_duration_s=plan.estimated_duration_s,
            estimated_tokens=plan.estimated_tokens,
            fallback_strategy=plan.fallback_strategy,
        )

        return filtered_plan

    def sanitize_snapshot(
        self,
        statement: dict[str, Any],
        required_vars: set[str],
        max_classification: str = "INTERNAL"
    ) -> dict[str, Any]:
        """
        Filter statement snapshot to only safe data.

        Args:
            statement: Full statement context
            required_vars: Variables that this step needs
            max_classification: Max allowed classification

        Returns:
            Sanitized snapshot (only safe variables)
        """

        snapshot = {}
        for var in required_vars:
            if var not in statement:
                continue

            var_value = statement[var]
            data_class = self._classify_variable(var, var_value)

            if not self._exceeds_max(data_class, max_classification):
                # Safe: include
                snapshot[var] = var_value
            else:
                # Unsafe: replace with placeholder
                snapshot[var] = f"[{data_class}_DATA_REDACTED]"

        return snapshot

    def _classify_variable(self, var_name: str, var_value: Any) -> str:
        """Classify a variable based on name + content (fail-closed)."""

        if self.l34_classifier is not None:
            try:
                return self.l34_classifier.classify(var_value)
            except Exception as exc:
                # FAIL-CLOSED: a broken classifier must never let data through.
                _logger.warning(
                    "L34 classifier raised (%s) for variable '%s' — treating as RESTRICTED",
                    type(exc).__name__, var_name,
                )
                return "RESTRICTED"

        return self._heuristic_classify(var_name, var_value)

    def _heuristic_classify(self, var_name: str, var_value: Any) -> str:
        """Built-in fallback: name heuristic + content scan."""
        # Non-str keys (ints, tuples) must not crash the gate (DoS class).
        lower_name = str(var_name).lower()

        if any(x in lower_name for x in ["password", "secret", "token", "key", "credential"]):
            return "RESTRICTED"

        # Content scan (bounded) — catches PII/secrets hiding under benign names.
        content_class = self._classify_content(var_value)
        if content_class == "RESTRICTED":
            return "RESTRICTED"

        if any(x in lower_name for x in ["email", "phone", "customer", "user", "customer_id"]):
            return "CONFIDENTIAL"
        if content_class == "CONFIDENTIAL":
            return "CONFIDENTIAL"

        if any(x in lower_name for x in ["internal", "config", "database", "api"]):
            return "INTERNAL"

        # Default: PUBLIC
        return "PUBLIC"

    def _classify_content(self, var_value: Any) -> str:
        """Scan the FULL value content for secret/PII patterns.

        Values above _CONTENT_SCAN_MAX_BYTES are RESTRICTED outright
        (fail-closed): an unscanned tail cannot be proven safe, and a
        partial-window scan would let a secret beyond the window ride
        along in the un-truncated snapshot.
        """
        if var_value is None:
            return "PUBLIC"
        try:
            text = var_value if isinstance(var_value, str) else str(var_value)
        except Exception:
            # Unstringifiable object → we cannot prove it's safe.
            return "RESTRICTED"
        if len(text) > _CONTENT_SCAN_MAX_BYTES:
            return "RESTRICTED"

        # Keyed by the text itself, NOT (len(text), hash(text)): a hash
        # collision between two DIFFERENT same-length strings would have
        # returned the wrong cached classification — for a data-safety
        # classifier that can mean a secret misclassified as PUBLIC via a
        # stale collision. Python's hash() has no collision-resistance
        # guarantee (round-4 finding; confirmed via a real delegated TDE
        # review of this exact function during the round-4 dogfood run).
        # str.__hash__ is cached per object, so repeated lookups of the SAME
        # object are still cheap; using the string as the key costs nothing
        # extra dict-wise (Python hashes it either way) and removes the
        # false-equality risk entirely.
        cached = self._content_cache.get(text)
        if cached is not None:
            return cached

        result = "PUBLIC"
        for pat in _SECRET_VALUE_PATTERNS:
            if pat.search(text):
                result = "RESTRICTED"
                break
        if result == "PUBLIC" and (
            _EMAIL_RE.search(text)
            or _PHONE_RE.search(text)
            or _has_iban(text)
            or _SSN_RE.search(text)
            or _has_payment_card(text)
        ):
            result = "CONFIDENTIAL"

        if len(self._content_cache) > 512:
            self._content_cache.clear()
        self._content_cache[text] = result
        return result

    def _exceeds_max(self, data_class: str, max_classification: str) -> bool:
        """Check if data_class exceeds max_classification.

        Unknown classifications rank as RESTRICTED (fail-closed).
        """
        rank = self.CLASSIFICATION_RANK.get(data_class, self.CLASSIFICATION_RANK["RESTRICTED"])
        return rank > self.CLASSIFICATION_RANK.get(max_classification, 0)

    def _get_required_variables(self, step: Optional[Step], statement: dict[str, Any]) -> set[str]:
        """Infer required variables for a step (based on action + statement keys).

        Conservative: all variables in statement. A step=None prescan uses
        the same rule. CONSEQUENCE (documented, round-2 review): with this
        superset, ONE over-ceiling variable blocks delegation of every step
        — the sanitize_snapshot redaction path only becomes reachable once
        per-step required_variables wiring (entity data from InitialAnalysis)
        narrows this set. Safety over throughput, per ADR-0214 tradeoffs.
        """
        return set(statement.keys())

    def _filter_text(self, text: str, max_classification: str) -> str:
        """Filter sensitive entities from step descriptions.

        Secrets (RESTRICTED) are ALWAYS redacted — no max_classification ever
        allows delegating a secret. PII (CONFIDENTIAL) is redacted whenever the
        ceiling is below CONFIDENTIAL.
        """
        for pat in _SECRET_VALUE_PATTERNS:
            text = pat.sub("[SECRET_REDACTED]", text)

        if self.CLASSIFICATION_RANK.get(max_classification, 0) < self.CLASSIFICATION_RANK["CONFIDENTIAL"]:
            text = _EMAIL_RE.sub("[EMAIL_REDACTED]", text)
            text = _PHONE_RE.sub("[PHONE_REDACTED]", text)

        return text
