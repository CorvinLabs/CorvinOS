"""Phase 1: Graph Routing — Five Independent Task-to-Component Routers.

This module implements five orthogonal graph-based routers for mapping normalized
tasks to affected code, tests, ADRs, layers, and code-diff patterns. Each router
is independent, isolated, and testable.

Routers:
    1. CallGraphRouter — Extract call-chain dependencies from component imports
    2. TestGraphRouter — Find test files related to affected components
    3. ADRGraphRouter — Match ADRs by affected_layers in paths: field
    4. LayerGraphRouter — Match components against layer-manifest.yaml definitions
    5. CodeDiffGraphRouter — Map task_type → expected diff scope

Output:
    Each router returns a GraphMatch, which is then passed to ConfidenceScorer
    for scoring and thresholding. A ClassifiedTask aggregates all five scores
    and determines which graphs to recommend downstream.

ADR:
    ADR-0267 — Task Engine: Router Layer Architecture
"""

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set
import logging

logger = logging.getLogger(__name__)


@dataclass
class GraphMatch:
    """Result of a single router.

    Attributes:
        name: Router name (e.g., 'call_graph', 'test_graph')
        score: Confidence 0.0–1.0 based on match quality
        metadata: Router-specific details (files, count, depth, etc.)
    """

    name: str
    score: float
    metadata: Dict = field(default_factory=dict)

    def __post_init__(self):
        """Validate score is in [0.0, 1.0]."""
        if not 0.0 <= self.score <= 1.0:
            raise ValueError(f"Score must be in [0.0, 1.0], got {self.score}")


class CallGraphRouter:
    """Extract call-chain dependencies from component imports.

    Uses grep to find transitive imports and build a call graph from the
    affected components. Scores based on depth and breadth of imports.

    Algorithm:
        1. Start with normalized.components (file paths, modules)
        2. For each component, grep for 'from|import MODULE'
        3. Build transitive closure up to depth=3
        4. Score: depth of closure / max expected depth (3)
        5. If no imports found, score = 0.0
    """

    def __init__(self, repo_root: Path = None):
        """Initialize with repo root.

        Args:
            repo_root: Path to repo root (default: infer from __file__)
        """
        if repo_root is None:
            repo_root = Path(__file__).resolve().parents[2]  # CorvinOS/
        self.repo_root = repo_root

    def route(self, task) -> GraphMatch:
        """Analyze call graph for task's affected components.

        Args:
            task: NormalizedTask with components list

        Returns:
            GraphMatch with call-graph analysis
        """
        if not task.components:
            return GraphMatch("call_graph", 0.0, {"files": [], "depth": 0})

        try:
            files = self._collect_imports(task.components)
            depth = self._estimate_depth(files)
            score = min(1.0, depth / 3.0)  # max depth = 3

            return GraphMatch(
                "call_graph",
                score,
                {
                    "files": list(files)[:20],  # cap at 20 for readability
                    "depth": depth,
                    "import_count": len(files),
                },
            )
        except Exception as e:
            logger.warning(f"CallGraphRouter failed: {e}")
            return GraphMatch("call_graph", 0.0, {"error": str(e)})

    def _collect_imports(self, components: List[str], depth: int = 0) -> Set[str]:
        """Recursively collect imports from components.

        Args:
            components: List of file paths or module names
            depth: Current recursion depth (stops at 3)

        Returns:
            Set of imported file paths
        """
        if depth > 3:
            return set()

        files = set()
        for comp in components:
            if comp.endswith(".py"):
                # Exact file
                path = self.repo_root / comp
                if path.is_file():
                    files.add(comp)
            else:
                # Module path (core, operator, etc.)
                pattern = self.repo_root / comp / "**" / "*.py"
                try:
                    for py_file in Path(self.repo_root).glob(str(pattern)):
                        if py_file.is_file():
                            rel_path = py_file.relative_to(self.repo_root)
                            files.add(str(rel_path))
                except Exception:
                    pass

        # For each collected file, grep for imports (one level deep)
        if depth < 3:
            new_components = []
            for f in list(files)[:10]:  # limit to avoid explosion
                try:
                    result = subprocess.run(
                        ["grep", "-h", "^from\\|^import", str(self.repo_root / f)],
                        capture_output=True,
                        text=True,
                        timeout=2,
                    )
                    for line in result.stdout.splitlines()[:5]:  # limit per file
                        # Extract module name
                        match = re.search(r"(?:from|import)\s+([\w.]+)", line)
                        if match:
                            module = match.group(1).split(".")[0]
                            if module in ("core", "operator", "forge", "voice", "bridge"):
                                new_components.append(module)
                except (subprocess.TimeoutExpired, Exception):
                    pass

            if new_components:
                files.update(self._collect_imports(list(set(new_components)), depth + 1))

        return files

    def _estimate_depth(self, files: Set[str]) -> int:
        """Estimate import chain depth.

        Args:
            files: Set of imported files

        Returns:
            Estimated depth (0–3)
        """
        if not files:
            return 0
        # Heuristic: count unique module prefixes
        modules = set()
        for f in files:
            parts = f.split("/")
            if parts and parts[0] in ("core", "operator", "forge", "voice"):
                modules.add(parts[0])
        return min(3, len(modules))


class TestGraphRouter:
    """Find test files related to affected components.

    Matches component names against test file naming patterns:
    - test_{component}.py
    - test_{module}.py
    - {component}_test.py
    - tests/{component}/**

    Scores based on number of test files found vs. expected ratio.
    """

    def __init__(self, repo_root: Path = None):
        """Initialize with repo root.

        Args:
            repo_root: Path to repo root (default: infer from __file__)
        """
        if repo_root is None:
            repo_root = Path(__file__).resolve().parents[2]  # CorvinOS/
        self.repo_root = repo_root
        self.test_patterns = [
            "test_{}.py",
            "test_{}_*.py",
            "{}_test.py",
            "tests/{}/",
            "**/test_{}.py",
        ]

    def route(self, task) -> GraphMatch:
        """Find test files for task's affected components.

        Args:
            task: NormalizedTask with components list

        Returns:
            GraphMatch with test discovery results
        """
        if not task.components:
            return GraphMatch("test_graph", 0.0, {"test_files": [], "found": 0})

        try:
            test_files = self._find_tests(task.components)
            found = len(test_files)
            expected = len(task.components) * 2  # Expect ~2 tests per component
            score = min(1.0, found / max(1, expected))

            return GraphMatch(
                "test_graph",
                score,
                {"test_files": list(test_files)[:20], "found": found, "expected": expected},
            )
        except Exception as e:
            logger.warning(f"TestGraphRouter failed: {e}")
            return GraphMatch("test_graph", 0.0, {"error": str(e)})

    def _find_tests(self, components: List[str]) -> Set[str]:
        """Find test files matching components.

        Args:
            components: List of component names/paths

        Returns:
            Set of test file paths
        """
        test_files = set()
        for comp in components:
            # Extract base name (last component of path)
            base = Path(comp).stem  # e.g., "renderer.py" → "renderer"
            if base.endswith("_test"):
                base = base[:-5]

            # Try each pattern
            for pattern in self.test_patterns:
                search_pattern = pattern.format(base)
                try:
                    for match in self.repo_root.glob(search_pattern):
                        if match.is_file() and match.suffix == ".py":
                            test_files.add(str(match.relative_to(self.repo_root)))
                except Exception:
                    pass

        return test_files


class ADRGraphRouter:
    """Match ADRs by affected layers in paths: field.

    Loads the ADR graph via scripts/adr_graph.py API and finds ADRs
    that mention the task's affected_layers in their paths: field.

    Scores based on number of matching ADRs.
    """

    def __init__(self, repo_root: Path = None):
        """Initialize with repo root.

        Args:
            repo_root: Path to repo root (default: infer from __file__)
        """
        if repo_root is None:
            repo_root = Path(__file__).resolve().parents[2]  # CorvinOS/
        self.repo_root = repo_root
        self._adr_graph_cache = None

    def route(self, task) -> GraphMatch:
        """Find ADRs matching task's affected layers.

        Args:
            task: NormalizedTask with affected_layers list

        Returns:
            GraphMatch with matching ADRs
        """
        if not task.affected_layers:
            return GraphMatch("adr_graph", 0.0, {"adrs": [], "matched": 0})

        try:
            adrs = self._find_adrs(task.affected_layers)
            matched = len(adrs)
            score = min(1.0, matched / 3.0)  # expect ~3 ADRs per layer

            return GraphMatch(
                "adr_graph",
                score,
                {
                    "adrs": adrs[:15],
                    "matched": matched,
                    "layers": task.affected_layers,
                },
            )
        except Exception as e:
            logger.warning(f"ADRGraphRouter failed: {e}")
            return GraphMatch("adr_graph", 0.0, {"error": str(e)})

    def _find_adrs(self, affected_layers: List[str]) -> List[str]:
        """Find ADRs that mention affected layers.

        Args:
            affected_layers: List of layer names (e.g., ['L10', 'L16'])

        Returns:
            List of ADR IDs
        """
        if self._adr_graph_cache is None:
            self._adr_graph_cache = self._load_adr_graph()

        if not self._adr_graph_cache:
            return []

        adrs = []
        for layer in affected_layers:
            # Look for ADRs mentioning this layer in paths or docs
            for adr_id, node in self._adr_graph_cache.items():
                # Check paths and docs fields
                combined = (node.get("paths") or []) + (node.get("docs") or [])
                if any(layer.lower() in str(p).lower() for p in combined):
                    adrs.append(adr_id)

        return list(set(adrs))  # deduplicate

    def _load_adr_graph(self) -> Dict:
        """Load ADR graph from scripts/adr_graph.py.

        Returns:
            Dict mapping ADR id → node info, or {} if unavailable
        """
        try:
            # Try to import and call adr_graph API
            adr_graph_file = self.repo_root / "scripts" / "adr_graph.py"
            if not adr_graph_file.is_file():
                return {}

            # Use subprocess to load graph and output as JSON
            result = subprocess.run(
                [
                    "python3",
                    str(adr_graph_file),
                    "--adr",
                    "0001",
                    "--format",
                    "json",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode != 0:
                return {}

            # Try to parse JSON (format: [{"id": "...", ...}])
            import json

            lines = result.stdout.strip().split("\n")
            for line in lines:
                try:
                    data = json.loads(line)
                    if isinstance(data, dict) and "id" in data:
                        if self._adr_graph_cache is None:
                            self._adr_graph_cache = {}
                        self._adr_graph_cache[data["id"]] = data
                except json.JSONDecodeError:
                    pass

            return self._adr_graph_cache or {}
        except Exception as e:
            logger.warning(f"Failed to load ADR graph: {e}")
            return {}


class LayerGraphRouter:
    """Match components against layer-manifest.yaml definitions.

    Loads docs/layer-manifest.yaml and matches task.components against
    each layer's code_patterns. Returns matching layers.

    Scores based on overlap ratio (matched components / total components).
    """

    def __init__(self, repo_root: Path = None):
        """Initialize with repo root.

        Args:
            repo_root: Path to repo root (default: infer from __file__)
        """
        if repo_root is None:
            repo_root = Path(__file__).resolve().parents[2]  # CorvinOS/
        self.repo_root = repo_root
        self._manifest_cache = None

    def route(self, task) -> GraphMatch:
        """Match components to layers.

        Args:
            task: NormalizedTask with components list

        Returns:
            GraphMatch with matching layers
        """
        if not task.components:
            return GraphMatch("layer_graph", 0.0, {"layers": [], "matched": 0})

        try:
            layers = self._match_layers(task.components)
            matched = len(layers)
            expected = len(task.components)  # expect 1 layer per component
            score = min(1.0, matched / max(1, expected))

            return GraphMatch(
                "layer_graph",
                score,
                {
                    "layers": layers[:15],
                    "matched": matched,
                    "components": task.components,
                },
            )
        except Exception as e:
            logger.warning(f"LayerGraphRouter failed: {e}")
            return GraphMatch("layer_graph", 0.0, {"error": str(e)})

    def _match_layers(self, components: List[str]) -> List[str]:
        """Match components to layers via patterns.

        Args:
            components: List of component paths

        Returns:
            List of layer IDs that match
        """
        manifest = self._load_manifest()
        if not manifest:
            return []

        matched_layers = set()
        for layer in manifest.get("layers", []):
            patterns = layer.get("code_patterns", [])
            for pattern in patterns:
                for comp in components:
                    # Simple glob matching
                    if self._pattern_matches(comp, pattern):
                        matched_layers.add(layer["id"])
                        break

        return sorted(matched_layers)

    def _pattern_matches(self, component: str, pattern: str) -> bool:
        """Check if component matches a glob pattern.

        Args:
            component: Component path (e.g., "core/voice/renderer.py")
            pattern: Glob pattern (e.g., "core/voice/**/*.py")

        Returns:
            True if matches
        """
        # Convert glob pattern to regex for simple matching
        # This is a simplified version; in production, use fnmatch.fnmatch
        try:
            import fnmatch

            return fnmatch.fnmatch(component, pattern)
        except Exception:
            return False

    def _load_manifest(self) -> Optional[Dict]:
        """Load layer-manifest.yaml.

        Returns:
            Parsed YAML dict or None if unavailable
        """
        if self._manifest_cache is not None:
            return self._manifest_cache

        try:
            manifest_file = self.repo_root / "docs" / "layer-manifest.yaml"
            if not manifest_file.is_file():
                return None

            import yaml

            with open(manifest_file, "r") as f:
                self._manifest_cache = yaml.safe_load(f)
            return self._manifest_cache
        except Exception as e:
            logger.warning(f"Failed to load layer manifest: {e}")
            return None


class CodeDiffGraphRouter:
    """Map task_type → expected diff scope.

    Uses task type and severity to estimate expected diff size and
    affected areas. Scores based on confidence in scope prediction.

    Algorithm:
        - BUG_FIX + high severity → low scope (single module fix)
        - FEATURE + high severity → medium scope (new subsystem)
        - REFACTOR + high severity → high scope (broad changes)
        - PERFORMANCE → medium scope (targeted optimization)
    """

    def route(self, task) -> GraphMatch:
        """Estimate diff scope from task type/severity.

        Args:
            task: NormalizedTask with type and severity

        Returns:
            GraphMatch with scope estimation
        """
        try:
            scope, confidence = self._estimate_scope(task)

            return GraphMatch(
                "code_diff",
                confidence,
                {
                    "scope": scope,
                    "task_type": task.type.value if task.type else "unknown",
                    "severity": task.severity,
                    "confidence": confidence,
                },
            )
        except Exception as e:
            logger.warning(f"CodeDiffGraphRouter failed: {e}")
            return GraphMatch("code_diff", 0.0, {"error": str(e)})

    def _estimate_scope(self, task) -> tuple[str, float]:
        """Estimate diff scope and confidence.

        Args:
            task: NormalizedTask

        Returns:
            Tuple of (scope, confidence)
            scope: 'low', 'medium', 'high'
            confidence: 0.0–1.0
        """
        from .normalizer import TaskType

        task_type = task.type if hasattr(task.type, 'value') else task.type
        severity = task.severity if isinstance(task.severity, str) else task.severity.value

        # Mapping: (task_type, severity) → (scope, confidence)
        mappings = {
            (TaskType.BUG_FIX, "high"): ("low", 0.85),
            (TaskType.BUG_FIX, "medium"): ("low", 0.75),
            (TaskType.BUG_FIX, "low"): ("low", 0.65),
            (TaskType.FEATURE, "high"): ("medium", 0.80),
            (TaskType.FEATURE, "medium"): ("medium", 0.70),
            (TaskType.FEATURE, "low"): ("medium", 0.60),
            (TaskType.REFACTOR, "high"): ("high", 0.80),
            (TaskType.REFACTOR, "medium"): ("medium", 0.75),
            (TaskType.REFACTOR, "low"): ("low", 0.70),
            (TaskType.INCIDENT, "high"): ("low", 0.90),
            (TaskType.INCIDENT, "medium"): ("low", 0.80),
            (TaskType.INCIDENT, "low"): ("low", 0.70),
            (TaskType.PERFORMANCE, "high"): ("medium", 0.75),
            (TaskType.PERFORMANCE, "medium"): ("medium", 0.65),
            (TaskType.PERFORMANCE, "low"): ("low", 0.55),
            (TaskType.DOCUMENTATION, "high"): ("low", 0.85),
            (TaskType.DOCUMENTATION, "medium"): ("low", 0.80),
            (TaskType.DOCUMENTATION, "low"): ("low", 0.85),
        }

        # Default if type not found
        default_key = (TaskType.UNKNOWN, severity)
        scope, confidence = mappings.get((task_type, severity), ("medium", 0.50))

        return scope, confidence
