"""Unit tests for learning loop schema and validation (ADR-0906 Phase 1)."""
import pytest
from datetime import datetime

from core.plugins.schema.learning_loop_schema import (
    LearningLoopManifest,
    LearningLoopIndexEntry,
    LearningLoopsManifestResponse,
    FeedbackTypeEnum,
    AggregationTypeEnum,
)
from core.plugins.validators.learning_loop_validator import (
    LearningLoopValidationError,
    validate_learning_loop_manifest,
    validate_learning_loops_list,
    extract_learning_loops_from_manifest,
    validate_loop_id_uniqueness,
    validate_loop_cross_plugin,
    sanitize_loop_for_storage,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SCHEMA TESTS (LearningLoopManifest validation)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestLearningLoopManifestValidation:
    """Test LearningLoopManifest Pydantic model."""

    def test_valid_manifest_minimal(self):
        """Test a minimal valid manifest."""
        manifest = LearningLoopManifest(
            id="test-loop",
            description="Test learning loop with minimum required fields",
            event_source="TestEvent.value",
        )
        assert manifest.id == "test-loop"
        assert manifest.aggregation == "rolling_mean_7d"  # default
        assert manifest.dormancy_alert_hours == 24  # default
        assert manifest.health_threshold is None

    def test_valid_manifest_full(self):
        """Test a fully populated manifest."""
        manifest = LearningLoopManifest(
            id="confidence_routing",
            description="Confidence score predictor for task routing",
            event_source="SkillExecutedEvent.confidence_score",
            feedback_types=["outcome_feedback", "preference_feedback"],
            aggregation="rolling_mean_7d",
            health_threshold=0.5,
            dormancy_alert_hours=24,
            owner_skill="os.delegation_router",
        )
        assert manifest.id == "confidence_routing"
        assert manifest.health_threshold == 0.5
        assert "outcome_feedback" in manifest.feedback_types

    def test_invalid_id_uppercase(self):
        """Test that uppercase in id is rejected."""
        with pytest.raises(ValueError, match="kebab-case or snake_case"):
            LearningLoopManifest(
                id="TestLoop",  # Invalid: contains uppercase
                description="Invalid uppercase in id",
                event_source="Event",
            )

    def test_invalid_id_special_chars(self):
        """Test that special characters in id are rejected."""
        with pytest.raises(ValueError, match="kebab-case or snake_case"):
            LearningLoopManifest(
                id="test@loop!",  # Invalid: special chars
                description="Invalid special chars in id",
                event_source="Event",
            )

    def test_invalid_id_starts_with_dash(self):
        """Test that id starting with dash is rejected."""
        with pytest.raises(ValueError, match="start with letter or digit"):
            LearningLoopManifest(
                id="-test-loop",
                description="Invalid dash at start",
                event_source="Event",
            )

    def test_invalid_id_ends_with_dash(self):
        """Test that id ending with dash is rejected."""
        with pytest.raises(ValueError, match="end with letter or digit"):
            LearningLoopManifest(
                id="test-loop-",
                description="Invalid dash at end",
                event_source="Event",
            )

    def test_valid_id_kebab_case(self):
        """Test valid kebab-case id."""
        manifest = LearningLoopManifest(
            id="my-test-loop-123",
            description="Valid kebab-case id",
            event_source="Event",
        )
        assert manifest.id == "my-test-loop-123"

    def test_valid_id_snake_case(self):
        """Test valid snake_case id."""
        manifest = LearningLoopManifest(
            id="my_test_loop_123",
            description="Valid snake_case id",
            event_source="Event",
        )
        assert manifest.id == "my_test_loop_123"

    def test_invalid_id_empty(self):
        """Test that empty id is rejected."""
        with pytest.raises(ValueError):
            LearningLoopManifest(
                id="",
                description="Empty id",
                event_source="Event",
            )

    def test_invalid_id_too_long(self):
        """Test that id exceeding 64 chars is rejected."""
        with pytest.raises(ValueError, match="at most 64"):
            LearningLoopManifest(
                id="a" * 65,
                description="Id too long",
                event_source="Event",
            )

    def test_invalid_description_too_short(self):
        """Test that description < 10 chars is rejected."""
        with pytest.raises(ValueError, match="at least 10"):
            LearningLoopManifest(
                id="test",
                description="short",
                event_source="Event",
            )

    def test_invalid_feedback_type_unknown(self):
        """Test that unknown feedback type is rejected."""
        with pytest.raises(ValueError, match="Unknown feedback_type"):
            LearningLoopManifest(
                id="test",
                description="Valid description for test",
                event_source="Event",
                feedback_types=["invalid_feedback_type"],
            )

    def test_valid_feedback_types_all(self):
        """Test all valid feedback types."""
        manifest = LearningLoopManifest(
            id="test",
            description="Test with all feedback types",
            event_source="Event",
            feedback_types=[
                "outcome_feedback",
                "preference_feedback",
                "confidence_score",
                "metric_observed",
            ],
        )
        assert len(manifest.feedback_types) == 4

    def test_invalid_aggregation_unknown(self):
        """Test that unknown aggregation is rejected."""
        with pytest.raises(ValueError, match="Unknown aggregation"):
            LearningLoopManifest(
                id="test",
                description="Valid description for test",
                event_source="Event",
                aggregation="invalid_aggregation",
            )

    def test_valid_aggregation_types(self):
        """Test all valid aggregation types."""
        valid_aggs = [
            "rolling_mean_7d",
            "rolling_mean_30d",
            "percentile_p50",
            "percentile_p95",
            "percentile_p99",
            "count_events_7d",
            "count_events_30d",
        ]
        for agg in valid_aggs:
            manifest = LearningLoopManifest(
                id="test",
                description="Test aggregation type validation",
                event_source="Event",
                aggregation=agg,
            )
            assert manifest.aggregation == agg

    def test_invalid_health_threshold_below_zero(self):
        """Test that health_threshold < 0 is rejected."""
        with pytest.raises(ValueError, match="greater than or equal to 0"):
            LearningLoopManifest(
                id="test",
                description="Valid description for test",
                event_source="Event",
                health_threshold=-0.1,
            )

    def test_invalid_health_threshold_above_one(self):
        """Test that health_threshold > 1 is rejected."""
        with pytest.raises(ValueError, match="less than or equal to 1"):
            LearningLoopManifest(
                id="test",
                description="Valid description for test",
                event_source="Event",
                health_threshold=1.1,
            )

    def test_valid_health_threshold_range(self):
        """Test valid health threshold range."""
        for threshold in [0.0, 0.25, 0.5, 0.75, 1.0]:
            manifest = LearningLoopManifest(
                id="test",
                description="Valid description for test",
                event_source="Event",
                health_threshold=threshold,
            )
            assert manifest.health_threshold == threshold

    def test_invalid_dormancy_alert_hours_zero(self):
        """Test that dormancy_alert_hours = 0 is rejected."""
        with pytest.raises(ValueError, match="greater than or equal to 1"):
            LearningLoopManifest(
                id="test",
                description="Valid description for test",
                event_source="Event",
                dormancy_alert_hours=0,
            )

    def test_invalid_dormancy_alert_hours_too_large(self):
        """Test that dormancy_alert_hours > 8760 (1 year) is rejected."""
        with pytest.raises(ValueError, match="8760"):
            LearningLoopManifest(
                id="test",
                description="Valid description for test",
                event_source="Event",
                dormancy_alert_hours=8761,
            )

    def test_valid_dormancy_alert_hours_range(self):
        """Test valid dormancy_alert_hours range."""
        for hours in [1, 6, 24, 72, 168, 8760]:
            manifest = LearningLoopManifest(
                id="test",
                description="Valid description for test",
                event_source="Event",
                dormancy_alert_hours=hours,
            )
            assert manifest.dormancy_alert_hours == hours


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# VALIDATOR FUNCTION TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestValidateLearningLoopManifest:
    """Test validate_learning_loop_manifest() function."""

    def test_valid_manifest(self):
        """Test validation of valid manifest dict."""
        manifest_dict = {
            "id": "test-loop",
            "description": "Valid test manifest",
            "event_source": "Event",
        }
        is_valid, error = validate_learning_loop_manifest(manifest_dict)
        assert is_valid is True
        assert error is None

    def test_invalid_manifest_missing_id(self):
        """Test validation fails when id is missing."""
        manifest_dict = {
            "description": "Missing id",
            "event_source": "Event",
        }
        is_valid, error = validate_learning_loop_manifest(manifest_dict)
        assert is_valid is False
        assert error is not None
        assert "Field required" in error or "required" in error

    def test_invalid_manifest_invalid_id(self):
        """Test validation fails with invalid id."""
        manifest_dict = {
            "id": "InvalidID",  # Uppercase
            "description": "Invalid id test",
            "event_source": "Event",
        }
        is_valid, error = validate_learning_loop_manifest(manifest_dict)
        assert is_valid is False
        assert error is not None


class TestValidateLearningLoopsList:
    """Test validate_learning_loops_list() function."""

    def test_valid_list_single(self):
        """Test validation of list with single valid loop."""
        loops = [
            {
                "id": "loop1",
                "description": "First learning loop",
                "event_source": "Event1",
            }
        ]
        valid, errors = validate_learning_loops_list(loops)
        assert len(valid) == 1
        assert len(errors) == 0
        assert valid[0].id == "loop1"

    def test_valid_list_multiple(self):
        """Test validation of list with multiple valid loops."""
        loops = [
            {
                "id": "loop1",
                "description": "First learning loop",
                "event_source": "Event1",
            },
            {
                "id": "loop2",
                "description": "Second learning loop",
                "event_source": "Event2",
            },
        ]
        valid, errors = validate_learning_loops_list(loops)
        assert len(valid) == 2
        assert len(errors) == 0

    def test_mixed_valid_invalid(self):
        """Test validation with mix of valid and invalid loops."""
        loops = [
            {
                "id": "valid-loop",
                "description": "Valid learning loop",
                "event_source": "Event",
            },
            {
                "id": "InvalidID",  # Invalid: uppercase
                "description": "Invalid learning loop",
                "event_source": "Event",
            },
            {
                "id": "another-valid",
                "description": "Another valid loop",
                "event_source": "Event",
            },
        ]
        valid, errors = validate_learning_loops_list(loops)
        assert len(valid) == 2  # Only the valid ones
        assert len(errors) == 1  # One error for the invalid one

    def test_empty_list(self):
        """Test validation of empty list."""
        loops = []
        valid, errors = validate_learning_loops_list(loops)
        assert len(valid) == 0
        assert len(errors) == 0


class TestExtractLearningLoopsFromManifest:
    """Test extract_learning_loops_from_manifest() function."""

    def test_missing_learning_loops_section(self):
        """Test that missing learning_loops section is OK (backward compat)."""
        manifest = {
            "id": "test-plugin",
            "name": "Test Plugin",
            "version": "1.0.0",
        }
        loops, warnings = extract_learning_loops_from_manifest(manifest)
        assert len(loops) == 0
        assert len(warnings) == 0

    def test_empty_learning_loops_list(self):
        """Test that empty learning_loops list is OK."""
        manifest = {
            "id": "test-plugin",
            "learning_loops": [],
        }
        loops, warnings = extract_learning_loops_from_manifest(manifest)
        assert len(loops) == 0
        assert len(warnings) == 0

    def test_invalid_learning_loops_type(self):
        """Test that non-list learning_loops generates warning."""
        manifest = {
            "id": "test-plugin",
            "learning_loops": "not-a-list",  # Invalid: should be list
        }
        loops, warnings = extract_learning_loops_from_manifest(manifest)
        assert len(loops) == 0
        assert len(warnings) == 1

    def test_valid_learning_loops_extracted(self):
        """Test that valid loops are extracted."""
        manifest = {
            "id": "test-plugin",
            "learning_loops": [
                {
                    "id": "loop1",
                    "description": "First learning loop",
                    "event_source": "Event",
                },
                {
                    "id": "loop2",
                    "description": "Second learning loop",
                    "event_source": "Event",
                },
            ],
        }
        loops, warnings = extract_learning_loops_from_manifest(manifest)
        assert len(loops) == 2
        assert len(warnings) == 0

    def test_mixed_valid_invalid_loops(self):
        """Test extraction with mix of valid and invalid loops."""
        manifest = {
            "id": "test-plugin",
            "learning_loops": [
                {
                    "id": "valid-loop",
                    "description": "Valid learning loop",
                    "event_source": "Event",
                },
                {
                    "id": "BadID",  # Invalid: uppercase
                    "description": "Invalid loop",
                    "event_source": "Event",
                },
            ],
        }
        loops, warnings = extract_learning_loops_from_manifest(manifest)
        assert len(loops) == 1  # Only the valid one
        assert len(warnings) == 1  # Warning for the invalid one


class TestValidateLoopIdUniqueness:
    """Test validate_loop_id_uniqueness() function."""

    def test_all_unique_ids(self):
        """Test that unique ids pass validation."""
        loops = [
            LearningLoopManifest(
                id="loop1",
                description="First loop",
                event_source="Event",
            ),
            LearningLoopManifest(
                id="loop2",
                description="Second loop",
                event_source="Event",
            ),
        ]
        errors = validate_loop_id_uniqueness(loops)
        assert len(errors) == 0

    def test_duplicate_ids(self):
        """Test that duplicate ids are detected."""
        loops = [
            LearningLoopManifest(
                id="duplicate",
                description="First duplicate",
                event_source="Event",
            ),
            LearningLoopManifest(
                id="duplicate",
                description="Second duplicate",
                event_source="Event",
            ),
        ]
        errors = validate_loop_id_uniqueness(loops)
        assert len(errors) == 1
        assert "Duplicate loop id" in errors[0]


class TestValidateLoopCrossPlugin:
    """Test validate_loop_cross_plugin() function."""

    def test_no_conflicts(self):
        """Test loops with no cross-plugin conflicts."""
        loops = [
            LearningLoopManifest(
                id="loop1",
                description="Test loop",
                event_source="Event",
            ),
        ]
        warnings = validate_loop_cross_plugin("_default", "plugin1", loops)
        assert len(warnings) == 0

    def test_conflict_detection(self):
        """Test detection of conflicting loops across plugins."""
        loops = [
            LearningLoopManifest(
                id="shared-loop",
                description="Shared loop",
                event_source="Event",
            ),
        ]
        existing = {
            "_default:plugin1:shared-loop": {
                "plugin_id": "plugin1",
            },
        }
        warnings = validate_loop_cross_plugin(
            "_default", "plugin2", loops, existing
        )
        assert len(warnings) == 1
        assert "conflict" in warnings[0].lower()


class TestSanitizeLoopForStorage:
    """Test sanitize_loop_for_storage() function."""

    def test_sanitize_valid_loop(self):
        """Test that valid loop is properly sanitized."""
        loop = LearningLoopManifest(
            id="test-loop",
            description="Test loop for sanitization",
            event_source="Event",
            health_threshold=0.5,
        )
        data = sanitize_loop_for_storage(loop)
        assert data["id"] == "test-loop"
        assert data["health_threshold"] == 0.5
        assert isinstance(data, dict)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# INDEX ENTRY TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestLearningLoopIndexEntry:
    """Test LearningLoopIndexEntry model."""

    def test_valid_index_entry(self):
        """Test creation of valid index entry."""
        entry = LearningLoopIndexEntry(
            tenant_id="_default",
            plugin_id="test-plugin",
            loop_id="test-loop",
            description="Test loop",
            event_source="Event",
            feedback_types=["outcome_feedback"],
            aggregation="rolling_mean_7d",
            health_score=0.75,
            status="active",
        )
        assert entry.tenant_id == "_default"
        assert entry.status == "active"
        assert entry.health_score == 0.75

    def test_index_entry_defaults(self):
        """Test default values for index entry."""
        entry = LearningLoopIndexEntry(
            tenant_id="_default",
            plugin_id="test-plugin",
            loop_id="test-loop",
            description="Test loop",
            event_source="Event",
            feedback_types=[],
            aggregation="rolling_mean_7d",
        )
        assert entry.event_count_7d == 0
        assert entry.health_score == 0.0
        assert entry.status == "active"
        assert entry.created_at is not None
        assert entry.updated_at is not None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MANIFEST RESPONSE TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestLearningLoopsManifestResponse:
    """Test LearningLoopsManifestResponse model."""

    def test_empty_manifest_response(self):
        """Test response with no learning loops."""
        response = LearningLoopsManifestResponse()
        assert response.manifest_version == "1.0"
        assert len(response.learning_loops) == 0

    def test_response_with_loops(self):
        """Test response with learning loops."""
        response = LearningLoopsManifestResponse(
            learning_loops=[
                {
                    "loop_id": "plugin:loop1",
                    "plugin_id": "plugin",
                    "description": "Test loop",
                    "event_source": "Event",
                    "feedback_types": ["outcome_feedback"],
                    "aggregation": "rolling_mean_7d",
                    "health_threshold": 0.5,
                    "dormancy_alert_hours": 24,
                    "owner_skill": "test-skill",
                    "event_count_7d": 10,
                    "health_score": 0.8,
                    "status": "active",
                }
            ]
        )
        assert len(response.learning_loops) == 1
        assert response.learning_loops[0]["loop_id"] == "plugin:loop1"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUM TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestEnums:
    """Test FeedbackTypeEnum and AggregationTypeEnum."""

    def test_feedback_type_enum_values(self):
        """Test FeedbackTypeEnum has expected values."""
        assert FeedbackTypeEnum.outcome_feedback.value == "outcome_feedback"
        assert FeedbackTypeEnum.preference_feedback.value == "preference_feedback"
        assert FeedbackTypeEnum.confidence_score.value == "confidence_score"
        assert FeedbackTypeEnum.metric_observed.value == "metric_observed"

    def test_aggregation_type_enum_values(self):
        """Test AggregationTypeEnum has expected values."""
        assert AggregationTypeEnum.rolling_mean_7d.value == "rolling_mean_7d"
        assert AggregationTypeEnum.rolling_mean_30d.value == "rolling_mean_30d"
        assert AggregationTypeEnum.percentile_p95.value == "percentile_p95"
        assert AggregationTypeEnum.count_events_7d.value == "count_events_7d"
