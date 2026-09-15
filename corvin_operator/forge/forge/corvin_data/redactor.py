"""Redaction strategies — what the LLM sees for each PII class.

Six strategies, applied per (column, pii_class) pair:

  * ``drop``           — column missing from sample + stats entirely
  * ``redact``         — literal ``<email>`` / ``<phone>`` tag (default)
  * ``pseudonymize``   — ``***pseudo:A1B2***`` deterministic by seed + value
  * ``mask_partial``   — class-specific partial reveal (``j****@***.com``)
  * ``aggregate_only`` — column missing from sample; stats kept
  * ``hash``           — SHA-256 of value, first 8 hex chars

The strategy choice flows from the operator's ``data_policy.yaml``
(Phase 12.3): a default for every detected PII class, plus per-column
overrides. ``apply_redaction(snapshot, policy, seed)`` is the single
entry point; it mutates the snapshot in place and returns it.

Phase 12.6 wires the Vault-derived per-tenant pseudonymisation seed
into this module's ``pseudonymize`` function via the ``seed`` arg.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from .snapshot import Snapshot


# ---------------------------------------------------------------------------
# Strategy catalog
# ---------------------------------------------------------------------------

STRATEGIES = (
    "drop",
    "redact",
    "pseudonymize",
    "mask_partial",
    "aggregate_only",
    "hash",
)


class RedactionError(ValueError):
    """Raised on unknown strategy / invalid policy shape."""


# ---------------------------------------------------------------------------
# Per-class redaction tags
# ---------------------------------------------------------------------------

# The literal label that replaces a value under the ``redact`` strategy.
# Stable, JSON-safe, and explicitly marked so a reader of the LLM
# prompt sees "this is a placeholder, not real data".
_REDACT_TAG = {
    "email":         "<email>",
    "phone":         "<phone>",
    "iban":          "<iban>",
    "credit_card":   "<credit_card>",
    "us_ssn":        "<us_ssn>",
    "ch_ahv":        "<ch_ahv>",
    "de_steuer_id":  "<de_steuer_id>",
    "name":          "<name>",
    "date_of_birth": "<date_of_birth>",
    "address":       "<address>",
    "opaque_id":     "<opaque_id>",
    "national_id":   "<national_id>",
}


# ---------------------------------------------------------------------------
# Per-strategy implementation
# ---------------------------------------------------------------------------

def redact(value: Any, pii_class: str) -> str:
    """Return the class-tag placeholder for *value*."""
    return _REDACT_TAG.get(pii_class, f"<{pii_class}>")


def pseudonymize(value: Any, pii_class: str, *, seed: str) -> str:
    """Deterministic per-(seed, value) token. Same seed + same value
    → same pseudonym; different seed → different pseudonym (no
    cross-tenant linkability).

    Format: ``***pseudo:<8 uppercase hex>***``. The fixed prefix /
    suffix makes the placeholder unambiguously not-real-data when an
    LLM reasons about it.
    """
    if value is None:
        return _REDACT_TAG.get(pii_class, f"<{pii_class}>")
    s = value if isinstance(value, str) else str(value)
    h = hashlib.sha256()
    h.update(seed.encode("utf-8"))
    h.update(b"\0")
    h.update(s.encode("utf-8"))
    return f"***pseudo:{h.hexdigest()[:8].upper()}***"


def mask_partial(value: Any, pii_class: str) -> str:
    """Class-specific partial reveal.

    Designed for cases where the operator wants the *shape* visible
    (domain of an email, last 4 of a credit card) without the
    sensitive part. Falls back to a generic mask for classes without
    a specific rule.
    """
    if value is None:
        return _REDACT_TAG.get(pii_class, f"<{pii_class}>")
    s = value if isinstance(value, str) else str(value)
    s = s.strip()

    if pii_class == "email":
        # j****@***.com — first letter of local + masked + first letter
        # of domain + masked + tld.
        m = re.match(r"^([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9])[A-Za-z0-9.-]*\.([A-Za-z]{2,})\s*$", s)
        if m:
            return f"{m.group(1)}****@{m.group(2)}***.{m.group(3)}"
        return "<email>"

    if pii_class == "phone":
        # Keep the last 4 digits visible.
        digits = re.sub(r"\D", "", s)
        if len(digits) >= 4:
            return f"+{'*' * (len(digits) - 4)}{digits[-4:]}"
        return "<phone>"

    if pii_class == "iban":
        # Keep first 4 + last 4 chars (country + check + start of BBAN
        # + last 4 of account).
        if len(s) >= 8:
            return f"{s[:4]}{'*' * (len(s) - 8)}{s[-4:]}"
        return "<iban>"

    if pii_class == "credit_card":
        digits = re.sub(r"\D", "", s)
        if len(digits) >= 4:
            return f"{'*' * (len(digits) - 4)}{digits[-4:]}"
        return "<credit_card>"

    if pii_class == "us_ssn":
        digits = re.sub(r"\D", "", s)
        if len(digits) >= 4:
            return f"***-**-{digits[-4:]}"
        return "<us_ssn>"

    if pii_class == "ch_ahv":
        # Keep the 756 prefix + last 2 digits.
        digits = re.sub(r"\D", "", s)
        if len(digits) >= 5 and digits.startswith("756"):
            return f"756.****.****.{digits[-2:]}"
        return "<ch_ahv>"

    if pii_class == "de_steuer_id":
        digits = re.sub(r"\D", "", s)
        if len(digits) >= 4:
            return f"{'*' * (len(digits) - 4)}{digits[-4:]}"
        return "<de_steuer_id>"

    # Generic fallback — first char + asterisks + last char (when long
    # enough); otherwise full mask.
    if len(s) >= 4:
        return f"{s[0]}{'*' * (len(s) - 2)}{s[-1]}"
    return "<masked>"


def hash_value(value: Any, *, prefix_len: int = 12) -> str:
    """``<hash:abcdef012345>`` — SHA-256 prefix. Stable per value,
    cross-seed (no per-tenant scoping). Used when the operator wants
    LOSSLESS joinability across datasets but no semantic decode.
    """
    if value is None:
        return "<null>"
    s = value if isinstance(value, str) else str(value)
    digest = hashlib.sha256(s.encode("utf-8")).hexdigest()
    return f"<hash:{digest[:prefix_len]}>"


# ---------------------------------------------------------------------------
# Policy shape
# ---------------------------------------------------------------------------

@dataclass
class RedactionPolicy:
    """The operator-controlled redaction envelope.

    *default_strategy* applies to every detected PII class unless
    overridden. *class_strategies* sets a per-PII-class default;
    *column_overrides* applies last (winning) and accepts BOTH a
    strategy name and a forced pii_class (rare — useful for
    "free-text column with embedded names" cases).
    """

    default_strategy:  str = "redact"
    class_strategies:  dict[str, str] | None = None     # {"email": "pseudonymize"}
    column_overrides:  dict[str, str] | None = None     # {"customer_email": "pseudonymize"}
    column_pii_class:  dict[str, str] | None = None     # operator-tagged

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        if self.default_strategy not in STRATEGIES:
            raise RedactionError(
                f"default_strategy {self.default_strategy!r} not in {STRATEGIES}"
            )
        for cls, strat in (self.class_strategies or {}).items():
            if strat not in STRATEGIES:
                raise RedactionError(
                    f"class_strategies[{cls!r}] = {strat!r} not in {STRATEGIES}"
                )
        for col, strat in (self.column_overrides or {}).items():
            if strat not in STRATEGIES:
                raise RedactionError(
                    f"column_overrides[{col!r}] = {strat!r} not in {STRATEGIES}"
                )

    def strategy_for(self, column: str, pii_class: str | None) -> str:
        """Pick the effective strategy for (column, pii_class).

        Resolution order:
          1. column_overrides[column] — most specific
          2. class_strategies[pii_class]
          3. default_strategy
        """
        if self.column_overrides and column in self.column_overrides:
            return self.column_overrides[column]
        if pii_class and self.class_strategies and pii_class in self.class_strategies:
            return self.class_strategies[pii_class]
        return self.default_strategy


# Cf. ADR-0012 §B — defaults that minimise leak while keeping snapshots
# usable. ``redact`` is the canonical default; the operator narrows or
# widens via data_policy.yaml.
DEFAULT_POLICY = RedactionPolicy(default_strategy="redact")


# ---------------------------------------------------------------------------
# Application — mutate a Snapshot in place
# ---------------------------------------------------------------------------

def apply_redaction(
    snapshot: Snapshot,
    policy:   RedactionPolicy | None = None,
    *,
    seed:     str | None = None,
) -> Snapshot:
    """Apply *policy* to *snapshot*, in place.

    Behaviour per strategy:
      * ``drop``           — remove the column from sample + stats + schema
      * ``aggregate_only`` — remove column from sample; keep schema + stats
      * ``redact``         — replace sample values with class tag
      * ``pseudonymize``   — replace sample values with pseudo token (needs seed)
      * ``mask_partial``   — replace sample values with class-specific mask
      * ``hash``           — replace sample values with SHA-256 prefix tag

    Columns without a detected PII class are left untouched (the LLM
    can see them in clear). To force redaction of a non-PII column,
    operator sets a ``column_overrides`` entry pointing at the column.

    The ``seed`` arg is required IFF any active strategy is
    ``pseudonymize``. Without a seed for a pseudonymize column, we
    fall back to ``redact`` (and log nothing — Phase 12.6 will
    emit a warning audit event when this fall-back fires).
    """
    pol = policy or DEFAULT_POLICY

    # First pass — identify columns to drop entirely (drop strategy).
    drop_cols: set[str] = set()
    aggregate_cols: set[str] = set()
    redaction_plan: dict[str, tuple[str, str | None]] = {}  # col → (strategy, pii_class)

    for col in snapshot.schema:
        pii_class = col.pii_class
        strategy = pol.strategy_for(col.name, pii_class)

        # Columns without PII AND without operator override stay untouched.
        if pii_class is None and not (pol.column_overrides and col.name in pol.column_overrides):
            continue

        if strategy == "drop":
            drop_cols.add(col.name)
        elif strategy == "aggregate_only":
            aggregate_cols.add(col.name)
        else:
            redaction_plan[col.name] = (strategy, pii_class)

    # Apply ``drop`` — remove from schema + stats + every sample row.
    if drop_cols:
        snapshot.schema = [c for c in snapshot.schema if c.name not in drop_cols]
        snapshot.stats = {k: v for k, v in snapshot.stats.items() if k not in drop_cols}
        for row in snapshot.sample:
            for col in list(row.keys()):
                if col in drop_cols:
                    del row[col]

    # Apply ``aggregate_only`` — remove from sample but keep schema/stats.
    if aggregate_cols:
        for row in snapshot.sample:
            for col in list(row.keys()):
                if col in aggregate_cols:
                    del row[col]

    # Apply value-level redactions to remaining sample columns.
    for row in snapshot.sample:
        for col_name, (strategy, pii_class) in redaction_plan.items():
            if col_name not in row:
                continue
            row[col_name] = _redact_one(
                row[col_name], strategy, pii_class, seed=seed,
            )

    # Also redact the ``top`` values in stats — those are sampled
    # column values and leak the same PII.
    for col_name, (strategy, pii_class) in redaction_plan.items():
        if col_name not in snapshot.stats:
            continue
        st = snapshot.stats[col_name]
        if st.top:
            st.top = [
                _redact_one(t, strategy, pii_class, seed=seed)
                for t in st.top
            ]

    return snapshot


def _redact_one(
    value:     Any,
    strategy:  str,
    pii_class: str | None,
    *,
    seed:      str | None,
) -> Any:
    """Apply *strategy* to a single value."""
    cls = pii_class or "opaque_id"  # fallback tag when class is unset

    if strategy == "redact":
        return redact(value, cls)
    if strategy == "mask_partial":
        return mask_partial(value, cls)
    if strategy == "hash":
        return hash_value(value)
    if strategy == "pseudonymize":
        if not seed:
            # Phase 12.6 emits an audit event for this; today we fall
            # back to plain redact.
            return redact(value, cls)
        return pseudonymize(value, cls, seed=seed)
    if strategy in {"drop", "aggregate_only"}:
        # Should never reach here — those are handled at the column level.
        raise RedactionError(
            f"{strategy} applied at value level (should be column-level)"
        )
    raise RedactionError(f"unknown strategy: {strategy}")
