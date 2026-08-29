"""
E2E Wiring Proof Validator — Phase 3.A
=======================================

Proves that code is reachable (not dead) and functional:
1. Reachability proof: Find ≥1 real call site outside definition + test files
2. Functional proof: E2E test through real transport boundary (HTTP/CLI/MCP/etc)

Phase 1 (reachability) is cheap; Phase 2 (functional) only runs if Phase 1 passes.
Both are fail-closed: zero found → BLOCK, dispatch root-cause-by-layer.
"""

import ast
import json
import logging
import re
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from enum import Enum


logger = logging.getLogger(__name__)


class ReachabilityStatus(Enum):
    """Reachability test outcome."""
    PASS = "pass"  # Found ≥1 real call site
    FAIL = "fail"  # Zero call sites found
    SKIP = "skip"  # Marked WIP/prototype


@dataclass
class CallSite:
    """A location where the target is invoked."""
    file_path: str
    line_number: int
    caller_function: str
    is_test_file: bool
    transport_boundary: Optional[str] = None  # HTTP/CLI/MCP/plugin-registry/etc
    confidence: float = 0.5  # 0.5=weak, 0.8=medium, 1.0=direct


@dataclass
class E2EWiringProofResult:
    """Result of a single E2E wiring validation."""
    entry_point: str  # function/endpoint/route name
    file_path: str
    reachability_status: ReachabilityStatus
    call_sites: List[CallSite] = field(default_factory=list)
    real_call_sites: List[CallSite] = field(default_factory=list)  # non-test, outside def file
    functional_test_passed: bool = False
    functional_test_output: Optional[str] = None
    failure_reason: Optional[str] = None
    required_transport_boundary: Optional[str] = None


class E2EWiringProofValidator:
    """
    Validates that new entry points (functions, endpoints, plugins) are reachable.

    Usage:
        validator = E2EWiringProofValidator(repo_root="/path/to/CorvinOS")
        result = validator.validate("core/console/app.py::my_new_endpoint")
        if not result.reachability_status == ReachabilityStatus.PASS:
            raise AssertionError(f"Unreachable: {result.failure_reason}")
    """

    def __init__(self, repo_root: Path = None):
        self.repo_root = Path(repo_root) if repo_root else Path.cwd()
        self._import_cache: Dict[str, ast.Module] = {}
        self._callsite_cache: Dict[str, List[CallSite]] = {}

    def validate(
        self,
        entry_point: str,
        required_transport: Optional[str] = None,
        functional_test_fn=None
    ) -> E2EWiringProofResult:
        """
        Two-phase validation:
        1. Phase 1 (reachability): Find ≥1 real call site
        2. Phase 2 (functional): Run E2E test if Phase 1 passes

        Args:
            entry_point: "file_path.py::function_name" or "file_path.py::ClassName.method"
            required_transport: "http" | "cli" | "mcp" | "plugin_registry" | None
            functional_test_fn: callable(entry_point, transport) -> bool

        Returns:
            E2EWiringProofResult with detailed reachability + functional outcome
        """
        logger.info(f"[E2E WIRING PROOF] Validating: {entry_point}")

        # Parse entry point string
        file_path_str, entry_name = entry_point.split("::")
        file_path = self.repo_root / file_path_str

        if not file_path.exists():
            result = E2EWiringProofResult(
                entry_point=entry_name,
                file_path=file_path_str,
                reachability_status=ReachabilityStatus.FAIL,
                failure_reason=f"Definition file not found: {file_path}"
            )
            logger.error(result.failure_reason)
            return result

        # Phase 1: Reachability proof
        logger.info(f"  Phase 1: Searching for call sites...")
        call_sites = self._find_all_call_sites(entry_name, file_path_str)
        real_call_sites = [
            cs for cs in call_sites
            if not cs.is_test_file and cs.file_path != file_path_str
        ]

        if not real_call_sites:
            result = E2EWiringProofResult(
                entry_point=entry_name,
                file_path=file_path_str,
                reachability_status=ReachabilityStatus.FAIL,
                call_sites=call_sites,
                real_call_sites=[],
                failure_reason=f"Zero real call sites found (only {len(call_sites)} weak references)"
            )
            logger.warning(f"  ✗ Phase 1 FAIL: {result.failure_reason}")
            return result

        logger.info(f"  ✓ Phase 1 PASS: Found {len(real_call_sites)} real call sites")

        # Phase 2: Functional proof (if requested and Phase 1 passed)
        functional_passed = True
        functional_output = None
        if functional_test_fn:
            logger.info(f"  Phase 2: Running functional E2E test...")
            try:
                transport_boundary = required_transport or self._infer_transport(real_call_sites)
                functional_passed = functional_test_fn(entry_point, transport_boundary)
                if functional_passed:
                    logger.info(f"  ✓ Phase 2 PASS: E2E test succeeded")
                    functional_output = f"Executed through {transport_boundary}"
                else:
                    logger.error(f"  ✗ Phase 2 FAIL: E2E test returned False")
            except Exception as e:
                logger.error(f"  ✗ Phase 2 ERROR: {e}")
                functional_passed = False
                functional_output = str(e)

        result = E2EWiringProofResult(
            entry_point=entry_name,
            file_path=file_path_str,
            reachability_status=ReachabilityStatus.PASS,  # Phase 1 passed
            call_sites=call_sites,
            real_call_sites=real_call_sites,
            functional_test_passed=functional_passed,
            functional_test_output=functional_output,
            required_transport_boundary=self._infer_transport(real_call_sites)
        )
        logger.info(f"[E2E WIRING PROOF] Result: {result.reachability_status.value}")
        return result

    def _find_all_call_sites(self, entry_name: str, source_file: str) -> List[CallSite]:
        """
        Find all places where `entry_name` is called.
        Returns direct calls + weak references (imports, argument passing).
        """
        sites = []
        search_files = list(self.repo_root.glob("**/*.py"))
        search_files = [
            f for f in search_files
            if ".venv" not in f.parts and "__pycache__" not in f.parts and ".pytest_cache" not in f.parts
        ]

        for py_file in search_files:
            try:
                tree = self._parse_file(py_file)
                is_test_file = "test" in py_file.name or "/tests/" in str(py_file)

                # Find calls to `entry_name`
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        # Direct function call: entry_name(...)
                        if isinstance(node.func, ast.Name) and node.func.id == entry_name:
                            sites.append(CallSite(
                                file_path=str(py_file.relative_to(self.repo_root)),
                                line_number=node.lineno,
                                caller_function=self._get_enclosing_function(node, tree),
                                is_test_file=is_test_file,
                                confidence=1.0
                            ))
                        # Attribute call: SomeClass.entry_name(...)
                        elif isinstance(node.func, ast.Attribute) and node.func.attr == entry_name:
                            sites.append(CallSite(
                                file_path=str(py_file.relative_to(self.repo_root)),
                                line_number=node.lineno,
                                caller_function=self._get_enclosing_function(node, tree),
                                is_test_file=is_test_file,
                                confidence=0.8
                            ))
            except SyntaxError as e:
                logger.warning(f"Skipping {py_file}: {e}")

        return sites

    def _parse_file(self, path: Path) -> ast.Module:
        """Parse a Python file, with caching."""
        if str(path) not in self._import_cache:
            try:
                with open(path) as f:
                    self._import_cache[str(path)] = ast.parse(f.read())
            except Exception as e:
                logger.error(f"Failed to parse {path}: {e}")
                self._import_cache[str(path)] = ast.Module(body=[])
        return self._import_cache[str(path)]

    def _get_enclosing_function(self, node: ast.expr, tree: ast.Module) -> str:
        """Find the function/method name that contains this node."""
        for parent in ast.walk(tree):
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.lineno >= parent.lineno and node.lineno <= parent.end_lineno:
                    return parent.name
        return "<module>"

    def _infer_transport(self, call_sites: List[CallSite]) -> Optional[str]:
        """Infer the transport boundary (HTTP/CLI/MCP/etc) from call site patterns."""
        transports = set()
        for cs in call_sites:
            if "route" in cs.caller_function.lower() or "/v1/" in cs.file_path:
                transports.add("http")
            if "cli" in cs.file_path.lower() or "_cmd" in cs.caller_function:
                transports.add("cli")
            if "mcp" in cs.file_path.lower():
                transports.add("mcp")
            if "plugin" in cs.file_path.lower():
                transports.add("plugin_registry")
        return list(transports)[0] if transports else None

    def validate_batch(self, entry_points: List[str]) -> Dict[str, E2EWiringProofResult]:
        """Validate multiple entry points."""
        results = {}
        for ep in entry_points:
            results[ep] = self.validate(ep)
        return results

    def to_json(self, result: E2EWiringProofResult) -> str:
        """Export result as JSON."""
        data = asdict(result)
        data['reachability_status'] = result.reachability_status.value
        data['call_sites'] = [asdict(cs) for cs in result.call_sites]
        data['real_call_sites'] = [asdict(cs) for cs in result.real_call_sites]
        return json.dumps(data, indent=2)
