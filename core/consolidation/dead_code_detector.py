"""
Dead-Code Detection — ADR-0421

Finds unreachable functions, unused imports, and orphaned classes.
Integrates with audit trail for GDPR Art. 30 compliance.

Detects:
- Unused imports (no references)
- Unreachable functions (defined but never called)
- Orphaned classes (instantiated nowhere)
- Unused module-level variables

LIMITATIONS (v1.0):
- Type annotations are not analyzed (false positives for imports in annotations)
- Dynamic calls via __getattr__, getattr() are not tracked
- Method dispatch (ast.NodeVisitor callbacks) may be missed
- Dataclass methods and properties may be reported as unused
- External API calls visible only within this module
Use confidence scores to prioritize findings: 0.9+ (high confidence),
0.7-0.8 (medium), <0.7 (requires manual review).
"""

import ast
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Set, Dict, List, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeadCodeFinding:
    """One dead-code finding (immutable for audit trail)."""

    finding_type: str  # "unused_import" | "unreachable_function" | "orphaned_class" | "unused_variable"
    module_path: str
    name: str
    line_number: int
    definition_context: str  # function/class/module context
    confidence: float  # 0.0-1.0: higher = more certain


@dataclass(frozen=True)
class DeadCodeReport:
    """Report of dead-code findings (immutable for audit trail)."""

    total_files_scanned: int
    files_with_issues: int
    findings: Tuple[DeadCodeFinding, ...]  # Immutable tuple
    scan_duration_ms: float
    tenant_id: str = "_default"

    def to_dict(self) -> dict:
        """Convert to dict for audit logging."""
        return {
            "total_files_scanned": self.total_files_scanned,
            "files_with_issues": self.files_with_issues,
            "findings_count": len(self.findings),
            "findings": [asdict(f) for f in self.findings],
            "scan_duration_ms": self.scan_duration_ms,
            "tenant_id": self.tenant_id,
        }


class DeadCodeVisitor(ast.NodeVisitor):
    """AST visitor to find defined symbols (functions, classes, imports, variables)."""

    def __init__(self, module_path: str):
        """Initialize visitor with module path."""
        self.module_path = module_path
        self.defined_functions: Dict[str, int] = {}  # name -> line
        self.defined_classes: Dict[str, int] = {}  # name -> line
        self.imported_names: Dict[str, int] = {}  # name -> line
        self.module_variables: Dict[str, int] = {}  # name -> line
        self.all_names_used: Set[str] = set()
        self._function_stack: List[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Record function definition and track usage within it."""
        self.defined_functions[node.name] = node.lineno
        self._function_stack.append(node.name)
        self.generic_visit(node)
        self._function_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Record async function definition."""
        self.defined_functions[node.name] = node.lineno
        self._function_stack.append(node.name)
        self.generic_visit(node)
        self._function_stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Record class definition."""
        self.defined_classes[node.name] = node.lineno
        self._function_stack.append(node.name)
        self.generic_visit(node)
        self._function_stack.pop()

    def visit_Import(self, node: ast.Import) -> None:
        """Record imports."""
        for alias in node.names:
            name = alias.asname or alias.name
            self.imported_names[name] = node.lineno
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Record from-imports."""
        for alias in node.names:
            if alias.name == "*":
                # Star imports are considered used (conservative)
                continue
            name = alias.asname or alias.name
            self.imported_names[name] = node.lineno
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        """Record module-level variable assignments."""
        for target in node.targets:
            if isinstance(target, ast.Name):
                if not self._function_stack:  # Module level only
                    self.module_variables[target.id] = node.lineno
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        """Track name usage (any reference)."""
        if isinstance(node.ctx, (ast.Load, ast.Del)):
            self.all_names_used.add(node.id)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Track attribute access (e.g., obj.method)."""
        # Don't mark the attribute as used (too noisy), but visit children
        self.generic_visit(node)


class DeadCodeDetector:
    """Detect dead code (unused imports, unreachable functions, orphaned classes)."""

    def __init__(self, root_dir: Path, *, tenant_id: str = "_default"):
        """Initialize detector.

        Args:
            root_dir: Root directory to scan
            tenant_id: Tenant identifier (keyword-only)
        """
        self.root_dir = Path(root_dir)
        self.tenant_id = tenant_id
        self._cache: Dict[Path, DeadCodeVisitor] = {}

    def scan(self) -> DeadCodeReport:
        """Scan all Python files for dead code.

        Returns:
            DeadCodeReport with findings
        """
        import time

        start = time.time()
        findings: List[DeadCodeFinding] = []
        files_scanned = 0
        files_with_issues = 0

        # Find all Python files
        python_files = list(self.root_dir.rglob("*.py"))

        for py_file in python_files:
            # Skip __pycache__ and test files (for now)
            if "__pycache__" in str(py_file):
                continue

            files_scanned += 1

            try:
                file_findings = self._scan_file(py_file)
                if file_findings:
                    findings.extend(file_findings)
                    files_with_issues += 1
            except Exception as e:
                logger.warning(f"Failed to scan {py_file}: {e}")

        duration_ms = (time.time() - start) * 1000

        return DeadCodeReport(
            total_files_scanned=files_scanned,
            files_with_issues=files_with_issues,
            findings=tuple(findings),
            scan_duration_ms=duration_ms,
            tenant_id=self.tenant_id,
        )

    def _scan_file(self, py_file: Path) -> List[DeadCodeFinding]:
        """Scan one Python file for dead code.

        Args:
            py_file: Path to Python file

        Returns:
            List of DeadCodeFinding
        """
        try:
            with open(py_file, "r", encoding="utf-8") as f:
                source = f.read()
        except Exception as e:
            logger.warning(f"Cannot read {py_file}: {e}")
            return []

        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            logger.warning(f"Syntax error in {py_file}: {e}")
            return []

        visitor = DeadCodeVisitor(str(py_file.relative_to(self.root_dir)))
        visitor.visit(tree)

        findings: List[DeadCodeFinding] = []

        # Check unused imports
        for import_name, lineno in visitor.imported_names.items():
            if import_name not in visitor.all_names_used and not import_name.startswith("_"):
                findings.append(
                    DeadCodeFinding(
                        finding_type="unused_import",
                        module_path=str(py_file.relative_to(self.root_dir)),
                        name=import_name,
                        line_number=lineno,
                        definition_context="module",
                        confidence=0.9,  # High confidence for unused imports
                    )
                )

        # Check unreachable functions
        for func_name, lineno in visitor.defined_functions.items():
            if func_name not in visitor.all_names_used and not func_name.startswith("_"):
                findings.append(
                    DeadCodeFinding(
                        finding_type="unreachable_function",
                        module_path=str(py_file.relative_to(self.root_dir)),
                        name=func_name,
                        line_number=lineno,
                        definition_context="module",
                        confidence=0.7,  # Medium confidence (may be called dynamically)
                    )
                )

        # Check orphaned classes
        for class_name, lineno in visitor.defined_classes.items():
            if class_name not in visitor.all_names_used and not class_name.startswith("_"):
                findings.append(
                    DeadCodeFinding(
                        finding_type="orphaned_class",
                        module_path=str(py_file.relative_to(self.root_dir)),
                        name=class_name,
                        line_number=lineno,
                        definition_context="module",
                        confidence=0.6,  # Lower confidence (may be used via __all__)
                    )
                )

        # Check unused module variables
        for var_name, lineno in visitor.module_variables.items():
            if var_name not in visitor.all_names_used and not var_name.startswith("_"):
                findings.append(
                    DeadCodeFinding(
                        finding_type="unused_variable",
                        module_path=str(py_file.relative_to(self.root_dir)),
                        name=var_name,
                        line_number=lineno,
                        definition_context="module",
                        confidence=0.8,
                    )
                )

        return findings

    def get_audit_event_dict(self, report: DeadCodeReport) -> dict:
        """Convert report to audit event dict (for audit chain integration).

        GDPR Art. 30: Document dead-code detection as a maintenance event.

        Args:
            report: DeadCodeReport

        Returns:
            Dict suitable for AuditEntry
        """
        return {
            "event_type": "consolidation_dead_code_scan",
            "actor": "consolidation_system",
            "action": "detect_dead_code",
            "resource": f"codebase_{report.total_files_scanned}_files",
            "result": "success" if report.findings else "no_issues_found",
            "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
            "details": report.to_dict(),
        }
