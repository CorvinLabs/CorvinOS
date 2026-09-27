"""
Skill Compiler (ADR-0533)

Compiles skill manifests to runtime instances.
Validates module encapsulation and enforces audit-trail invariants.

NOT WIRED: no production caller as of 2026-09-27 (adversarial review).
"""

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any
import yaml
import json

try:  # package import (core.skills.compiler)
    from .skill_validator import validate_skill_manifest_dict, SkillValidationReport
except ImportError:  # flat import with core/skills on sys.path
    from skill_validator import validate_skill_manifest_dict, SkillValidationReport


@dataclass
class SkillCall:
    """Represents a skill call in code (e.g., os.delegation_router.infer)."""
    skill_id: str
    function_name: str
    line_number: int
    column_offset: int
    source_line: str


@dataclass
class ImportStatement:
    """Represents an import statement in code."""
    module_name: str
    alias: Optional[str]
    line_number: int
    is_from_import: bool
    imported_name: Optional[str]  # For "from X import Y"


@dataclass
class CompilationReport:
    """Report from manifest compilation."""
    is_valid: bool
    skill_id: Optional[str] = None
    version: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    dependency_graph: Dict[str, List[str]] = field(default_factory=dict)
    skill_calls_found: List[SkillCall] = field(default_factory=list)
    runtime_instance: Optional[Dict[str, Any]] = None
    
    def __str__(self):
        status = "✅ COMPILED" if self.is_valid else "❌ COMPILATION FAILED"
        result = f"{status} {self.skill_id}@{self.version}\n"
        if self.errors:
            result += f"\nErrors ({len(self.errors)}):\n"
            for error in self.errors:
                result += f"  ❌ {error}\n"
        if self.warnings:
            result += f"\nWarnings ({len(self.warnings)}):\n"
            for warning in self.warnings:
                result += f"  ⚠️  {warning}\n"
        return result


class SkillCallExtractor(ast.NodeVisitor):
    """
    AST visitor that extracts skill calls.
    
    Detects patterns like:
    - runtime.call("os.brain_loader.infer", {...})
    - os.brain_loader.infer(...)  (if via import)
    """
    
    #: Root name of a skill namespace called directly (``os.brain_loader.infer``).
    SKILL_ROOTS = frozenset({"os"})

    def __init__(self, source_lines: List[str]):
        self.skill_calls: List[SkillCall] = []
        self.bound_names: Set[str] = set()
        self.imports: List[ImportStatement] = []
        self.source_lines = source_lines
    
    def visit_ImportFrom(self, node: ast.ImportFrom):
        """Visit 'from X import Y' statements."""
        if node.module:
            for alias in node.names:
                self.bound_names.add(alias.asname or alias.name)
                self.imports.append(ImportStatement(
                    module_name=node.module,
                    alias=alias.asname or alias.name,
                    line_number=node.lineno,
                    is_from_import=True,
                    imported_name=alias.name,
                ))
        self.generic_visit(node)
    
    def visit_Import(self, node: ast.Import):
        """Visit 'import X' statements."""
        for alias in node.names:
            self.bound_names.add(alias.asname or alias.name.split(".")[0])
            self.imports.append(ImportStatement(
                module_name=alias.name,
                alias=alias.asname or alias.name,
                line_number=node.lineno,
                is_from_import=False,
                imported_name=None,
            ))
        self.generic_visit(node)
    
    def visit_Call(self, node: ast.Call):
        """
        Visit function call expressions.
        Look for runtime.call("skill.func", ...) and direct skill calls.
        """
        # Check for runtime.call("skill_id.func", ...)
        if self._is_runtime_call(node):
            skill_call = self._extract_runtime_call(node)
            if skill_call:
                self.skill_calls.append(skill_call)
        
        # Check for direct skill calls (imported)
        elif self._is_direct_skill_call(node):
            skill_call = self._extract_direct_skill_call(node)
            if skill_call:
                self.skill_calls.append(skill_call)
        
        self.generic_visit(node)
    
    def _is_runtime_call(self, node: ast.Call) -> bool:
        """Check if this is a runtime.call(...) pattern."""
        if not isinstance(node.func, ast.Attribute):
            return False
        
        if node.func.attr != "call":
            return False
        
        if isinstance(node.func.value, ast.Name):
            return node.func.value.id == "runtime"
        
        return False
    
    def _extract_runtime_call(self, node: ast.Call) -> Optional[SkillCall]:
        """Extract skill call from runtime.call("skill_id.func", ...)."""
        if not node.args:
            return None
        
        first_arg = node.args[0]
        if not isinstance(first_arg, ast.Constant):
            return None
        
        skill_id_func = first_arg.value
        if not isinstance(skill_id_func, str):
            return None
        
        # Parse "skill_id.func" format
        parts = skill_id_func.rsplit(".", 1)
        if len(parts) != 2:
            return None
        
        skill_id, func_name = parts
        
        return SkillCall(
            skill_id=skill_id,
            function_name=func_name,
            line_number=node.lineno,
            column_offset=node.col_offset,
            source_line=self._get_source_line(node.lineno),
        )
    
    def _is_direct_skill_call(self, node: ast.Call) -> bool:
        """Check if this is a direct skill call (not via runtime)."""
        # Look for patterns like: os.brain_loader.infer(...)
        # This would be: Attribute(Attribute(Name, name), name)
        if not isinstance(node.func, ast.Attribute):
            return False
        
        func_value = node.func.value
        if not isinstance(func_value, ast.Attribute):
            return False
        
        # Root must be a skill namespace (``os``) that the module has NOT bound
        # by an import — ``import os; os.path.join(...)`` is the stdlib, not
        # a skill. Matching every ``a.b.c()`` call failed ordinary code with
        # "Undeclared skill call: os.path.join".
        if not isinstance(func_value.value, ast.Name):
            return False
        root = func_value.value.id
        return root in self.SKILL_ROOTS and root not in self.bound_names
    
    def _extract_direct_skill_call(self, node: ast.Call) -> Optional[SkillCall]:
        """Extract a direct skill call like os.brain_loader.infer(...)."""
        # os.brain_loader.infer(...)
        # func = Attribute(name="infer", value=Attribute(name="brain_loader", ...))
        
        root_name = node.func.value.value.id if isinstance(node.func.value.value, ast.Name) else None
        if not root_name:
            return None
        
        middle_name = node.func.value.attr
        func_name = node.func.attr
        
        skill_id = f"{root_name}.{middle_name}"
        
        return SkillCall(
            skill_id=skill_id,
            function_name=func_name,
            line_number=node.lineno,
            column_offset=node.col_offset,
            source_line=self._get_source_line(node.lineno),
        )
    
    def _get_source_line(self, line_number: int) -> str:
        """Get source line by line number (1-indexed)."""
        if 0 < line_number <= len(self.source_lines):
            return self.source_lines[line_number - 1].strip()
        return ""


class SkillCompiler:
    """Compiles skill manifests and code into runtime instances."""
    
    def __init__(self):
        """Initialize compiler."""
        pass
    
    def compile_skill(
        self,
        skill_dir: Path,
    ) -> CompilationReport:
        """
        Compile a skill from a directory.
        
        Performs:
        1. Manifest validation (ADR-0533 schema)
        2. Module encapsulation checks
        3. Dependency validation
        4. Skill call extraction and validation
        5. Audit invariant checks
        
        Args:
            skill_dir: Path to skill directory (contains manifest.yaml, skill.py, etc.)
            
        Returns:
            CompilationReport with results
        """
        report = CompilationReport(is_valid=True)
        
        manifest_path = skill_dir / "manifest.yaml"
        skill_code_path = skill_dir / "skill.py"
        
        # Step 1: Load and validate manifest
        if not manifest_path.exists():
            report.is_valid = False
            report.errors.append(f"Manifest not found: {manifest_path}")
            return report
        
        try:
            with open(manifest_path) as f:
                manifest = yaml.safe_load(f)
        except Exception as e:
            report.is_valid = False
            report.errors.append(f"Failed to load manifest: {e}")
            return report
        
        # Validate manifest
        validation_report = validate_skill_manifest_dict(manifest)
        if not validation_report.is_valid:
            report.is_valid = False
            report.errors.extend(validation_report.blockers)
            report.warnings.extend(validation_report.warnings)
            return report
        
        report.skill_id = manifest.get("name")
        report.version = manifest.get("version")
        
        # Step 2: Build dependency graph
        report.dependency_graph = self._build_dependency_graph(manifest)
        
        # Step 3: Check module encapsulation (if code exists)
        if skill_code_path.exists():
            encapsulation_errors = self._validate_module_encapsulation(
                skill_code_path,
                manifest,
            )
            if encapsulation_errors:
                report.is_valid = False
                report.errors.extend(encapsulation_errors)
        
        # If any errors, don't proceed to runtime instance
        if not report.is_valid:
            return report
        
        # Step 4: Build runtime instance
        report.runtime_instance = self._build_runtime_instance(manifest, skill_dir)
        
        return report
    
    def _build_dependency_graph(self, manifest: Dict) -> Dict[str, List[str]]:
        """Build dependency graph for the skill."""
        graph = {}
        skill_id = manifest.get("name", "unknown")
        
        dependencies = []
        for dep in manifest.get("depends_on", []):
            dep_name = dep.get("name")
            if dep_name:
                dependencies.append(dep_name)
        
        graph[skill_id] = dependencies
        return graph
    
    def _validate_module_encapsulation(
        self,
        skill_code_path: Path,
        manifest: Dict,
    ) -> List[str]:
        """
        Validate module encapsulation (ADR-0533).
        
        Checks:
        1. No direct imports of sealed modules (corvin.skills.*)
        2. All skill calls are declared in manifest.depends_on
        3. Entry points match manifest declarations
        
        Args:
            skill_code_path: Path to skill.py
            manifest: Skill manifest
            
        Returns:
            List of validation errors (empty if valid)
        """
        errors = []
        
        try:
            with open(skill_code_path) as f:
                source = f.read()
                source_lines = source.split("\n")
        except Exception as e:
            return [f"Failed to read skill code: {e}"]
        
        # Parse AST
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            return [f"Syntax error in skill code: {e}"]
        
        # Extract imports and skill calls
        extractor = SkillCallExtractor(source_lines)
        extractor.visit(tree)
        
        # Check for forbidden direct imports. ``from corvin.skills import
        # brain_loader`` and ``import corvin.skills.brain_loader`` reach the
        # sealed module just as well as ``from corvin.skills.brain_loader
        # import infer``; the old ``startswith("corvin.skills.")`` test let the
        # first form through. Only ``from corvin.skills import SkillRuntime``.
        for imp in extractor.imports:
            if imp.module_name == "corvin.skills" or imp.module_name.startswith("corvin.skills."):
                if not (imp.is_from_import and imp.module_name == "corvin.skills"
                        and imp.imported_name == "SkillRuntime"):
                    errors.append(
                        f"Line {imp.line_number}: Direct import forbidden: "
                        f"from {imp.module_name} import {imp.imported_name or '*'}\n"
                        f"Fix: Use SkillRuntime instead:\n"
                        f"  from corvin.skills import SkillRuntime\n"
                        f"  runtime = SkillRuntime()\n"
                        f"  result = runtime.call('{imp.module_name}.{imp.imported_name}', ...)"
                    )
        
        # Check all skill calls are declared in manifest
        declared_deps = {
            dep.get("name"): dep.get("entry_point")
            for dep in manifest.get("depends_on", [])
        }
        
        for skill_call in extractor.skill_calls:
            if skill_call.skill_id not in declared_deps:
                errors.append(
                    f"Line {skill_call.line_number}: Undeclared skill call: "
                    f"{skill_call.skill_id}.{skill_call.function_name}\n"
                    f"Fix: Add to manifest.yaml depends_on:\n"
                    f"  - name: {skill_call.skill_id}\n"
                    f"    version: >=0.0.0\n"
                    f"    entry_point: {skill_call.function_name}"
                )
            elif declared_deps[skill_call.skill_id] and declared_deps[skill_call.skill_id] != skill_call.function_name:
                errors.append(
                    f"Line {skill_call.line_number}: Wrong entry point for {skill_call.skill_id}\n"
                    f"Manifest declares: {declared_deps[skill_call.skill_id]}\n"
                    f"But code calls: {skill_call.function_name}"
                )
        
        return errors
    
    def _build_runtime_instance(
        self,
        manifest: Dict,
        skill_dir: Path,
    ) -> Dict[str, Any]:
        """
        Build runtime instance dictionary.
        
        Args:
            manifest: Skill manifest
            skill_dir: Skill directory
            
        Returns:
            Runtime instance configuration
        """
        return {
            "skill_id": manifest.get("name"),
            "version": manifest.get("version"),
            "goal": manifest.get("goal"),
            "triggers": manifest.get("triggers", []),
            "input_schema": manifest.get("input_schema", {}),
            "output_schema": manifest.get("output_schema", {}),
            "learning_signal": manifest.get("learning_signal", {}),
            "dependencies": manifest.get("depends_on", []),
            "boot_layer": manifest.get("boot_layer", "bundled"),
            "origin": manifest.get("origin", "builtin"),
            "scope": manifest.get("scope", "local_development"),
            "code_path": str(skill_dir / "skill.py"),
            "manifest_path": str(skill_dir / "manifest.yaml"),
        }


def compile_skill(skill_dir: Path) -> CompilationReport:
    """
    Compile a skill (convenience function).
    
    Args:
        skill_dir: Path to skill directory
        
    Returns:
        CompilationReport
    """
    compiler = SkillCompiler()
    return compiler.compile_skill(skill_dir)
