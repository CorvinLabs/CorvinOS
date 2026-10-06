"""Tool Quarantine Workflow — staged tool import (ADR-2229 Phase 3).

Tool Forge (core/orchestration/tool_forge/registry.py) has no review state:
``ToolRegistry.create`` writes executable code straight into the registry. To
prevent bundles from bypassing operator review, imports stage tools in a
quarantine directory before the operator approves them into the active registry.

Quarantine storage: ``~/.corvin/tenants/{TENANT_ID}/global/forge/quarantine/tools/``
Each quarantined tool is stored as:
  - ``{tool_id}__{bundle_id}__{timestamp}.json`` — the ToolSpec
  - ``{tool_id}__{bundle_id}__{timestamp}.{sh,py,js}`` — the implementation file

Lifecycle:
  1. extract_tool_from_bundle() → QuarantinedTool (stored on disk)
  2. operator reviews quarantine + accepts or rejects
  3. accept_quarantined_tool() → ToolRegistry.create() called (on approval)
  4. tool is now active, quarantine entry cleaned up
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.paths import tenant_home
from core.pii.sensitive import PIIDetectionFailedClosed, detect_sensitive_types


class QuarantineError(RuntimeError):
    """Tool quarantine operation failed."""


@dataclass(frozen=True)
class QuarantinedTool:
    """A tool staged in quarantine, awaiting operator approval."""

    tool_id: str
    bundle_id: str
    timestamp: float
    version: str
    spec_json: dict[str, Any]  # The ToolSpec
    impl_data: bytes  # The implementation file (sh, py, js, etc.)
    impl_ext: str  # File extension: sh, py, js, etc.

    @property
    def quarantine_id(self) -> str:
        """Unique ID for this quarantine entry."""
        return f"{self.tool_id}__{self.bundle_id}__{int(self.timestamp)}"

    @property
    def spec_file(self) -> str:
        """Filename for the ToolSpec JSON."""
        return f"{self.quarantine_id}.json"

    @property
    def impl_file(self) -> str:
        """Filename for the implementation file."""
        return f"{self.quarantine_id}.{self.impl_ext}"


class ToolQuarantineWorkflow:
    """Manage tool quarantine staging + approval."""

    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self._quarantine_dir = self._get_quarantine_dir()

    def _get_quarantine_dir(self) -> Path:
        """Get the quarantine directory for this tenant, creating it if needed."""
        home = tenant_home(self.tenant_id)
        quarantine_dir = home / "global" / "forge" / "quarantine" / "tools"
        quarantine_dir.mkdir(parents=True, exist_ok=True)
        return quarantine_dir

    def stage_tool_from_bundle(
        self,
        tool_id: str,
        bundle_id: str,
        version: str,
        spec: dict[str, Any],
        impl_bytes: bytes,
        impl_ext: str,
        user_id: str,
    ) -> QuarantinedTool:
        """Stage a tool from a bundle into quarantine.

        Args:
            tool_id: The tool's ID (validated by bundle import)
            bundle_id: The source bundle's ID
            version: The tool's version
            spec: The ToolSpec (dict with id, version, description, etc.)
            impl_bytes: The implementation file bytes (shell, python, etc.)
            impl_ext: Extension of the implementation file (sh, py, js)
            user_id: The user staging the tool

        Returns:
            QuarantinedTool with all details for operator review

        Raises:
            QuarantineError if the tool contains secrets or storage fails
        """
        # Audit-First: check for secrets BEFORE writing anything
        self._scan_for_secrets(spec, impl_bytes, tool_id)

        timestamp = time.time()
        quarantined = QuarantinedTool(
            tool_id=tool_id,
            bundle_id=bundle_id,
            timestamp=timestamp,
            version=version,
            spec_json=spec,
            impl_data=impl_bytes,
            impl_ext=impl_ext,
        )

        # Write to quarantine directory (atomic: temp file + rename)
        spec_file = self._quarantine_dir / quarantined.spec_file
        impl_file = self._quarantine_dir / quarantined.impl_file

        try:
            # Write spec as JSON
            spec_temp = self._quarantine_dir / f".{quarantined.spec_file}.tmp"
            spec_temp.write_text(json.dumps(spec, indent=2))
            spec_temp.replace(spec_file)

            # Write implementation
            impl_temp = self._quarantine_dir / f".{quarantined.impl_file}.tmp"
            impl_temp.write_bytes(impl_bytes)
            impl_temp.replace(impl_file)

            # Make implementation readable only by owner (security)
            impl_file.chmod(0o600)
            spec_file.chmod(0o600)

        except (OSError, IOError) as exc:
            # Clean up any partial writes
            spec_file.unlink(missing_ok=True)
            impl_file.unlink(missing_ok=True)
            raise QuarantineError(
                f"failed to stage {tool_id} to quarantine: {type(exc).__name__}"
            ) from exc

        return quarantined

    def accept_quarantined_tool(
        self, quarantine_id: str, user_id: str
    ) -> dict[str, Any]:
        """Promote a quarantined tool to the active registry.

        The caller must then call ``ToolRegistry.create(spec, impl_bytes)`` to
        activate the tool. This method only handles quarantine cleanup; the
        actual registry write is the caller's responsibility.

        Args:
            quarantine_id: The tool's quarantine ID (from QuarantinedTool.quarantine_id)
            user_id: The operator approving the tool

        Returns:
            A dict with the spec and impl bytes for registry creation

        Raises:
            QuarantineError if the quarantine entry is missing or unreadable
        """
        # Parse quarantine_id to find the files
        spec_file = self._quarantine_dir / f"{quarantine_id}.json"
        # We don't know the impl extension without reading the spec, so scan for it
        impl_files = list(self._quarantine_dir.glob(f"{quarantine_id}.*"))
        impl_files = [f for f in impl_files if f.suffix != ".json"]

        if not spec_file.exists():
            raise QuarantineError(f"quarantine entry not found: {quarantine_id}")
        if not impl_files:
            raise QuarantineError(
                f"implementation file not found for quarantine {quarantine_id}"
            )
        if len(impl_files) > 1:
            raise QuarantineError(
                f"multiple implementation files for quarantine {quarantine_id}"
            )

        impl_file = impl_files[0]

        try:
            spec = json.loads(spec_file.read_text())
            impl_bytes = impl_file.read_bytes()
        except (OSError, IOError, json.JSONDecodeError) as exc:
            raise QuarantineError(
                f"failed to read quarantine {quarantine_id}: {type(exc).__name__}"
            ) from exc

        return {"spec": spec, "impl_bytes": impl_bytes}

    def cleanup_quarantine_entry(self, quarantine_id: str) -> None:
        """Delete a quarantine entry (after acceptance or rejection).

        Args:
            quarantine_id: The tool's quarantine ID

        Raises:
            QuarantineError if cleanup fails
        """
        spec_file = self._quarantine_dir / f"{quarantine_id}.json"
        impl_files = list(self._quarantine_dir.glob(f"{quarantine_id}.*"))
        impl_files = [f for f in impl_files if f.suffix != ".json"]

        try:
            spec_file.unlink(missing_ok=True)
            for impl_file in impl_files:
                impl_file.unlink(missing_ok=True)
        except OSError as exc:
            raise QuarantineError(
                f"failed to clean up quarantine {quarantine_id}: {type(exc).__name__}"
            ) from exc

    def list_quarantined_tools(self) -> list[QuarantinedTool]:
        """List all currently quarantined tools."""
        quarantined = []
        for spec_file in self._quarantine_dir.glob("*.json"):
            try:
                spec = json.loads(spec_file.read_text())
                # Parse quarantine_id from filename
                name_parts = spec_file.stem.split("__")
                if len(name_parts) < 3:
                    continue  # Malformed filename
                tool_id = name_parts[0]
                bundle_id = name_parts[1]
                timestamp = float(name_parts[2])

                # Find implementation file
                impl_files = list(
                    self._quarantine_dir.glob(f"{spec_file.stem}.*")
                )
                impl_files = [f for f in impl_files if f.suffix != ".json"]
                if not impl_files:
                    continue

                impl_file = impl_files[0]
                impl_bytes = impl_file.read_bytes()
                impl_ext = impl_file.suffix.lstrip(".")

                version = spec.get("version", "unknown")
                quarantined.append(
                    QuarantinedTool(
                        tool_id=tool_id,
                        bundle_id=bundle_id,
                        timestamp=timestamp,
                        version=version,
                        spec_json=spec,
                        impl_data=impl_bytes,
                        impl_ext=impl_ext,
                    )
                )
            except (OSError, IOError, json.JSONDecodeError, ValueError):
                # Skip malformed entries
                continue

        return sorted(quarantined, key=lambda t: t.timestamp, reverse=True)

    def _scan_for_secrets(
        self, spec: dict[str, Any], impl_bytes: bytes, tool_id: str
    ) -> None:
        """Scan the tool for credential-shaped strings (ADR-0297-style gate).

        This is fail-closed: any credential shape in the spec or implementation
        causes the import to be rejected. Never scrub or allow it through.

        Raises:
            QuarantineError if a secret shape is detected
        """
        # Credential detector types to check for (subset from ADR-0297)
        secret_types = frozenset(
            {
                "private_key_block",
                "aws_access_key",
                "aws_secret_key",
                "github_token",
                "github_pat",
                "slack_token",
                "google_api_key",
                "prefixed_secret_key",
                "jwt",
            }
        )

        # Scan spec JSON (as string)
        spec_str = json.dumps(spec)
        try:
            findings = detect_sensitive_types(spec_str)
            # Filter for credential types only (ignore other PII like emails)
            for finding_name in findings:
                if any(st in finding_name for st in secret_types):
                    raise QuarantineError(
                        f"tool {tool_id}: credential shape detected in spec ({finding_name})"
                    )
        except PIIDetectionFailedClosed as exc:
            raise QuarantineError(
                f"tool {tool_id}: PII detector error during spec scan"
            ) from exc

        # Scan implementation (try to decode as UTF-8, skip if binary)
        try:
            impl_str = impl_bytes.decode("utf-8", errors="ignore")
            findings = detect_sensitive_types(impl_str)
            for finding_name in findings:
                if any(st in finding_name for st in secret_types):
                    raise QuarantineError(
                        f"tool {tool_id}: credential shape detected in implementation ({finding_name})"
                    )
        except PIIDetectionFailedClosed as exc:
            raise QuarantineError(
                f"tool {tool_id}: PII detector error during implementation scan"
            ) from exc
