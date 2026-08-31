"""Phase 0: Task Normalizer — Structured task metadata extraction.

This module normalizes raw task descriptions into structured metadata for
downstream analysis, triage, and routing within the Task Engine (ADR-0267).

## Normalization Pipeline

A task passes through six analysis phases:

    1. Sufficiency Validation — Does the task have minimum descriptive content?
    2. Type Detection — What kind of task is this? (bug, feature, refactor, etc.)
    3. Component Extraction — Which files/modules/layers are affected?
    4. Severity Inference — How critical is this? (low, medium, high)
    5. Memory Enrichment — Which prior reports/incidents are relevant?
    6. Incident Linking — Which incident records share affected components?

Each phase is independent and can fail safely (with defaults or exceptions).

## Type Classification

Seven task types are recognized:

    - BUG_FIX: Fixing broken functionality or crashes
    - FEATURE: Adding new functionality or capability
    - REFACTOR: Reorganizing/simplifying code without behavior change
    - INCIDENT: Emergency/critical-path response to live outages
    - DOCUMENTATION: Docs, docstrings, comments
    - PERFORMANCE: Optimizing speed, latency, resource usage
    - UNKNOWN: No confident type match (fallback)

Type detection uses keyword matching with two confidence tiers:
- Primary keywords (high confidence): "fix", "bug", "crash", "hang"
- Secondary keywords (medium confidence): "error", "broken", "issue"

## Severity Levels

Three severity levels based on keyword presence:

    - HIGH: crash, hang, data loss, security, compliance, CRITICAL
    - MEDIUM: bug, issue, error, exception, broken, inconsistent (default)
    - LOW: typo, formatting, whitespace, cosmetic, docstring

## Component Detection

Components are extracted via regex patterns:

    - File paths: `path/to/file.py`, `core/compliance/layer.tsx`
    - Module roots: `core/`, `operator/`, `console/`, `bridge/`, `forge/`, `voice/`
    - Layer references: `L1` through `L44`

## Memory Enrichment

Scans ~/.claude/projects/CorvinOS/memory/ for relevant prior reports:
- Extracts key terms (content words, excludes stop words)
- Scores memory files by keyword overlap
- Returns files with score >= 2, ranked by score

Fallback to env var `CORVIN_HOME` if default path doesn't exist.

## Incident Linking

Automatically finds related incident reports:
- Searches for `incident-*.md` files in memory directory
- Links incidents that mention the same affected components
- Returns incident names for cross-reference

## Validation & Error Handling

A task is considered sufficient if:
    1. Not empty (len > 0)
    2. At least 10 characters
    3. At least 3 words
    4. At least 2 words outside the generic-only set ("fix", "bug", "issue", "problem", "task")

Tasks that fail validation raise `InsufficientTaskInfo` with a specific
clarification request and a list of missing fields.

## Example

    >>> from operator.task_analysis.normalizer import TaskNormalizer
    >>> normalizer = TaskNormalizer()
    >>>
    >>> task = '''Fix crash in voice module when processing audio > 5min
    ...
    ... The TTS rendering hangs for files longer than 5 minutes.
    ... Affects L23 speech-to-text and core/voice/renderer.py.
    ... Related to ADR-0185 voice reliability.
    ... '''
    >>>
    >>> normalized = normalizer.normalize(task)
    >>> normalized.type
    <TaskType.BUG_FIX: 'bug_fix'>
    >>> normalized.severity
    'high'
    >>> normalized.affected_layers
    ['L23']
    >>> normalized.components
    ['core', 'voice', 'core/voice/renderer.py']
    >>> normalized.metadata['component_count']
    3
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set
import os


class TaskType(Enum):
    """Enumeration of recognized task types.

    Each type corresponds to a distinct pattern of change:
    - BUG_FIX: Repair of broken functionality
    - FEATURE: Addition of new capability
    - REFACTOR: Code reorganization without behavior change
    - INCIDENT: Emergency/critical live issue
    - DOCUMENTATION: Docs, comments, docstrings
    - PERFORMANCE: Speed/latency/resource optimization
    - UNKNOWN: No confident match (fallback)
    """

    BUG_FIX = "bug_fix"
    FEATURE = "feature"
    REFACTOR = "refactor"
    INCIDENT = "incident"
    DOCUMENTATION = "docs"
    PERFORMANCE = "performance"
    UNKNOWN = "unknown"


class Severity(Enum):
    """Severity levels for tasks.

    Severity guides triage, resource allocation, and routing:
    - HIGH: crashes, data loss, security, compliance violations
    - MEDIUM: broken features, errors (default for unclassified)
    - LOW: typos, formatting, cosmetic changes
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class InsufficientTaskInfo(Exception):
    """Raised when a task lacks critical information for processing.

    Attributes:
        missing_fields: List of field names/aspects that are missing
        clarification_request: Human-readable explanation + guidance
    """

    def __init__(self, missing_fields: List[str], clarification_request: str):
        """Initialize with missing fields and clarification request.

        Args:
            missing_fields: List of missing aspects (e.g., ['description', 'context'])
            clarification_request: User-facing guidance to improve the task
        """
        self.missing_fields = missing_fields
        self.clarification_request = clarification_request
        super().__init__(clarification_request)


@dataclass
class SufficiencyCheck:
    """Result of task sufficiency validation.

    Attributes:
        valid: True if task passed validation
        missing_fields: List of fields/aspects that are missing (if not valid)
        clarification_request: Guidance for improvement (if not valid)
    """

    valid: bool
    missing_fields: List[str] = field(default_factory=list)
    clarification_request: Optional[str] = None


@dataclass
class NormalizedTask:
    """A normalized task with extracted structured metadata.

    All extraction phases (type, components, severity, memory, incidents)
    have completed and the task is ready for downstream processing.

    Attributes:
        summary: First line of the task (human-readable title)
        description: Remaining task text (details, context, links)
        type: Detected task type (see TaskType)
        severity: Inferred severity level (see Severity)
        components: Extracted affected files/modules/paths
        affected_layers: Extracted security/compliance layer references (L1–L44)
        memory_context: Matching memory files found via keyword enrichment
        related_incidents: Incident reports mentioning the same components
        metadata: Aggregated metrics and extraction details
    """

    summary: str
    description: str
    type: TaskType
    severity: str
    components: List[str]
    affected_layers: List[str]
    memory_context: List[str]
    related_incidents: List[str]
    metadata: Dict[str, any]


class TaskNormalizer:
    """Normalizes raw task descriptions into structured metadata.

    The normalizer applies a six-phase pipeline to extract type, components,
    severity, memory context, and incident links from raw text. Each phase
    uses keyword matching, regex patterns, and heuristics to infer structure
    that downstream tools (routers, planners, LDD orchestrators) rely on.

    Phases are order-dependent (validation → type → components → severity →
    memory → incidents), but each is self-contained so a failure in one
    propagates safely (validation raises, others default).

    Attributes:
        memory_dir: Path to memory directory (~/.claude/projects/CorvinOS/memory/)
        TYPE_KEYWORDS: Two-tier keyword lookup for task type detection
        SEVERITY_*_KEYWORDS: Keyword lists for severity inference
        LAYER_PATTERN: Regex for L1–L44 layer references
        FILE_PATH_PATTERN: Regex for file path extraction
        MODULE_PATTERN: Regex for module path extraction
    """

    # Keyword patterns for type detection (primary → secondary confidence)
    # Priority order (checked in reverse due to dict insertion order):
    # 1. Most specific types first: DOCUMENTATION, INCIDENT, REFACTOR
    # 2. Then action types: FEATURE, BUG_FIX (order matters: FEATURE before BUG_FIX
    #    because both can occur in one task, FEATURE should win in ambiguity)
    # 3. Last: PERFORMANCE (often combined with others)
    TYPE_KEYWORDS = {
        TaskType.DOCUMENTATION: {
            "primary": ["docstring", "documentation", "readme", "guide"],
            "secondary": ["doc", "docs", "comment", "tutorial"],
        },
        TaskType.INCIDENT: {
            "primary": [
                "incident",
                "outage",
                "down",
            ],
            "secondary": ["critical", "emergency", "alert", "urgent"],
        },
        TaskType.REFACTOR: {
            "primary": [
                "refactor",
                "cleanup",
                "reorganize",
                "simplify",
                "rename",
            ],
            "secondary": ["restructure", "rewrite", "consolidate"],
        },
        TaskType.FEATURE: {
            "primary": [
                "implement",
                "new",
                "support",
                "enable",
                "introduce",
            ],
            "secondary": ["add", "feature", "capability", "functionality"],
        },
        TaskType.BUG_FIX: {
            "primary": [
                "fix",
                "bug",
                "issue",
                "broken",
                "crash",
                "hang",
                "freeze",
                "error",
            ],
            "secondary": ["fails", "doesn't work", "not working"],
        },
        TaskType.PERFORMANCE: {
            "primary": [
                "performance",
                "slow",
                "optimize",
                "latency",
                "timeout",
                "inefficient",
            ],
            "secondary": ["speed", "throughput", "bottleneck"],
        },
    }

    # Severity keywords (HIGH > MEDIUM > LOW)
    SEVERITY_HIGH_KEYWORDS = [
        "crash",
        "hang",
        "freeze",
        "data loss",
        "security",
        "critical",
        "exploit",
        "breach",
        "dataleak",
        "corruption",
        "audit",
        "compliance",
        "fail-open",
        "fail-closed",
        "loop",
        "infinite",
    ]

    SEVERITY_MEDIUM_KEYWORDS = [
        "bug",
        "issue",
        "problem",
        "fails",
        "error",
        "exception",
        "doesn't work",
        "broken",
        "inconsistent",
        "race condition",
    ]

    SEVERITY_LOW_KEYWORDS = [
        "typo",
        "formatting",
        "whitespace",
        "cosmetic",
        "docstring",
        "comment",
        "style",
        "linting",
        "indentation",
    ]

    # Regex patterns for extraction
    LAYER_PATTERN = re.compile(r"\bL\d{1,2}\b")
    FILE_PATH_PATTERN = re.compile(
        r"(?:[a-zA-Z0-9_./\-]+(?:\.py|\.tsx|\.json|\.yaml|\.sql|\.md|\.sh))"
    )
    MODULE_PATTERN = re.compile(
        r"\b(?:core|operator|console|bridge|forge|voice)/[a-zA-Z0-9_./\-]+"
    )

    def __init__(self, memory_dir: Optional[Path] = None):
        """Initialize the task normalizer.

        Args:
            memory_dir: Optional path to memory directory. If not provided,
                       resolves to ~/.claude/projects/CorvinOS/memory/ or
                       $CORVIN_HOME/tenants/_default/sessions/.../memory/.
        """
        if memory_dir:
            self.memory_dir = memory_dir
        else:
            # Try CORVIN_HOME first
            corvin_home = os.environ.get("CORVIN_HOME")
            if corvin_home:
                self.memory_dir = Path(corvin_home).parent / "projects" / "CorvinOS" / "memory"
            else:
                # Fallback to canonical ~/.claude location
                self.memory_dir = (
                    Path.home()
                    / ".claude"
                    / "projects"
                    / "CorvinOS"
                    / "memory"
                )

        self._memory_cache: Optional[Dict[str, str]] = None

    def normalize(self, raw_task: str) -> NormalizedTask:
        """Normalize a raw task description into structured metadata.

        This is the primary entry point. The task passes through all six
        normalization phases and returns a fully enriched NormalizedTask.

        Pipeline order:
            1. Validate sufficiency (raises InsufficientTaskInfo if invalid)
            2. Detect type (keyword-based, with fallback)
            3. Extract components (file paths, modules)
            4. Extract layers (L1–L44 references)
            5. Infer severity (keyword-based, with fallback)
            6. Enrich from memory (scan .md files for relevance)
            7. Link incidents (find incident-*.md mentioning components)

        Args:
            raw_task: Raw task description (typically from user input or CLI)

        Returns:
            A NormalizedTask with all fields populated.

        Raises:
            InsufficientTaskInfo: If task lacks critical information.
                Includes missing_fields list and clarification_request string.

        Example:
            >>> normalizer = TaskNormalizer()
            >>> task = "Fix crash in core/voice/renderer.py on long audio"
            >>> n = normalizer.normalize(task)
            >>> n.type
            <TaskType.BUG_FIX: 'bug_fix'>
            >>> n.severity
            'high'
        """
        # Phase 1: Validate sufficiency (must come first; raises)
        sufficiency = self._validate_sufficiency(raw_task)
        if not sufficiency.valid:
            raise InsufficientTaskInfo(
                sufficiency.missing_fields,
                sufficiency.clarification_request
                or "Task lacks critical information",
            )

        # Phase 2: Detect task type
        task_type = self._detect_type(raw_task)

        # Phase 3: Extract components and layers
        components = self._extract_components(raw_task)
        affected_layers = self._extract_layers(raw_task)

        # Phase 4: Infer severity
        severity = self._infer_severity(raw_task)

        # Phase 5: Enrich from memory
        memory_context = self._enrich_from_memory(raw_task, task_type, components)

        # Phase 6: Find related incidents
        related_incidents = self._find_related_incidents(components, memory_context)

        # Extract summary and description
        lines = raw_task.strip().split("\n")
        summary = lines[0].strip() if lines else "Untitled task"
        description = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""

        return NormalizedTask(
            summary=summary,
            description=description,
            type=task_type,
            severity=severity,
            components=components,
            affected_layers=affected_layers,
            memory_context=memory_context,
            related_incidents=related_incidents,
            metadata={
                "raw_length": len(raw_task),
                "component_count": len(components),
                "layer_count": len(affected_layers),
                "memory_hits": len(memory_context),
                "incident_hits": len(related_incidents),
            },
        )

    def _detect_type(self, task: str) -> TaskType:
        """Detect task type from keyword matching.

        Uses two-tier confidence approach with prioritized type checking:
        1. Check most specific types first (DOCUMENTATION, INCIDENT, REFACTOR)
        2. Then FEATURE (before BUG_FIX to avoid "add feature" -> BUG_FIX)
        3. Then BUG_FIX
        4. Finally PERFORMANCE (catch-all for optimization tasks)
        5. Within each type, check primary keywords first (high confidence)
        6. Then secondary keywords (medium confidence)
        7. Fallback to UNKNOWN if no match

        This ordering prevents ambiguous keywords from blocking specific type detection.

        Args:
            task: Raw task text

        Returns:
            Detected TaskType (or UNKNOWN)
        """
        task_lower = task.lower()

        # Explicit priority order: specific -> general
        type_priority_order = [
            TaskType.DOCUMENTATION,
            TaskType.INCIDENT,
            TaskType.REFACTOR,
            TaskType.FEATURE,
            TaskType.BUG_FIX,
            TaskType.PERFORMANCE,
        ]

        # First pass: check primary keywords (high confidence)
        for task_type in type_priority_order:
            keywords = self.TYPE_KEYWORDS.get(task_type, {})
            for keyword in keywords.get("primary", []):
                if keyword in task_lower:
                    return task_type

        # Second pass: check secondary keywords (medium confidence)
        for task_type in type_priority_order:
            keywords = self.TYPE_KEYWORDS.get(task_type, {})
            for keyword in keywords.get("secondary", []):
                if keyword in task_lower:
                    return task_type

        return TaskType.UNKNOWN

    def _extract_components(self, task: str) -> List[str]:
        """Extract affected components (files, modules, paths).

        Looks for:
        - File paths: core/module/file.py, operator/bridges/adapter.py
        - Module roots: core/, operator/, console/, bridge/, forge/, voice/
        - Infers module root from nested paths

        Args:
            task: Raw task text

        Returns:
            Sorted, deduplicated list of component paths
        """
        components: Set[str] = set()

        # Find explicit file paths
        file_matches = self.FILE_PATH_PATTERN.findall(task)
        components.update(file_matches)

        # Find module paths
        module_matches = self.MODULE_PATTERN.findall(task)
        components.update(module_matches)

        # Infer module roots from nested paths
        for comp in list(components):
            if "/" in comp:
                # Extract module root (first component)
                parts = comp.split("/")
                if parts[0] in ["core", "operator", "console", "bridge", "forge", "voice"]:
                    components.add(parts[0])

        return sorted(list(components))

    def _extract_layers(self, task: str) -> List[str]:
        """Extract security/compliance layer references (L1–L44).

        Args:
            task: Raw task text

        Returns:
            Sorted, deduplicated list of layer identifiers (e.g., ['L10', 'L16'])
        """
        matches = self.LAYER_PATTERN.findall(task)
        return sorted(list(set(matches)))

    def _infer_severity(self, task: str) -> str:
        """Infer task severity from keyword presence.

        Priority: HIGH > MEDIUM > LOW
        - HIGH: crash, hang, data loss, security, compliance, critical
        - MEDIUM: bug, issue, error, broken (default)
        - LOW: typo, formatting, cosmetic

        Args:
            task: Raw task text

        Returns:
            Severity level as string ('high', 'medium', 'low')
        """
        task_lower = task.lower()

        # Check HIGH severity keywords
        for keyword in self.SEVERITY_HIGH_KEYWORDS:
            if keyword in task_lower:
                return Severity.HIGH.value

        # Check MEDIUM severity keywords
        for keyword in self.SEVERITY_MEDIUM_KEYWORDS:
            if keyword in task_lower:
                return Severity.MEDIUM.value

        # Check LOW severity keywords
        for keyword in self.SEVERITY_LOW_KEYWORDS:
            if keyword in task_lower:
                return Severity.LOW.value

        # Default: MEDIUM (safest default for unclassified tasks)
        return Severity.MEDIUM.value

    def _enrich_from_memory(
        self, task: str, task_type: TaskType, components: List[str]
    ) -> List[str]:
        """Find and rank relevant memory files via keyword enrichment.

        Strategy:
        1. Extract key terms from task (content words, exclude stop words)
        2. Scan memory directory for .md files
        3. Score each file by keyword overlap (content + filename)
        4. Return files with score >= 2, ranked by score

        Args:
            task: Raw task text
            task_type: Detected task type (not currently used but available)
            components: Extracted components (not currently used but available)

        Returns:
            List of memory file names (e.g., ['incident-2026-08-04-crash.md'])
                ranked by relevance score (highest first)
        """
        if not self.memory_dir.exists():
            return []

        memory_hits: List[tuple] = []
        task_lower = task.lower()

        # Extract key terms (content words, no stop words)
        key_terms = self._extract_key_terms(task)

        # Scan memory directory for .md files
        try:
            for md_file in self.memory_dir.glob("*.md"):
                # Read file content (with error tolerance)
                try:
                    content = md_file.read_text(encoding="utf-8", errors="ignore")
                    content_lower = content.lower()

                    # Score based on keyword matches in content
                    score = 0
                    for term in key_terms:
                        if term in content_lower:
                            # Count occurrences, but cap per term
                            score += min(content_lower.count(term), 5)

                    # Boost score for matches in filename (significant boost)
                    filename_lower = md_file.stem.lower()
                    for term in key_terms:
                        if term in filename_lower:
                            score += 10  # Strong boost for filename matches

                    # Include files with score >= 2
                    if score >= 2:
                        memory_hits.append((md_file.name, score))

                except (IOError, OSError):
                    # Skip unreadable files
                    continue

        except (IOError, OSError):
            # Skip if memory_dir is not accessible
            pass

        # Sort by score (descending) and return file names
        memory_hits.sort(key=lambda x: x[1], reverse=True)
        return [name for name, _ in memory_hits]

    def _find_related_incidents(
        self, components: List[str], memory_context: List[str]
    ) -> List[str]:
        """Find incident reports mentioning the same components.

        Scans memory_dir for incident-*.md files and checks if they
        mention any of the affected components. Also checks for partial
        component matches (e.g., "core/voice" matches files mentioning
        "core/voice/renderer.py").

        Args:
            components: Extracted components (e.g., ['core/voice/renderer.py'])
            memory_context: Previously found memory files (unused, but available)

        Returns:
            List of incident file names (e.g., ['incident-2026-08-01-hang.md'])
        """
        if not self.memory_dir.exists():
            return []

        related: List[str] = []

        try:
            for md_file in self.memory_dir.glob("incident-*.md"):
                try:
                    content = md_file.read_text(encoding="utf-8", errors="ignore")
                    content_lower = content.lower()

                    # Check if any component is mentioned in incident
                    for component in components:
                        component_lower = component.lower()

                        # Direct match
                        if component_lower in content_lower:
                            related.append(md_file.name)
                            break

                        # Try partial matching for paths
                        # If component is "core/voice", also match "core/voice/renderer.py"
                        # and vice versa: if "core/voice/renderer.py" in content, match "core/voice"
                        parts = component_lower.split("/")
                        for i in range(len(parts)):
                            partial = "/".join(parts[: i + 1])
                            if partial in content_lower and len(partial) > 2:
                                related.append(md_file.name)
                                break
                        else:
                            # If no partial match found, continue to next component
                            continue
                        # If partial match found, break from component loop
                        break

                except (IOError, OSError):
                    # Skip unreadable files
                    continue

        except (IOError, OSError):
            # Skip if memory_dir is not accessible
            pass

        return related

    def _validate_sufficiency(self, task: str) -> SufficiencyCheck:
        """Validate that a task has minimum descriptive content.

        Checks:
        1. Task is not empty (len > 0)
        2. Task has minimum length (>= 10 chars)
        3. Task has minimum word count (>= 3 words)
        4. Task has at least 2 words outside generic-only set

        Args:
            task: Raw task text

        Returns:
            SufficiencyCheck with valid=True if all checks pass,
            or valid=False with missing_fields and clarification_request
        """
        if not task or not task.strip():
            return SufficiencyCheck(
                valid=False,
                missing_fields=["description"],
                clarification_request="Task is empty. Please provide a description.",
            )

        task_stripped = task.strip()

        # Check minimum length (must be at least 10 chars)
        if len(task_stripped) < 10:
            return SufficiencyCheck(
                valid=False,
                missing_fields=["description"],
                clarification_request="Task is too short. Please provide more details (min. 10 chars).",
            )

        # Check minimum word count
        words = task_stripped.split()
        if len(words) < 3:
            return SufficiencyCheck(
                valid=False,
                missing_fields=["description"],
                clarification_request="Task is too vague. Please provide more context (min. 3 words).",
            )

        # Check for overly generic descriptions
        generic_only_words = {"fix", "bug", "issue", "problem", "task"}
        non_generic = [w for w in words if w.lower() not in generic_only_words]

        if len(non_generic) < 2:
            return SufficiencyCheck(
                valid=False,
                missing_fields=["description", "context"],
                clarification_request="Task lacks specific details. What exactly should be fixed or implemented?",
            )

        return SufficiencyCheck(valid=True)

    def _extract_key_terms(self, task: str) -> List[str]:
        """Extract key search terms from task for memory enrichment.

        Filters out stop words and keeps content words (len > 2).
        Also extracts path components from file paths (e.g., "voice", "renderer"
        from "core/voice/renderer.py").

        Args:
            task: Raw task text

        Returns:
            List of unique key terms (deduplicated, lowercase)
        """
        stop_words = {
            "the",
            "a",
            "an",
            "and",
            "or",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "being",
            "have",
            "has",
            "had",
            "do",
            "does",
            "did",
            "will",
            "would",
            "could",
            "should",
            "may",
            "might",
            "in",
            "on",
            "at",
            "to",
            "from",
            "for",
            "with",
            "by",
            "of",
            "that",
            "this",
            "it",
            "its",
            "but",
            "not",
            "so",
            "as",
            "please",
            "thanks",
            "pls",
            "py",  # Common file extension
            "tsx",
            "json",
            "yaml",
            "md",
        }

        words = task.lower().split()
        terms = set()

        for word in words:
            # First, try to extract path components (split by / and .)
            if "/" in word or "." in word:
                # Split by path separators and dots
                parts = re.split(r"[/\.\-]+", word)
                for part in parts:
                    clean_part = re.sub(r"[^\w]", "", part)
                    if len(clean_part) > 2 and clean_part not in stop_words:
                        terms.add(clean_part)
            else:
                # Regular word: remove punctuation
                clean_word = re.sub(r"[^\w\-]", "", word)

                # Keep if length > 2 and not a stop word
                if len(clean_word) > 2 and clean_word not in stop_words:
                    terms.add(clean_word)

        return list(terms)
