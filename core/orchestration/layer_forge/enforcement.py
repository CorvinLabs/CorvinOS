"""EnforcementChecker — host-awareness validation for the MVP (ADR-2222 D3).

Compile-time Mypy boundary checks are explicitly deferred (named in ADR-2222
Consequences, not silently dropped). This module implements only the checks
the MVP ships: schema re-validation and host-awareness cross-checking.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .schema import content_hash_of_paths


@dataclass(frozen=True)
class EnforcementVerdict:
    rule_id: str
    status: str  # PASS | FAIL | SKIPPED
    detail: str = ""


class LayerHostAwarenessError(RuntimeError):
    pass


class EnforcementChecker:
    def __init__(self, repo_root: Path):
        self.repo_root = Path(repo_root)

    def check_host_awareness(self, manifest: dict) -> EnforcementVerdict:
        """Validate that source_tree paths exist, and when a runtime host root
        is configured, that runtime paths exist AND their content hash matches
        the source_tree hash. In a repo-only checkout (no runtime install),
        the runtime half is SKIPPED, never silently treated as PASS."""
        ha = manifest.get("host_awareness") or {}
        source_paths = ha.get("source_tree", {}).get("paths", [])
        runtime_paths = ha.get("runtime", {}).get("paths", [])
        cross_check = ha.get("cross_check", "none")

        if not source_paths:
            return EnforcementVerdict("host_awareness", "SKIPPED", "no host_awareness declared")

        missing = [p for p in source_paths if not (self.repo_root / p).exists()]
        if missing:
            return EnforcementVerdict(
                "host_awareness", "FAIL", f"source_tree paths missing: {missing}"
            )

        if cross_check == "none" or not runtime_paths:
            return EnforcementVerdict(
                "host_awareness", "SKIPPED", "no runtime cross-check configured"
            )

        runtime_root = Path("/opt/corvin")
        if not runtime_root.exists():
            return EnforcementVerdict(
                "host_awareness", "SKIPPED", "skipped_no_runtime_host"
            )

        runtime_missing = [p for p in runtime_paths if not (runtime_root / p).exists()]
        if runtime_missing:
            return EnforcementVerdict(
                "host_awareness", "FAIL", f"runtime paths missing: {runtime_missing}"
            )

        source_hash = content_hash_of_paths(source_paths, self.repo_root)
        runtime_hash = content_hash_of_paths(runtime_paths, runtime_root)
        if source_hash != runtime_hash:
            return EnforcementVerdict(
                "host_awareness",
                "FAIL",
                f"content mismatch: source={source_hash[:12]} runtime={runtime_hash[:12]}",
            )
        return EnforcementVerdict("host_awareness", "PASS", "source == runtime")
