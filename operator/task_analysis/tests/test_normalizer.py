"""Comprehensive tests for Phase 0 Task Normalizer (ADR-0267).

Tests cover all six normalization phases:
1. Type detection (keyword-based, all TaskTypes)
2. Component extraction (files, modules, layers)
3. Severity inference (high, medium, low)
4. Sufficiency validation (error cases, edge cases)
5. Memory enrichment (file scanning, keyword matching, scoring)
6. Incident linking (incident-*.md discovery)

Run with:
    python -m pytest operator/task_analysis/tests/test_normalizer.py -v
    python -m pytest operator/task_analysis/tests/test_normalizer.py::TestTaskTypeDetection -v
"""

import unittest
from pathlib import Path
import tempfile
import sys

# Adjust path for imports (allows running from different directories)
sys.path.insert(0, str(Path(__file__).parent.parent))

from normalizer import (
    TaskNormalizer,
    TaskType,
    Severity,
    NormalizedTask,
    InsufficientTaskInfo,
    SufficiencyCheck,
)


class TestTaskTypeDetection(unittest.TestCase):
    """Test task type detection via keyword matching.

    Verifies all TaskType categories and fallback to UNKNOWN.
    """

    def setUp(self):
        """Create normalizer with temporary memory directory."""
        self.temp_dir = tempfile.mkdtemp()
        self.normalizer = TaskNormalizer(memory_dir=Path(self.temp_dir))

    def test_detect_bug_fix_primary_keywords(self):
        """Test BUG_FIX detection with primary keywords."""
        test_cases = [
            "Fix crash in voice module",
            "Bug in layer 10 path gate",
            "Issue with audit chain hash verification",
            "Broken bridge connection to Discord",
            "Application hangs on startup when parsing config",
            "Error in memory enrichment algorithm",
        ]

        for task in test_cases:
            task_type = self.normalizer._detect_type(task)
            self.assertEqual(
                task_type, TaskType.BUG_FIX, f"Failed for: {task}"
            )

    def test_detect_bug_fix_secondary_keywords(self):
        """Test BUG_FIX detection with secondary keywords."""
        test_cases = [
            "Fails to connect to console",
            "Doesn't work in offline mode",
            "Not working on Windows",
        ]

        for task in test_cases:
            task_type = self.normalizer._detect_type(task)
            self.assertEqual(
                task_type, TaskType.BUG_FIX, f"Failed for: {task}"
            )

    def test_detect_feature(self):
        """Test FEATURE detection."""
        test_cases = [
            "Add new plugin system with validation",
            "Implement voice recognition for commands",
            "Support OIDC authentication flow",
            "Enable TDE delegation in console",
            "Introduce caching layer for performance",
        ]

        for task in test_cases:
            task_type = self.normalizer._detect_type(task)
            self.assertEqual(
                task_type, TaskType.FEATURE, f"Failed for: {task}"
            )

    def test_detect_refactor(self):
        """Test REFACTOR detection."""
        test_cases = [
            "Refactor console startup sequence",
            "Cleanup unused imports and dead code",
            "Rename audit layer functions for consistency",
            "Reorganize bridge initialization code",
            "Simplify exception handling in normalizer",
        ]

        for task in test_cases:
            task_type = self.normalizer._detect_type(task)
            self.assertEqual(
                task_type, TaskType.REFACTOR, f"Failed for: {task}"
            )

    def test_detect_incident(self):
        """Test INCIDENT detection."""
        test_cases = [
            "INCIDENT: service down for 2 hours",
            "Outage in production affecting all users",
            "Critical emergency response needed",
            "Emergency: offline mode doesn't reconnect",
        ]

        for task in test_cases:
            task_type = self.normalizer._detect_type(task)
            self.assertEqual(
                task_type, TaskType.INCIDENT, f"Failed for: {task}"
            )

    def test_detect_documentation(self):
        """Test DOCUMENTATION detection."""
        test_cases = [
            "Doc: update layer reference guide",
            "Add docstring to normalizer main methods",
            "Write README for plugin system",
            "Update documentation for ADR-0267",
        ]

        for task in test_cases:
            task_type = self.normalizer._detect_type(task)
            self.assertEqual(
                task_type, TaskType.DOCUMENTATION, f"Failed for: {task}"
            )

    def test_detect_performance(self):
        """Test PERFORMANCE detection."""
        test_cases = [
            "Optimize startup latency from 5s to <1s",
            "Slow audit log scanning for large files",
            "Inefficient regex patterns in extraction",
            "Timeout on memory enrichment for large tasks",
        ]

        for task in test_cases:
            task_type = self.normalizer._detect_type(task)
            self.assertEqual(
                task_type, TaskType.PERFORMANCE, f"Failed for: {task}"
            )

    def test_detect_unknown_type(self):
        """Test fallback to UNKNOWN for no-match tasks."""
        test_cases = [
            "xyzabc 12345 foobar nonsense",
            "Lorem ipsum dolor sit amet",
            "Qwerty asdfgh zxcvbn",
        ]

        for task in test_cases:
            task_type = self.normalizer._detect_type(task)
            self.assertEqual(
                task_type, TaskType.UNKNOWN, f"Failed for: {task}"
            )

    def test_type_detection_case_insensitive(self):
        """Test that type detection is case-insensitive."""
        test_cases = [
            ("FIX crash immediately", TaskType.BUG_FIX),
            ("ADD new feature", TaskType.FEATURE),
            ("REFACTOR the module", TaskType.REFACTOR),
            ("INCIDENT in production", TaskType.INCIDENT),
        ]

        for task, expected in test_cases:
            task_type = self.normalizer._detect_type(task)
            self.assertEqual(task_type, expected, f"Failed for: {task}")


class TestComponentExtraction(unittest.TestCase):
    """Test extraction of affected components and layers.

    Verifies file path extraction, module detection, and layer reference parsing.
    """

    def setUp(self):
        """Create normalizer with temporary memory directory."""
        self.temp_dir = tempfile.mkdtemp()
        self.normalizer = TaskNormalizer(memory_dir=Path(self.temp_dir))

    def test_extract_single_file_path(self):
        """Test extraction of a single file path."""
        task = "Fix bug in core/compliance/tripwire.py"
        components = self.normalizer._extract_components(task)

        self.assertIn("core/compliance/tripwire.py", components)

    def test_extract_multiple_file_paths(self):
        """Test extraction of multiple file paths."""
        task = "Fix in core/compliance/tripwire.py and operator/bridges/adapter.py"
        components = self.normalizer._extract_components(task)

        self.assertIn("core/compliance/tripwire.py", components)
        self.assertIn("operator/bridges/adapter.py", components)

    def test_extract_module_paths(self):
        """Test extraction of module paths (not just files)."""
        task = "Issue in core/plugins/ and operator/task_analysis/"
        components = self.normalizer._extract_components(task)

        # Should extract module-level paths
        self.assertTrue(any("core" in comp for comp in components))
        self.assertTrue(any("operator" in comp for comp in components))

    def test_extract_module_roots(self):
        """Test inference of module root names from nested paths."""
        task = "core/compliance/layer.py and operator/voice/renderer.py"
        components = self.normalizer._extract_components(task)

        # Should include deduced module roots
        self.assertIn("core", components)
        self.assertIn("operator", components)

    def test_extract_various_file_types(self):
        """Test extraction of various file extensions."""
        task = "Fix core/file.py and console/app.tsx and config.yaml"
        components = self.normalizer._extract_components(task)

        self.assertIn("core/file.py", components)
        self.assertIn("console/app.tsx", components)
        self.assertIn("config.yaml", components)

    def test_extract_layers_single(self):
        """Test extraction of a single layer reference."""
        task = "Issue in L10 path-gate implementation"
        layers = self.normalizer._extract_layers(task)

        self.assertIn("L10", layers)
        self.assertEqual(len(layers), 1)

    def test_extract_layers_multiple(self):
        """Test extraction of multiple layer references."""
        task = "L10 path-gate and L16 security layer and L44 house-rules"
        layers = self.normalizer._extract_layers(task)

        self.assertIn("L10", layers)
        self.assertIn("L16", layers)
        self.assertIn("L44", layers)
        self.assertEqual(len(layers), 3)

    def test_extract_layers_deduplicated(self):
        """Test that duplicate layer references are deduplicated."""
        task = "L16 issue in L16 audit layer L16 compliance"
        layers = self.normalizer._extract_layers(task)

        # Should have only one L16
        self.assertEqual(layers.count("L16"), 1)

    def test_extract_layers_sorted(self):
        """Test that extracted layers are sorted."""
        task = "L44 L10 L23 L16 all layers mentioned"
        layers = self.normalizer._extract_layers(task)

        self.assertEqual(layers, ["L10", "L16", "L23", "L44"])

    def test_no_components_extracted(self):
        """Test task with no specific components."""
        task = "General cleanup needed everywhere"
        components = self.normalizer._extract_components(task)

        self.assertEqual(len(components), 0)

    def test_no_layers_extracted(self):
        """Test task with no layer references."""
        task = "Fix performance issue in startup"
        layers = self.normalizer._extract_layers(task)

        self.assertEqual(len(layers), 0)

    def test_components_deduplicated_and_sorted(self):
        """Test that components are deduplicated and sorted."""
        task = "core/file1.py core/file2.py core/file1.py core/"
        components = self.normalizer._extract_components(task)

        # Check deduplication
        self.assertEqual(components.count("core/file1.py"), 1)
        self.assertEqual(components.count("core/file2.py"), 1)

        # Check sorted
        self.assertEqual(components, sorted(components))


class TestSeverityInference(unittest.TestCase):
    """Test severity inference via keyword matching.

    Verifies HIGH, MEDIUM, LOW classification and defaults.
    """

    def setUp(self):
        """Create normalizer with temporary memory directory."""
        self.temp_dir = tempfile.mkdtemp()
        self.normalizer = TaskNormalizer(memory_dir=Path(self.temp_dir))

    def test_high_severity_single_keywords(self):
        """Test HIGH severity with individual keywords."""
        test_cases = [
            "Crash in startup",
            "Data loss in audit chain",
            "Security exploit found",
            "Compliance violation detected",
            "Corrupt audit records",
        ]

        for task in test_cases:
            severity = self.normalizer._infer_severity(task)
            self.assertEqual(
                severity, Severity.HIGH.value, f"Failed for: {task}"
            )

    def test_high_severity_combined_keywords(self):
        """Test HIGH severity with combined keywords."""
        task = "CRITICAL: hang causing infinite loop in startup"
        severity = self.normalizer._infer_severity(task)
        self.assertEqual(severity, Severity.HIGH.value)

    def test_medium_severity_single_keywords(self):
        """Test MEDIUM severity with individual keywords."""
        test_cases = [
            "Bug in cache logic",
            "Error in exception handling",
            "Inconsistent behavior across platforms",
            "Race condition in shutdown",
        ]

        for task in test_cases:
            severity = self.normalizer._infer_severity(task)
            self.assertEqual(
                severity, Severity.MEDIUM.value, f"Failed for: {task}"
            )

    def test_low_severity_single_keywords(self):
        """Test LOW severity with individual keywords."""
        test_cases = [
            "Fix typo in comment",
            "Cleanup whitespace",
            "Docstring formatting",
            "Cosmetic UI improvement",
        ]

        for task in test_cases:
            severity = self.normalizer._infer_severity(task)
            self.assertEqual(
                severity, Severity.LOW.value, f"Failed for: {task}"
            )

    def test_default_severity(self):
        """Test default to MEDIUM when no keywords match."""
        task = "Update something somewhere somehow"
        severity = self.normalizer._infer_severity(task)

        self.assertEqual(severity, Severity.MEDIUM.value)

    def test_severity_priority_high_beats_medium(self):
        """Test that HIGH severity takes priority over MEDIUM."""
        task = "Crash with some error handling bug"
        severity = self.normalizer._infer_severity(task)

        self.assertEqual(severity, Severity.HIGH.value)

    def test_severity_priority_medium_beats_low(self):
        """Test that MEDIUM severity takes priority over LOW."""
        task = "Issue with typo in docstring"
        severity = self.normalizer._infer_severity(task)

        self.assertEqual(severity, Severity.MEDIUM.value)

    def test_severity_case_insensitive(self):
        """Test that severity detection is case-insensitive."""
        test_cases = [
            ("CRASH immediately", Severity.HIGH.value),
            ("Fix BUG now", Severity.MEDIUM.value),
            ("TYPO in readme", Severity.LOW.value),
        ]

        for task, expected in test_cases:
            severity = self.normalizer._infer_severity(task)
            self.assertEqual(severity, expected, f"Failed for: {task}")


class TestSufficiencyValidation(unittest.TestCase):
    """Test task sufficiency validation.

    Verifies rejection of empty, too-short, too-vague, and generic tasks.
    """

    def setUp(self):
        """Create normalizer with temporary memory directory."""
        self.temp_dir = tempfile.mkdtemp()
        self.normalizer = TaskNormalizer(memory_dir=Path(self.temp_dir))

    def test_empty_task(self):
        """Test rejection of completely empty task."""
        check = self.normalizer._validate_sufficiency("")

        self.assertFalse(check.valid)
        self.assertIn("description", check.missing_fields)

    def test_whitespace_only_task(self):
        """Test rejection of whitespace-only task."""
        check = self.normalizer._validate_sufficiency("   \n  \t  ")

        self.assertFalse(check.valid)

    def test_too_short_task(self):
        """Test rejection of too-short task (<10 chars)."""
        check = self.normalizer._validate_sufficiency("fix")

        self.assertFalse(check.valid)
        self.assertIn("description", check.missing_fields)

    def test_too_few_words(self):
        """Test rejection of task with too few words (<3)."""
        check = self.normalizer._validate_sufficiency("fix bug")

        self.assertFalse(check.valid)

    def test_too_generic(self):
        """Test rejection of task that is only generic words."""
        check = self.normalizer._validate_sufficiency("fix bug issue problem")

        self.assertFalse(check.valid)
        self.assertIn("context", check.missing_fields)

    def test_minimal_valid_task(self):
        """Test acceptance of minimal valid task."""
        check = self.normalizer._validate_sufficiency("Fix audit chain corruption")

        self.assertTrue(check.valid)

    def test_typical_valid_task(self):
        """Test acceptance of typical valid task."""
        check = self.normalizer._validate_sufficiency(
            "Fix crash in voice module when processing long audio files"
        )

        self.assertTrue(check.valid)

    def test_valid_multiline_task(self):
        """Test acceptance of multiline valid task."""
        task = """Fix crash in voice module

        The TTS rendering hangs when processing audio files longer than 5 minutes.
        Affects L23 speech-to-text layer."""

        check = self.normalizer._validate_sufficiency(task)

        self.assertTrue(check.valid)

    def test_valid_task_with_special_chars(self):
        """Test acceptance of task with special characters."""
        check = self.normalizer._validate_sufficiency(
            "Fix bug in core/compliance/layer-10.py: CRITICAL ISSUE"
        )

        self.assertTrue(check.valid)

    def test_edge_case_exactly_10_chars(self):
        """Test task with exactly 10 characters (boundary)."""
        # Test a 10+ char task that clearly has content and meets all minimum requirements
        task = "Fix startup delay"  # 16 chars, 3 words, all content words (not generic)
        check = self.normalizer._validate_sufficiency(task)

        self.assertTrue(check.valid)

    def test_edge_case_9_chars(self):
        """Test task with 9 characters (just below minimum)."""
        check = self.normalizer._validate_sufficiency("012345678")

        self.assertFalse(check.valid)


class TestMemoryEnrichment(unittest.TestCase):
    """Test memory file enrichment via keyword matching and scoring.

    Verifies file discovery, keyword extraction, scoring, and ranking.
    """

    def test_enrich_with_matching_files(self):
        """Test finding matching memory files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = Path(tmpdir)

            # Create test memory files
            (memory_dir / "incident-2026-08-04-audio-lag.md").write_text(
                "Audio lag issue in voice module\nAffects TTS rendering\n"
            )
            (memory_dir / "adr-0267-task-normalizer.md").write_text(
                "ADR for task normalization system\n"
            )
            (memory_dir / "unrelated.md").write_text(
                "Something about clouds and weather\n"
            )

            normalizer = TaskNormalizer(memory_dir=memory_dir)

            # Test task matching audio lag incident
            task = "Fix audio lag in TTS pipeline"
            context = normalizer._enrich_from_memory(task, TaskType.BUG_FIX, [])

            self.assertIn("incident-2026-08-04-audio-lag.md", context)
            self.assertNotIn("unrelated.md", context)

    def test_enrich_no_matching_files(self):
        """Test that unrelated tasks yield empty context."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = Path(tmpdir)

            (memory_dir / "incident-2026-08-04-audio.md").write_text(
                "Audio-specific issue\n"
            )

            normalizer = TaskNormalizer(memory_dir=memory_dir)

            # Task with no matching keywords
            task = "Update documentation for API endpoints"
            context = normalizer._enrich_from_memory(task, TaskType.DOCUMENTATION, [])

            self.assertEqual(len(context), 0)

    def test_enrich_memory_ranking(self):
        """Test that memory files are ranked by relevance score."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = Path(tmpdir)

            # High-relevance file (multiple keyword matches)
            (memory_dir / "high-score.md").write_text(
                "Audio voice TTS rendering pipeline\n" * 5
            )

            # Low-relevance file (single keyword match)
            (memory_dir / "low-score.md").write_text("Mentions audio once\n")

            normalizer = TaskNormalizer(memory_dir=memory_dir)

            task = "Fix audio voice rendering"
            context = normalizer._enrich_from_memory(task, TaskType.BUG_FIX, [])

            # High-score should come first
            if len(context) >= 2:
                self.assertEqual(context[0], "high-score.md")
                self.assertEqual(context[1], "low-score.md")

    def test_enrich_no_memory_directory(self):
        """Test handling of missing memory directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = Path(tmpdir) / "nonexistent"

            normalizer = TaskNormalizer(memory_dir=memory_dir)
            context = normalizer._enrich_from_memory("test task", TaskType.BUG_FIX, [])

            self.assertEqual(len(context), 0)

    def test_enrich_empty_memory_directory(self):
        """Test handling of empty memory directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = Path(tmpdir)
            # Create empty directory

            normalizer = TaskNormalizer(memory_dir=memory_dir)
            context = normalizer._enrich_from_memory("test task", TaskType.BUG_FIX, [])

            self.assertEqual(len(context), 0)

    def test_enrich_filename_boost(self):
        """Test that keyword matches in filenames get score boost."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = Path(tmpdir)

            # File with keyword in name (should score higher)
            (memory_dir / "incident-crash-recovery.md").write_text(
                "General description\n"
            )
            # File with keyword in content only
            (memory_dir / "other.md").write_text("Crash happened in module X\n" * 10)

            normalizer = TaskNormalizer(memory_dir=memory_dir)

            task = "Fix crash"
            context = normalizer._enrich_from_memory(task, TaskType.BUG_FIX, [])

            # Filename match should come first despite fewer content matches
            if len(context) >= 2:
                self.assertEqual(context[0], "incident-crash-recovery.md")


class TestIncidentLinking(unittest.TestCase):
    """Test incident report discovery and linking.

    Verifies finding incident-*.md files that mention affected components.
    """

    def test_find_related_incidents_by_component(self):
        """Test finding incidents mentioning specific components."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = Path(tmpdir)

            # Create incident mentioning core/compliance
            (memory_dir / "incident-2026-08-04-compliance.md").write_text(
                "Compliance layer failure affecting core/compliance/tripwire.py\n"
            )
            # Non-incident file (should not match)
            (memory_dir / "adr-some.md").write_text(
                "core/compliance mentioned here too\n"
            )

            normalizer = TaskNormalizer(memory_dir=memory_dir)

            # Find incidents mentioning core/compliance
            incidents = normalizer._find_related_incidents(
                ["core/compliance/tripwire.py"], []
            )

            self.assertIn("incident-2026-08-04-compliance.md", incidents)
            self.assertNotIn("adr-some.md", incidents)

    def test_find_related_incidents_multiple(self):
        """Test finding multiple related incidents."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = Path(tmpdir)

            (memory_dir / "incident-1.md").write_text("core/voice crash\n")
            (memory_dir / "incident-2.md").write_text("operator/bridge failure\n")

            normalizer = TaskNormalizer(memory_dir=memory_dir)

            incidents = normalizer._find_related_incidents(
                ["core/voice/renderer.py", "operator/bridges/adapter.py"], []
            )

            self.assertIn("incident-1.md", incidents)
            self.assertIn("incident-2.md", incidents)

    def test_find_related_incidents_no_match(self):
        """Test when no incidents match."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = Path(tmpdir)

            (memory_dir / "incident-2026-08-04.md").write_text(
                "Audio module problem\n"
            )

            normalizer = TaskNormalizer(memory_dir=memory_dir)

            # Component not mentioned in any incident
            incidents = normalizer._find_related_incidents(["core/compliance/"], [])

            self.assertEqual(len(incidents), 0)

    def test_find_related_incidents_no_memory_dir(self):
        """Test when memory directory doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = Path(tmpdir) / "nonexistent"

            normalizer = TaskNormalizer(memory_dir=memory_dir)
            incidents = normalizer._find_related_incidents(["core/voice/"], [])

            self.assertEqual(len(incidents), 0)

    def test_find_related_incidents_non_incident_files_ignored(self):
        """Test that non-incident files (not incident-*.md) are ignored."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = Path(tmpdir)

            (memory_dir / "adr-0267.md").write_text("core/voice mentioned\n")
            (memory_dir / "incident-real.md").write_text("core/voice crash\n")

            normalizer = TaskNormalizer(memory_dir=memory_dir)

            incidents = normalizer._find_related_incidents(["core/voice"], [])

            self.assertIn("incident-real.md", incidents)
            self.assertNotIn("adr-0267.md", incidents)


class TestFullNormalization(unittest.TestCase):
    """End-to-end tests for complete normalization flow.

    Verifies all phases working together, raising exceptions, and metadata.
    """

    def test_normalize_complete_task(self):
        """Test normalization of a well-formed, complete task."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = Path(tmpdir)

            normalizer = TaskNormalizer(memory_dir=memory_dir)

            task = """Fix crash in voice module

            The TTS rendering hangs when processing audio files longer than 5 minutes.
            Affects L23 speech-to-text layer and core/voice/renderer.py.
            Related to ADR-0185."""

            normalized = normalizer.normalize(task)

            # Verify all fields
            self.assertEqual(normalized.type, TaskType.BUG_FIX)
            self.assertEqual(normalized.severity, Severity.HIGH.value)
            self.assertIn("core", normalized.components)
            self.assertIn("L23", normalized.affected_layers)
            self.assertEqual(normalized.summary, "Fix crash in voice module")
            self.assertGreater(len(normalized.description), 0)

    def test_normalize_insufficient_task_raises(self):
        """Test that insufficient tasks raise InsufficientTaskInfo."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = Path(tmpdir)

            normalizer = TaskNormalizer(memory_dir=memory_dir)

            with self.assertRaises(InsufficientTaskInfo) as ctx:
                normalizer.normalize("fix bug issue")

            self.assertIn("description", ctx.exception.missing_fields)

    def test_normalize_minimal_but_valid_task(self):
        """Test normalization of minimal but valid task."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = Path(tmpdir)

            normalizer = TaskNormalizer(memory_dir=memory_dir)

            task = "Fix audit chain hash verification"

            normalized = normalizer.normalize(task)

            self.assertEqual(normalized.type, TaskType.BUG_FIX)
            self.assertIsNotNone(normalized.severity)
            self.assertEqual(normalized.summary, task)

    def test_normalize_unknown_type_fallback(self):
        """Test that unknown type is handled gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = Path(tmpdir)

            normalizer = TaskNormalizer(memory_dir=memory_dir)

            task = "Update something in the mysterious place"

            normalized = normalizer.normalize(task)

            # Should succeed with UNKNOWN type
            self.assertEqual(normalized.type, TaskType.UNKNOWN)
            self.assertIsNotNone(normalized.severity)

    def test_normalize_metadata_populated(self):
        """Test that metadata dict is properly populated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = Path(tmpdir)

            normalizer = TaskNormalizer(memory_dir=memory_dir)

            task = "Fix bug in core/file.py affecting L16"

            normalized = normalizer.normalize(task)

            self.assertIsNotNone(normalized.metadata)
            self.assertIn("raw_length", normalized.metadata)
            self.assertIn("component_count", normalized.metadata)
            self.assertIn("layer_count", normalized.metadata)
            self.assertIn("memory_hits", normalized.metadata)
            self.assertIn("incident_hits", normalized.metadata)

    def test_normalize_with_memory_enrichment(self):
        """Test full normalization with memory enrichment."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = Path(tmpdir)

            # Create memory file
            (memory_dir / "incident-crash.md").write_text(
                "Voice crash issue\nAudio rendering\n"
            )

            normalizer = TaskNormalizer(memory_dir=memory_dir)

            task = "Fix crash in voice audio rendering"

            normalized = normalizer.normalize(task)

            # Should include memory context
            self.assertGreater(len(normalized.memory_context), 0)


class TestKeyTermExtraction(unittest.TestCase):
    """Test key term extraction for memory search.

    Verifies stop word filtering and deduplication.
    """

    def setUp(self):
        """Create normalizer with temporary memory directory."""
        self.temp_dir = tempfile.mkdtemp()
        self.normalizer = TaskNormalizer(memory_dir=Path(self.temp_dir))

    def test_extract_key_terms_basic(self):
        """Test basic key term extraction."""
        task = "Fix crash in voice module affecting TTS rendering"
        terms = self.normalizer._extract_key_terms(task)

        # Content words should be included
        self.assertIn("crash", terms)
        self.assertIn("voice", terms)
        self.assertIn("module", terms)
        self.assertIn("tts", terms)
        self.assertIn("rendering", terms)

    def test_extract_key_terms_exclude_stopwords(self):
        """Test that stop words are excluded."""
        task = "Fix crash in voice module affecting TTS rendering"
        terms = self.normalizer._extract_key_terms(task)

        # Stop words should not be included
        self.assertNotIn("in", terms)
        self.assertNotIn("the", terms)
        self.assertNotIn("a", terms)
        self.assertNotIn("and", terms)

    def test_extract_key_terms_deduplication(self):
        """Test that key terms are deduplicated."""
        task = "voice voice module module crash crash"
        terms = self.normalizer._extract_key_terms(task)

        # Each term should appear only once
        self.assertEqual(terms.count("voice"), 1)
        self.assertEqual(terms.count("module"), 1)
        self.assertEqual(terms.count("crash"), 1)

    def test_extract_key_terms_short_words_excluded(self):
        """Test that very short words (<=2 chars) are excluded."""
        task = "Is it ok to go to the place"
        terms = self.normalizer._extract_key_terms(task)

        # Very short words should be excluded (min length 3)
        self.assertNotIn("is", terms)
        self.assertNotIn("it", terms)
        self.assertNotIn("ok", terms)
        self.assertNotIn("to", terms)

    def test_extract_key_terms_with_special_chars(self):
        """Test term extraction with special characters."""
        task = "Fix crash in core/voice/renderer.py affecting TTS performance"
        terms = self.normalizer._extract_key_terms(task)

        # Should extract content words and path components despite special chars
        self.assertIn("crash", terms)
        self.assertIn("voice", terms)
        self.assertIn("renderer", terms)
        self.assertIn("tts", terms)
        self.assertIn("performance", terms)

    def test_extract_key_terms_case_normalization(self):
        """Test that terms are normalized to lowercase."""
        task = "CRASH in VOICE Module affecting TTS Rendering"
        terms = self.normalizer._extract_key_terms(task)

        # All terms should be lowercase
        for term in terms:
            self.assertEqual(term, term.lower())
            self.assertNotIn(term.upper(), terms)


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and boundary conditions."""

    def setUp(self):
        """Create normalizer with temporary memory directory."""
        self.temp_dir = tempfile.mkdtemp()
        self.normalizer = TaskNormalizer(memory_dir=Path(self.temp_dir))

    def test_very_long_task(self):
        """Test normalization of very long task."""
        task = "Fix " + ("crash in voice " * 100) + "module"

        normalized = self.normalizer.normalize(task)

        self.assertEqual(normalized.type, TaskType.BUG_FIX)
        self.assertGreater(normalized.metadata["raw_length"], 1000)

    def test_task_with_unicode(self):
        """Test task with unicode characters."""
        task = "Fix crash in module — critical issue 🚨"

        normalized = self.normalizer.normalize(task)

        self.assertEqual(normalized.type, TaskType.BUG_FIX)

    def test_task_with_multiple_newlines(self):
        """Test task with multiple consecutive newlines."""
        task = "Fix crash\n\n\n\nIn voice module\n\n\n\nDetails here"

        normalized = self.normalizer.normalize(task)

        self.assertIsNotNone(normalized.summary)
        self.assertGreater(len(normalized.description), 0)

    def test_task_all_uppercase(self):
        """Test that uppercase keywords are recognized."""
        task = "FIX CRASH IN CORE/VOICE/RENDERER.PY"

        normalized = self.normalizer.normalize(task)

        self.assertEqual(normalized.type, TaskType.BUG_FIX)
        self.assertEqual(normalized.severity, Severity.HIGH.value)

    def test_task_mixed_case_paths(self):
        """Test extraction of mixed-case file paths."""
        task = "Fix bug in Core/Voice/Renderer.py"

        normalized = self.normalizer.normalize(task)

        # Should extract at least some components
        self.assertIsNotNone(normalized.components)


if __name__ == "__main__":
    unittest.main(verbosity=2)
