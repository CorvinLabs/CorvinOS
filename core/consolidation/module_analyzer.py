"""
Module Dependency Analyzer — ADR-0421

Builds dependency graph, detects circular dependencies, enforces module boundaries.
Integrates with audit trail for GDPR Art. 30 compliance.

Capabilities:
- Dependency graph construction (module -> imports)
- Circular dependency detection (SCCs via Tarjan's algorithm)
- Module boundary enforcement (explicit allowed imports)
- Cross-layer dependency validation

Algorithm: Tarjan's Strongly Connected Components (SCCs)
- Time complexity: O(V + E) where V = modules, E = imports
- Finds all cycles in one pass
- Robust against self-loops and multi-edges

LIMITATIONS (v1.0):
- Dynamic imports via __import__() or importlib not detected
- Conditional imports (if/try blocks) are still included
- Type hints in string form are not parsed
- Does not distinguish runtime vs. type-only imports
Results should be reviewed manually for accuracy.
"""

import ast
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Set, Dict, List, Tuple, FrozenSet

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CircularDependency:
    """One circular dependency cycle (immutable for audit trail)."""

    modules: Tuple[str, ...]  # Cycle: [A, B, C, A]
    cycle_length: int
    severity: str  # "critical" (2 modules) | "high" (3+)


@dataclass(frozen=True)
class BoundaryViolation:
    """One module boundary violation (immutable for audit trail)."""

    source_module: str
    target_module: str
    violation_type: str  # "cross_layer" | "disallowed_pattern"
    severity: str  # "error" | "warning"
    explanation: str


@dataclass(frozen=True)
class ModuleDependencyReport:
    """Report of module dependencies and violations (immutable for audit trail)."""

    modules_analyzed: int
    total_dependencies: int
    circular_dependencies: Tuple[CircularDependency, ...]
    boundary_violations: Tuple[BoundaryViolation, ...]
    dependency_graph: Dict[str, Tuple[str, ...]]  # Mutable dict for JSON serialization
    scan_duration_ms: float
    tenant_id: str = "_default"

    def to_dict(self) -> dict:
        """Convert to dict for audit logging."""
        return {
            "modules_analyzed": self.modules_analyzed,
            "total_dependencies": self.total_dependencies,
            "circular_dependencies": [
                {"modules": cd.modules, "cycle_length": cd.cycle_length, "severity": cd.severity}
                for cd in self.circular_dependencies
            ],
            "boundary_violations": [
                {
                    "source_module": bv.source_module,
                    "target_module": bv.target_module,
                    "violation_type": bv.violation_type,
                    "severity": bv.severity,
                    "explanation": bv.explanation,
                }
                for bv in self.boundary_violations
            ],
            "scan_duration_ms": self.scan_duration_ms,
            "tenant_id": self.tenant_id,
        }


class ImportVisitor(ast.NodeVisitor):
    """AST visitor to extract imports from a module."""

    def __init__(self, module_path: str):
        """Initialize visitor."""
        self.module_path = module_path
        self.imports: Set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        """Record imports."""
        for alias in node.names:
            module_name = alias.name.split(".")[0]  # Get root module
            if not module_name.startswith("_"):
                self.imports.add(module_name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Record from-imports."""
        if node.module:
            module_name = node.module.split(".")[0]
            if not module_name.startswith("_"):
                self.imports.add(module_name)
        self.generic_visit(node)


class ModuleAnalyzer:
    """Analyze module dependencies, detect cycles, enforce boundaries."""

    def __init__(
        self,
        root_dir: Path,
        *,
        tenant_id: str = "_default",
        layer_boundaries: Optional[Dict[str, List[str]]] = None,
    ):
        """Initialize analyzer.

        Args:
            root_dir: Root directory to scan
            tenant_id: Tenant identifier (keyword-only)
            layer_boundaries: Layer definitions {layer_name: [allowed_imports]} (keyword-only)
        """
        self.root_dir = Path(root_dir)
        self.tenant_id = tenant_id
        self.layer_boundaries = layer_boundaries or self._default_layer_boundaries()

    def _default_layer_boundaries(self) -> Dict[str, List[str]]:
        """Default layer boundaries for CorvinOS."""
        return {
            "audit": ["core/compliance", "dataclasses"],
            "compliance": ["core/audit", "pathlib"],
            "core": ["core/audit", "core/compliance"],
        }

    def scan(self) -> ModuleDependencyReport:
        """Scan all Python files and build dependency graph.

        Returns:
            ModuleDependencyReport with findings
        """
        import time

        start = time.time()

        # Build dependency graph
        dep_graph = self._build_dependency_graph()

        # Detect circular dependencies
        circular_deps = self._find_circular_dependencies(dep_graph)

        # Check boundary violations
        violations = self._check_boundary_violations(dep_graph)

        duration_ms = (time.time() - start) * 1000

        return ModuleDependencyReport(
            modules_analyzed=len(dep_graph),
            total_dependencies=sum(len(deps) for deps in dep_graph.values()),
            circular_dependencies=tuple(circular_deps),
            boundary_violations=tuple(violations),
            dependency_graph=dep_graph,
            scan_duration_ms=duration_ms,
            tenant_id=self.tenant_id,
        )

    def _build_dependency_graph(self) -> Dict[str, Tuple[str, ...]]:
        """Build module dependency graph.

        Returns:
            Dict[module_path -> tuple of import paths]
        """
        graph: Dict[str, Tuple[str, ...]] = {}
        python_files = list(self.root_dir.rglob("*.py"))

        for py_file in python_files:
            if "__pycache__" in str(py_file):
                continue

            try:
                with open(py_file, "r", encoding="utf-8") as f:
                    source = f.read()
            except Exception as e:
                logger.warning(f"Cannot read {py_file}: {e}")
                continue

            try:
                tree = ast.parse(source)
            except SyntaxError as e:
                logger.warning(f"Syntax error in {py_file}: {e}")
                continue

            visitor = ImportVisitor(str(py_file))
            visitor.visit(tree)

            module_name = self._get_module_name(py_file)
            if visitor.imports:
                graph[module_name] = tuple(sorted(visitor.imports))

        return graph

    def _get_module_name(self, py_file: Path) -> str:
        """Convert file path to module name (e.g., core/audit/chain.py -> core.audit.chain)."""
        rel_path = py_file.relative_to(self.root_dir)
        module = str(rel_path).replace(".py", "").replace("/", ".").replace("\\", ".")
        return module

    def _find_circular_dependencies(
        self, graph: Dict[str, Tuple[str, ...]]
    ) -> List[CircularDependency]:
        """Find circular dependencies using Tarjan's algorithm (SCCs).

        Args:
            graph: Module dependency graph

        Returns:
            List of CircularDependency
        """
        # Build adjacency list (only for modules we know about)
        adj: Dict[str, Set[str]] = {module: set(graph.get(module, ())) for module in graph}

        # Find SCCs
        sccs = self._tarjan_sccs(adj)

        # Convert SCCs to CircularDependency
        cycles: List[CircularDependency] = []
        for scc in sccs:
            if len(scc) > 1:  # Ignore self-loops
                cycle = tuple(sorted(scc)) + (sorted(scc)[0],)  # Add first module to end
                severity = "critical" if len(scc) == 2 else "high"
                cycles.append(
                    CircularDependency(
                        modules=cycle,
                        cycle_length=len(scc),
                        severity=severity,
                    )
                )

        return cycles

    def _tarjan_sccs(self, adj: Dict[str, Set[str]]) -> List[Set[str]]:
        """Find strongly connected components using Tarjan's algorithm.

        Args:
            adj: Adjacency list

        Returns:
            List of SCCs (each SCC is a set of nodes)
        """
        index_counter = [0]
        stack: List[str] = []
        indices: Dict[str, int] = {}
        lowlinks: Dict[str, int] = {}
        on_stack: Set[str] = set()
        sccs: List[Set[str]] = []

        def strongconnect(node: str) -> None:
            indices[node] = index_counter[0]
            lowlinks[node] = index_counter[0]
            index_counter[0] += 1
            stack.append(node)
            on_stack.add(node)

            for successor in adj.get(node, set()):
                if successor not in indices:
                    strongconnect(successor)
                    lowlinks[node] = min(lowlinks[node], lowlinks[successor])
                elif successor in on_stack:
                    lowlinks[node] = min(lowlinks[node], indices[successor])

            if lowlinks[node] == indices[node]:
                component: Set[str] = set()
                while True:
                    successor = stack.pop()
                    on_stack.remove(successor)
                    component.add(successor)
                    if successor == node:
                        break
                sccs.append(component)

        for node in adj:
            if node not in indices:
                strongconnect(node)

        return sccs

    def _check_boundary_violations(self, graph: Dict[str, Tuple[str, ...]]) -> List[BoundaryViolation]:
        """Check for module boundary violations.

        Args:
            graph: Module dependency graph

        Returns:
            List of BoundaryViolation
        """
        violations: List[BoundaryViolation] = []

        for source, imports in graph.items():
            for target in imports:
                # Check if this import violates defined boundaries
                for layer_name, allowed in self.layer_boundaries.items():
                    if source.startswith(layer_name + "."):
                        # This module is in this layer
                        for allowed_import in allowed:
                            if target.startswith(allowed_import.replace(".", "/")):
                                # Import is allowed
                                break
                        else:
                            # Import not in allowed list
                            violations.append(
                                BoundaryViolation(
                                    source_module=source,
                                    target_module=target,
                                    violation_type="cross_layer",
                                    severity="warning",
                                    explanation=f"{source} imports {target}, not in layer {layer_name}'s allowed list",
                                )
                            )

        return violations

    def get_audit_event_dict(self, report: ModuleDependencyReport) -> dict:
        """Convert report to audit event dict (for audit chain integration).

        GDPR Art. 30: Document module dependency analysis as a maintenance event.

        Args:
            report: ModuleDependencyReport

        Returns:
            Dict suitable for AuditEntry
        """
        return {
            "event_type": "consolidation_module_analysis",
            "actor": "consolidation_system",
            "action": "analyze_dependencies",
            "resource": f"codebase_{report.modules_analyzed}_modules",
            "result": "success" if not report.circular_dependencies else "circular_dependencies_found",
            "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
            "details": report.to_dict(),
        }
