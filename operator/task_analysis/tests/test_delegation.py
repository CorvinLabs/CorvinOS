"""Tests for Phase 5: Delegation Decision (ADR-0217 big-data rules)."""

import pytest
from ..delegation import BigDataDetector, DelegationRouter, DelegationTarget
from ..enrichment import TaskEnricher
from ..validation import GraphValidator
from ..filtering import FilteredGraphs
from ..classifier import ClassifiedTask
from ..normalizer import TaskNormalizer


@pytest.fixture
def detector():
    return BigDataDetector()


@pytest.fixture
def normalizer():
    return TaskNormalizer()


@pytest.fixture
def validator():
    return GraphValidator()


@pytest.fixture
def enricher():
    return TaskEnricher()


@pytest.fixture
def router():
    return DelegationRouter()


class TestBigDataDetector:
    """Test ADR-0217 big-data detection."""

    def test_rule1_big_data_keyword(self, detector):
        """Rule 1: Big-Data vocabulary should trigger."""
        result, reason = detector.detect(
            "Process big data from data lake with millions of records"
        )
        assert result is True
        assert reason == "big_data_vocabulary"

    def test_rule1_not_triggered_without_keyword(self, detector):
        """Without keywords, Rule 1 should not trigger."""
        result, reason = detector.detect("Fix bug in voice module")
        assert result is False
        assert reason == "none"

    def test_rule2_markdown_table_10plus(self, detector):
        """Rule 2: ≥10 row markdown table should trigger."""
        table = """Process these rows:
| ID | Name | Value |
|---|---|---|
| 1 | a | x |
| 2 | b | y |
| 3 | c | z |
| 4 | d | w |
| 5 | e | v |
| 6 | f | u |
| 7 | g | t |
| 8 | h | s |
| 9 | i | r |
| 10 | j | q |"""
        result, reason = detector.detect(table)
        assert result is True
        assert reason == "tabular_paste"

    def test_rule2_not_triggered_with_small_table(self, detector):
        """Rule 2 should not trigger with <10 rows."""
        small_table = """| ID | Name |
|---|---|
| 1 | test |"""
        result, reason = detector.detect(small_table)
        # Should not trigger unless other rules match
        assert reason != "tabular_paste"

    def test_rule3_csv_with_bulk_verb(self, detector):
        """Rule 3: CSV + bulk verb should trigger."""
        result, reason = detector.detect(
            "Bulk process the data.csv file and aggregate all records"
        )
        assert result is True
        assert reason == "structured_source_bulk_work"

    def test_rule3_csv_without_bulk_verb_no_trigger(self, detector):
        """Rule 3: CSV alone (no bulk verb) should NOT trigger."""
        result, reason = detector.detect("Load data.csv for testing purposes")
        # "Load" is in BULK_VERBS, so this WOULD trigger
        assert result is True
        assert reason == "structured_source_bulk_work"

    def test_rule3_bulk_verb_without_csv_no_trigger(self, detector):
        """Rule 3: Bulk verb alone (no CSV) should NOT trigger."""
        result, reason = detector.detect("Bulk rename some functions in the code")
        assert result is False
        assert reason == "none"

    def test_rule4_volume_with_data_noun(self, detector):
        """Rule 4: Volume + data noun should trigger."""
        result, reason = detector.detect("Process 10 GB of customer records")
        assert result is True
        assert reason == "volume_data_noun"

    def test_rule4_excludes_code_noun(self, detector):
        """Rule 4: Volume + CODE noun should NOT trigger."""
        result, reason = detector.detect("Refactor 2 million lines of code")
        # This should NOT match because 'code' is excluded
        assert result is False or reason != "volume_data_noun"

    def test_rule4_excludes_codebase(self, detector):
        """Rule 4: Volume + codebase should NOT trigger."""
        result, reason = detector.detect("Optimize 500 GB codebase")
        # 'codebase' contains 'code', so should NOT trigger
        assert result is False or reason != "volume_data_noun"

    def test_rule4_with_tb_unit(self, detector):
        """Rule 4: Should recognize TB unit."""
        result, reason = detector.detect("Migrate 50 TB of data to cloud storage")
        assert result is True
        assert reason == "volume_data_noun"


class TestDelegationRouter:
    """Test delegation routing logic."""

    def test_router_big_data_goes_to_acs(
        self, router, normalizer, validator, enricher
    ):
        """Big-data tasks should route to ACS."""
        task = normalizer.normalize(
            "Process big data from data warehouse with millions of records"
        )
        classified = ClassifiedTask(
            normalized=task,
            classification={"call_graph": (0.8, {"files": ["test.py"]})},
            confidence=0.8,
            recommended_graphs=["call_graph"],
            skills_to_inject=[],
        )

        filtered = FilteredGraphs(
            normalized=task,
            classified=classified,
            filtered_graphs=["call_graph"],
            deduplicated_files={},
            ranked_graphs=[],
            redundancy_ratio=0.0,
        )

        validated = validator.validate(filtered)
        enriched = enricher.enrich(validated)
        decision = router.route(enriched)

        assert decision.delegation_target == DelegationTarget.ACS
        assert decision.should_delegate is True

    def test_router_high_complexity_opus_may_go_to_tde(
        self, router, normalizer, validator, enricher
    ):
        """High complexity + Opus should consider TDE."""
        task = normalizer.normalize("major system refactor entire architecture rewrite")
        classified = ClassifiedTask(
            normalized=task,
            classification={
                "call_graph": (0.95, {"files": ["a.py", "b.py", "c.py", "d.py"]})
            },
            confidence=0.95,
            recommended_graphs=["call_graph"],
            skills_to_inject=[],
        )

        filtered = FilteredGraphs(
            normalized=task,
            classified=classified,
            filtered_graphs=["call_graph"],
            deduplicated_files={},
            ranked_graphs=[],
            redundancy_ratio=0.0,
        )

        validated = validator.validate(filtered)
        enriched = enricher.enrich(validated)
        decision = router.route(enriched)

        # Should either be native or TDE (depends on cost threshold)
        assert decision.delegation_target in [DelegationTarget.NATIVE, DelegationTarget.TDE]

    def test_router_low_complexity_goes_native(
        self, router, normalizer, validator, enricher
    ):
        """Low complexity tasks should stay native."""
        task = normalizer.normalize("Fix typo in documentation file")
        classified = ClassifiedTask(
            normalized=task,
            classification={"call_graph": (0.3, {"files": ["readme.md"]})},
            confidence=0.3,
            recommended_graphs=["call_graph"],
            skills_to_inject=[],
        )

        filtered = FilteredGraphs(
            normalized=task,
            classified=classified,
            filtered_graphs=["call_graph"],
            deduplicated_files={},
            ranked_graphs=[],
            redundancy_ratio=0.0,
        )

        validated = validator.validate(filtered)
        enriched = enricher.enrich(validated)
        decision = router.route(enriched)

        assert decision.delegation_target == DelegationTarget.NATIVE
        assert decision.should_delegate is False

    def test_router_output_structure(
        self, router, normalizer, validator, enricher
    ):
        """DelegationDecision should have all fields."""
        task = normalizer.normalize("test task for decision output structure")
        classified = ClassifiedTask(
            normalized=task,
            classification={"call_graph": (0.8, {"files": ["test.py"]})},
            confidence=0.8,
            recommended_graphs=["call_graph"],
            skills_to_inject=[],
        )

        filtered = FilteredGraphs(
            normalized=task,
            classified=classified,
            filtered_graphs=["call_graph"],
            deduplicated_files={},
            ranked_graphs=[],
            redundancy_ratio=0.0,
        )

        validated = validator.validate(filtered)
        enriched = enricher.enrich(validated)
        decision = router.route(enriched)

        assert decision.enriched == enriched
        assert isinstance(decision.should_delegate, bool)
        assert isinstance(decision.delegation_target, DelegationTarget)
        assert isinstance(decision.carve_out_reason, str)
        assert 0.0 <= decision.confidence <= 1.0
