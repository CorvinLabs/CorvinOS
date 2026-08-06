"""Phase 5: Delegation Decision & Routing (ADR-0267, ADR-0217).

Apply ADR-0217 Big-Data carve-out rules to decide: native vs ACS vs TDE.

ADR-0217 Big-Data Rules (in priority order):
1. Big-Data Vocabulary: "Big Data", "data lake", "warehouse", etc.
2. Tabular Paste: Markdown table ≥10 rows
3. Structured Source + Bulk Work Verb: CSV/DB file + (bulk|aggregate|analyze|process)
4. Volume + Data Noun: GB/TB/PB + (data|records|rows|entries) — NOT code
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from .enrichment import EnrichedTask


class DelegationTarget(Enum):
    """Where to send the task."""

    NATIVE = "native"
    """Direct OS-turn (no delegation)."""

    ACS = "acs"
    """Autonomous Compute Shell (big-data, structured work)."""

    TDE = "tde"
    """Tiered Delegation Engine (complex, expensive tasks)."""


@dataclass
class DelegationDecision:
    """Output of Phase 5 delegation routing."""

    enriched: EnrichedTask
    """Original enriched task from Phase 4."""

    should_delegate: bool
    """True if not native."""

    delegation_target: DelegationTarget
    """native | acs | tde."""

    carve_out_reason: str
    """Which ADR-0217 rule fired, or 'none'."""

    confidence: float
    """Confidence in delegation decision (0.0–1.0)."""


class BigDataDetector:
    """Detect if task matches ADR-0217 big-data carve-out rules."""

    # Rule 1: Big-Data Vocabulary (keywords)
    BIG_DATA_KEYWORDS = [
        "big data",
        "data lake",
        "data warehouse",
        "warehouse",
        "datenmengen",
        "datenmenge",
        "daten",  # German: data
        "menge",  # German: quantity/set
    ]

    # Rule 3: Structured sources (file types)
    STRUCTURED_SOURCES = [".csv", ".xlsx", ".xls", ".db", ".sql", ".parquet"]

    # Rule 3: Bulk work verbs
    BULK_VERBS = [
        "bulk",
        "batch",
        "aggregate",
        "analyze",
        "process",
        "transform",
        "migrate",
        "import",
        "export",
        "load",
        "extract",
    ]

    # Rule 4: Data nouns (exclude code)
    DATA_NOUNS = [
        "data",
        "records",
        "rows",
        "entries",
        "items",
        "dataset",
        "table",
        "columns",
        "fields",
        "cells",
    ]

    # Rule 4: Volume units
    VOLUME_UNITS = [
        r"\d+\s*gb",
        r"\d+\s*tb",
        r"\d+\s*pb",
        r"\d+\s*gigabyte",
        r"\d+\s*terabyte",
        r"\d+\s*million",
        r"\d+\s*millionen",  # German
    ]

    def detect(self, task_description: str) -> tuple[bool, str]:
        """Detect if task is big-data (per ADR-0217).

        Args:
            task_description: Task description from normalizer.

        Returns:
            (is_big_data, reason_string).
        """
        desc_lower = task_description.lower()

        # Rule 1: Big-Data Vocabulary
        if self._has_big_data_keyword(desc_lower):
            return True, "big_data_vocabulary"

        # Rule 2: Tabular Paste (≥10 rows)
        if self._has_markdown_table_10plus(task_description):
            return True, "tabular_paste"

        # Rule 3: Structured Source + Bulk Verb (must both be present)
        if (
            self._has_structured_source(task_description)
            and self._has_bulk_verb(desc_lower)
        ):
            return True, "structured_source_bulk_work"

        # Rule 4: Volume + Data Noun (exclude code)
        if self._has_volume_with_data_noun(desc_lower):
            return True, "volume_data_noun"

        return False, "none"

    def _has_big_data_keyword(self, desc_lower: str) -> bool:
        """Check Rule 1: Big-Data Vocabulary."""
        return any(kw in desc_lower for kw in self.BIG_DATA_KEYWORDS)

    def _has_markdown_table_10plus(self, task_description: str) -> bool:
        """Check Rule 2: Markdown table with ≥10 data rows.

        Detects markdown tables via:
        1. Lines starting/ending with | (table row markers)
        2. Separator line with --- and | (table header separator)
        3. Counts actual data rows (not header/separator)
        """
        lines = task_description.split("\n")

        # Find potential table region (consecutive pipe-delimited lines)
        in_table = False
        table_data_rows = 0
        separator_found = False

        for line in lines:
            stripped = line.strip()

            # Skip empty lines
            if not stripped:
                in_table = False
                continue

            # Check if line is a markdown table row (starts and ends with |)
            if stripped.startswith("|") and stripped.endswith("|"):
                # Check if this is the separator line (contains dashes and pipes)
                if re.search(r'\|\s*-+\s*\|', stripped):
                    separator_found = True
                elif separator_found:
                    # Data row after separator
                    table_data_rows += 1
                    in_table = True
                elif in_table or not separator_found:
                    # Potential header or first data rows
                    in_table = True
            else:
                # Line doesn't look like markdown table
                if in_table:
                    in_table = False

        # Rule 2 matches if: separator found AND ≥10 data rows
        return separator_found and table_data_rows >= 10

    def _has_structured_source(self, task_description: str) -> bool:
        """Check Rule 3 (part 1): CSV/DB/etc. file reference."""
        desc_lower = task_description.lower()
        return any(
            ext in desc_lower for ext in self.STRUCTURED_SOURCES
        )

    def _has_bulk_verb(self, desc_lower: str) -> bool:
        """Check Rule 3 (part 2): Bulk work verb."""
        return any(verb in desc_lower for verb in self.BULK_VERBS)

    def _has_volume_with_data_noun(self, desc_lower: str) -> bool:
        """Check Rule 4: Volume + Data Noun (excluding code).

        Examples that MATCH:
        - "10 GB of customer records"
        - "process 1 million rows from database"
        - "code-generated CSV with 1 million records" (CSV forces big-data even with 'code')

        Examples that DON'T MATCH:
        - "2 million lines of code" (code, NOT data)
        - "10 TB codebase" (code, NOT data)
        """
        # Check for volume unit
        has_volume = any(
            re.search(pattern, desc_lower) for pattern in self.VOLUME_UNITS
        )
        if not has_volume:
            return False

        # Check for data noun (but NOT pure code) — use word boundaries to avoid false matches
        # E.g., "database" should not match "data" noun
        has_data_noun = any(
            re.search(rf'\b{re.escape(noun)}\b', desc_lower) for noun in self.DATA_NOUNS
        )
        has_pure_code = any(
            re.search(rf'\b{re.escape(kw)}\b', desc_lower)
            for kw in ["code", "lines", "function", "class", "method"]
        )

        # CSV/DB reference over-rides code noun (code-generated data is still BIG_DATA)
        has_structured_source = self._has_structured_source(desc_lower)

        # Rule 4 matches if:
        # (data noun + no pure code) OR (data noun + structured source exists)
        return has_data_noun and (not has_pure_code or has_structured_source)


class DelegationRouter:
    """Route enriched task to native, ACS, or TDE."""

    def __init__(self):
        """Initialize router."""
        self.big_data_detector = BigDataDetector()

    def route(self, enriched: EnrichedTask) -> DelegationDecision:
        """Route task to delegation target.

        Logic:
        1. If big-data → ACS
        2. Else if complex + expensive → TDE (if available)
        3. Else → native (default)

        Args:
            enriched: EnrichedTask from Phase 4.

        Returns:
            DelegationDecision.
        """
        # Safely traverse nested attributes
        try:
            normalized = enriched.validated.filtered.classified.normalized
        except (AttributeError, TypeError) as e:
            raise ValueError(
                f"Invalid enriched task structure: cannot access normalized task. {e}"
            ) from e

        # Check both summary and description for big-data keywords
        task_description = (
            getattr(normalized, "description", "")
            or getattr(normalized, "summary", "")
        )

        # Check big-data carve-out (ADR-0217)
        is_big_data, carve_out_reason = self.big_data_detector.detect(task_description)

        if is_big_data:
            target = DelegationTarget.ACS
            confidence = 0.9
        elif (
            enriched.task_complexity >= 0.7
            and enriched.model_recommendation == "opus"
            and enriched.estimated_cost_usd > 0.10
        ):
            # High complexity + expensive → TDE (if available)
            target = DelegationTarget.TDE
            confidence = 0.7
            if carve_out_reason == "none":
                carve_out_reason = "high_complexity_opus"
        else:
            # Default: native
            target = DelegationTarget.NATIVE
            confidence = 0.95
            carve_out_reason = "none"

        return DelegationDecision(
            enriched=enriched,
            should_delegate=(target != DelegationTarget.NATIVE),
            delegation_target=target,
            carve_out_reason=carve_out_reason,
            confidence=confidence,
        )
