"""Compliance Manifest loader and validator (ADR-0056, ADR-0060).

Loads the machine-readable compliance rules from compliance/*.yaml, verifies
the GPG signature, checks each rule's implementation references, and emits a
structured result consumed by bridge.sh doctor (via self_test.py) and the
corvin-compliance-check CLI.

NOT a runtime oracle — this module is called only from bridge.sh doctor and
the GitHub Actions CI pipeline.  Never import from hot code paths.

Audit allow-list:
  manifest_version, sig_valid, rules_checked, rules_passed,
  rules_warned, rules_failed.
  Never: rule text, file paths, error messages, Haiku output.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ── Constants ─────────────────────────────────────────────────────────────────

MANIFEST_DIR_ENV = "CORVIN_COMPLIANCE_MANIFEST_DIR"

# Relative to repo root — resolved lazily
_DEFAULT_MANIFEST_SUBDIR = "compliance"

# Multi-framework registry (ADR-0060)
_FRAMEWORK_RULE_FILES: dict[str, tuple[str, ...]] = {
    "eu-ai-act":   ("eu-ai-act.yaml",),
    "gdpr":        ("gdpr.yaml",),
    "iso-42001":   ("iso-42001.yaml",),
    "nist-ai-rmf": ("nist-ai-rmf.yaml",),
}
_DEFAULT_FRAMEWORKS = ("eu-ai-act", "gdpr")
_ALL_FRAMEWORKS = tuple(_FRAMEWORK_RULE_FILES.keys())

# Backward-compat alias — callers that reference _RULE_FILES directly continue to work
_RULE_FILES = ("eu-ai-act.yaml", "gdpr.yaml")

_SIG_FILE = "manifest.sig"
_VERSION_FILE = "manifest-version.txt"

SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

_VALID_RULE_SEVERITY = {SEVERITY_CRITICAL, SEVERITY_WARNING, SEVERITY_INFO}

# Width used for the framework label column in human output
_FRAMEWORK_LABEL_WIDTH = 12

# ── Result types ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RuleCheckResult:
    rule_id: str
    severity: str
    status: str        # "pass" | "warn" | "fail"
    detail: str = ""
    framework: str = ""  # ADR-0060: which framework this rule belongs to

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ManifestCheckResult:
    manifest_version: str = "unknown"
    sig_status: str = "unknown"   # "valid" | "invalid" | "missing" | "skipped"
    sig_valid: bool | None = None
    rules_checked: int = 0
    rules_passed: int = 0
    rules_warned: int = 0
    rules_failed: int = 0
    rule_results: list[RuleCheckResult] = field(default_factory=list)
    load_error: str = ""

    @property
    def ok(self) -> bool:
        return (
            self.load_error == ""
            and self.sig_status != "invalid"
            and self.rules_failed == 0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_version": self.manifest_version,
            "sig_status": self.sig_status,
            "sig_valid": self.sig_valid,
            "rules_checked": self.rules_checked,
            "rules_passed": self.rules_passed,
            "rules_warned": self.rules_warned,
            "rules_failed": self.rules_failed,
            "load_error": self.load_error,
            # framework field is included in each rule result via RuleCheckResult.to_dict()
            "rule_results": [r.to_dict() for r in self.rule_results],
        }

    def audit_dict(self) -> dict[str, Any]:
        """Subset safe for the L16 audit chain (allow-list from ADR-0056)."""
        return {
            "manifest_version": self.manifest_version,
            "sig_valid": self.sig_valid,
            "rules_checked": self.rules_checked,
            "rules_passed": self.rules_passed,
            "rules_warned": self.rules_warned,
            "rules_failed": self.rules_failed,
        }


# ── Path helpers ──────────────────────────────────────────────────────────────


def _repo_root() -> Path:
    env = os.environ.get("CORVIN_REPO_ROOT")
    if env:
        return Path(env)
    # __file__ = operator/bridges/shared/compliance_manifest.py  → 3 parents up
    return Path(__file__).resolve().parents[3]


def resolve_manifest_dir() -> Path:
    env = os.environ.get(MANIFEST_DIR_ENV)
    if env:
        return Path(env)
    return _repo_root() / _DEFAULT_MANIFEST_SUBDIR


# ── YAML loading (stdlib-only fallback) ──────────────────────────────────────


def _load_yaml(path: Path) -> Any:
    """Load YAML using PyYAML if available, else raise ImportError."""
    try:
        import yaml  # type: ignore

        with path.open() as fh:
            return yaml.safe_load(fh)
    except ImportError:
        raise ImportError(
            "PyYAML is required for compliance_manifest.  "
            "Run: pip install pyyaml"
        )


# ── GPG signature verification ───────────────────────────────────────────────


def _verify_signature(
    manifest_dir: Path,
    *,
    signer_fingerprint: str | None = None,
    files_to_sign: list[str] | None = None,
) -> tuple[bool, str]:
    """Return (ok, detail).

    Signs the sha256 digest of the given rule files (plus the version file),
    matching what sign.sh produces.  Returns (False, reason) without raising.

    Args:
        manifest_dir: directory that contains the YAML files and manifest.sig.
        signer_fingerprint: optional GPG key fingerprint to check against.
        files_to_sign: list of YAML filenames (not paths) to include in the
            digest.  Defaults to all files in _RULE_FILES for backward compat.
    """
    sig_file = manifest_dir / _SIG_FILE
    if not sig_file.exists():
        return False, "manifest.sig not found"

    # Resolve to absolute paths so sha256sum output matches what sign.sh
    # produced (sign.sh always runs with SCRIPT_DIR as absolute path).
    manifest_dir = manifest_dir.resolve()

    # Default: sign all framework files (matches sign.sh MANIFEST_FILES).
    # _RULE_FILES is a 2-file backward-compat alias; _verify_signature must
    # cover ALL frameworks so the sha256sum input matches what sign.sh wrote.
    all_framework_files = [
        f for files in _FRAMEWORK_RULE_FILES.values() for f in files
    ]
    yaml_files = files_to_sign if files_to_sign is not None else all_framework_files
    rule_paths = [manifest_dir / f for f in yaml_files]
    version_path = manifest_dir / _VERSION_FILE
    all_paths = [p for p in [*rule_paths, version_path] if p.exists()]

    try:
        sha_proc = subprocess.run(
            ["sha256sum", *[str(p) for p in all_paths]],
            capture_output=True, text=True, timeout=10,
        )
        if sha_proc.returncode != 0:
            return False, "sha256sum failed"

        gpg_cmd = ["gpg", "--verify", str(sig_file), "-"]
        if signer_fingerprint:
            gpg_cmd = ["gpg", "--verify",
                       f"--trusted-key={signer_fingerprint}", str(sig_file), "-"]

        gpg_proc = subprocess.run(
            gpg_cmd,
            input=sha_proc.stdout,
            capture_output=True, text=True, timeout=10,
        )
        if gpg_proc.returncode == 0:
            return True, "signature valid"
        return False, f"gpg exit {gpg_proc.returncode}"
    except FileNotFoundError:
        return False, "gpg binary not found — signature check skipped"
    except subprocess.TimeoutExpired:
        return False, "gpg verification timed out"
    except Exception as exc:  # noqa: BLE001
        return False, f"verification error: {type(exc).__name__}"


# ── Rule validation ───────────────────────────────────────────────────────────


def _check_rule(
    rule: dict[str, Any],
    manifest_dir: Path,
    *,
    framework: str = "",
) -> RuleCheckResult:
    rule_id = rule.get("id", "<no-id>")
    severity_str = str(rule.get("severity", SEVERITY_WARNING)).lower()
    if severity_str not in _VALID_RULE_SEVERITY:
        severity_str = SEVERITY_WARNING

    impl_entries = rule.get("implemented_by", [])
    if not impl_entries:
        return RuleCheckResult(
            rule_id=rule_id,
            severity=severity_str,
            status="warn",
            detail="no implemented_by entries — rule is unimplemented",
            framework=framework,
        )

    missing_tests: list[str] = []
    for entry in impl_entries:
        test_ref = entry.get("test", "")
        if not test_ref:
            missing_tests.append(f"layer={entry.get('layer', '?')} has no test_ref")
            continue
        # test_ref is relative to repo root
        test_path = _repo_root() / test_ref
        if not test_path.exists():
            missing_tests.append(f"{test_ref} NOT FOUND")

    if missing_tests:
        return RuleCheckResult(
            rule_id=rule_id,
            severity=severity_str,
            status="warn",
            detail="; ".join(missing_tests),
            framework=framework,
        )

    return RuleCheckResult(
        rule_id=rule_id,
        severity=severity_str,
        status="pass",
        detail="",
        framework=framework,
    )


# ── Public API ────────────────────────────────────────────────────────────────


def run_compliance_check(
    manifest_dir: Path | None = None,
    *,
    verify_sig: bool = True,
    signer_fingerprint: str | None = None,
    tenant_config: dict[str, Any] | None = None,
    frameworks: tuple[str, ...] | None = None,
) -> ManifestCheckResult:
    """Load the manifest and validate all rules.

    Args:
        manifest_dir: path to compliance/ directory.  Resolved from env /
            repo root when None.
        verify_sig: whether to verify GPG signature.
        signer_fingerprint: expected GPG key fingerprint; overrides
            tenant_config value if both are given.
        tenant_config: parsed tenant.corvin.yaml spec.compliance_manifest
            section.  Used to read min_version + signer_fingerprint when
            not overridden by arguments.
        frameworks: tuple of framework names to check (e.g.
            ``("eu-ai-act", "gdpr")``).  When None, defaults to
            ``_DEFAULT_FRAMEWORKS`` (eu-ai-act + gdpr).  Pass
            ``_ALL_FRAMEWORKS`` to check every registered framework.

    Returns:
        ManifestCheckResult with structured findings.  Never raises.
    """
    result = ManifestCheckResult()

    try:
        if manifest_dir is None:
            manifest_dir = resolve_manifest_dir()

        # Determine which frameworks to load
        active_frameworks: tuple[str, ...] = (
            frameworks if frameworks is not None else _DEFAULT_FRAMEWORKS
        )

        # Collect the YAML filenames for the active frameworks
        yaml_files_to_load: list[tuple[str, str]] = []  # (framework, filename)
        for fw in active_frameworks:
            fw_files = _FRAMEWORK_RULE_FILES.get(fw)
            if fw_files is None:
                # Unknown framework — warn but don't crash (ADR-0060 graceful handling)
                import warnings
                warnings.warn(
                    f"compliance_manifest: unknown framework '{fw}' — skipped",
                    stacklevel=2,
                )
                continue
            for fname in fw_files:
                fpath = manifest_dir / fname
                if not fpath.exists():
                    import warnings
                    warnings.warn(
                        f"compliance_manifest: rule file '{fname}' for framework "
                        f"'{fw}' not found — skipped",
                        stacklevel=2,
                    )
                    continue
                yaml_files_to_load.append((fw, fname))

        # ── Tenant config overrides ──────────────────────────────────────
        cfg = tenant_config or {}
        min_version = cfg.get("min_version")
        require_sig = cfg.get("require_signature", False)
        fp = signer_fingerprint or cfg.get("signer_fingerprint")

        # ── Version ──────────────────────────────────────────────────────
        version_path = manifest_dir / _VERSION_FILE
        if version_path.exists():
            result.manifest_version = version_path.read_text().strip()
        else:
            result.manifest_version = "unknown"

        if min_version and result.manifest_version != "unknown":
            if _version_lt(result.manifest_version, min_version):
                result.load_error = (
                    f"manifest version {result.manifest_version} < "
                    f"required {min_version}"
                )
                return result

        # ── GPG signature ────────────────────────────────────────────────
        # The signature covers the files that are actually loaded so that
        # partial framework checks remain verifiable.
        loaded_yaml_filenames = [fname for _, fname in yaml_files_to_load]

        if verify_sig:
            sig_path = manifest_dir / _SIG_FILE
            if not sig_path.exists():
                result.sig_status = "missing"
                result.sig_valid = None
                # Missing sig is WARNING unless require_signature is set
                if require_sig:
                    result.load_error = (
                        "manifest.sig missing and require_signature=true in tenant config"
                    )
                    return result
            else:
                # Always verify against ALL framework files — sign.sh covers
                # every file regardless of which frameworks are "active" for
                # rule checking.  Partial-framework rule checks are independent
                # of the signature scope.
                ok, detail = _verify_signature(
                    manifest_dir,
                    signer_fingerprint=fp,
                    files_to_sign=None,
                )
                if "not found" in detail and "gpg binary" in detail:
                    result.sig_status = "skipped"
                    result.sig_valid = None
                elif ok:
                    result.sig_status = "valid"
                    result.sig_valid = True
                elif not fp and "gpg exit 2" in detail:
                    # No signer fingerprint configured + GPG exit 2 = key not in
                    # keyring, cannot verify. Treat as skipped (WARNING, not CRITICAL).
                    result.sig_status = "skipped"
                    result.sig_valid = None
                else:
                    result.sig_status = "invalid"
                    result.sig_valid = False
                    result.load_error = f"GPG verification failed: {detail}"
                    return result
        else:
            result.sig_status = "skipped"
            result.sig_valid = None

        # ── Load and validate rules ──────────────────────────────────────
        rule_results: list[RuleCheckResult] = []
        for fw, fname in yaml_files_to_load:
            fpath = manifest_dir / fname
            data = _load_yaml(fpath)
            if isinstance(data, dict):
                for rule in data.get("rules", []):
                    rule_results.append(_check_rule(rule, manifest_dir, framework=fw))

        result.rules_checked = len(rule_results)
        result.rules_passed = sum(1 for r in rule_results if r.status == "pass")
        result.rules_warned = sum(1 for r in rule_results if r.status == "warn")
        result.rules_failed = sum(1 for r in rule_results if r.status == "fail")
        result.rule_results = rule_results

    except Exception as exc:  # noqa: BLE001
        result.load_error = f"{type(exc).__name__}: {exc}"

    return result


# ── Version comparison (no semver dep) ───────────────────────────────────────


def _version_lt(a: str, b: str) -> bool:
    def _parts(v: str) -> tuple[int, ...]:
        try:
            return tuple(int(x) for x in re.split(r"[.\-]", v) if x.isdigit())
        except Exception:
            return (0,)

    return _parts(a) < _parts(b)


# ── Human-readable output ─────────────────────────────────────────────────────


def _icon(r: RuleCheckResult) -> str:
    if r.status == "pass":
        return "✓"
    if r.status == "warn":
        return "⚠"
    return "✗"


def _has_multiple_frameworks(results: list[RuleCheckResult]) -> bool:
    """Return True when results span more than one framework."""
    seen: set[str] = set()
    for r in results:
        if r.framework:
            seen.add(r.framework)
        if len(seen) > 1:
            return True
    return False


def format_human(result: ManifestCheckResult) -> str:
    lines: list[str] = []
    lines.append(
        f"Compliance Manifest v{result.manifest_version}  "
        f"(sig: {result.sig_status.upper()})"
    )
    lines.append("")

    multi_fw = _has_multiple_frameworks(result.rule_results)

    for r in result.rule_results:
        icon = _icon(r)
        detail = f"  — {r.detail}" if r.detail else ""
        if multi_fw and r.framework:
            fw_label = r.framework[:_FRAMEWORK_LABEL_WIDTH].ljust(_FRAMEWORK_LABEL_WIDTH)
            lines.append(
                f"  {icon} {fw_label}  {r.rule_id:<42} [{r.severity}]{detail}"
            )
        else:
            lines.append(f"  {icon} {r.rule_id:<42} [{r.severity}]{detail}")

    lines.append("")
    if result.load_error:
        lines.append(f"ERROR: {result.load_error}")
    elif result.ok:
        lines.append(
            f"Result: PASS  "
            f"({result.rules_passed} passed"
            + (f", {result.rules_warned} warned" if result.rules_warned else "")
            + ")"
        )
    else:
        lines.append(
            f"Result: FAIL  "
            f"({result.rules_failed} failed, "
            f"{result.rules_warned} warned, "
            f"{result.rules_passed} passed)"
        )
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="corvin-compliance-check",
        description="Validate the Corvin compliance manifest (ADR-0056, ADR-0060).",
    )
    parser.add_argument(
        "--manifest-dir",
        help=f"Path to compliance/ directory (default: repo root/{_DEFAULT_MANIFEST_SUBDIR})",
    )
    parser.add_argument(
        "--no-sig",
        action="store_true",
        help="Skip GPG signature verification",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_out",
        help="Emit JSON instead of human-readable output",
    )
    parser.add_argument(
        "--layer",
        help="Filter output to rules for a specific layer (e.g. L19)",
    )
    parser.add_argument(
        "--framework",
        metavar="FRAMEWORK",
        help=(
            "Check only rules for this framework "
            f"(choices: {', '.join(_ALL_FRAMEWORKS)})"
        ),
    )
    parser.add_argument(
        "--all-frameworks",
        action="store_true",
        help="Check rules for all four frameworks (eu-ai-act, gdpr, iso-42001, nist-ai-rmf)",
    )
    args = parser.parse_args()

    # Resolve which frameworks to check
    if args.all_frameworks:
        frameworks: tuple[str, ...] = _ALL_FRAMEWORKS
    elif args.framework:
        fw = args.framework.lower()
        if fw not in _FRAMEWORK_RULE_FILES:
            print(
                f"error: unknown framework '{fw}'.  "
                f"Valid choices: {', '.join(_ALL_FRAMEWORKS)}",
                file=sys.stderr,
            )
            sys.exit(2)
        frameworks = (fw,)
    else:
        frameworks = _DEFAULT_FRAMEWORKS

    manifest_dir = Path(args.manifest_dir) if args.manifest_dir else None
    result = run_compliance_check(
        manifest_dir,
        verify_sig=not args.no_sig,
        frameworks=frameworks,
    )

    if args.layer:
        layer_filter = args.layer.upper()
        result.rule_results = [
            r for r in result.rule_results
            if layer_filter.lower() in r.rule_id.lower()
            or any(
                layer_filter in str(e.get("layer", "")).upper()
                for rule in []  # filtered already above via rule_id
                for e in []
            )
        ]

    if args.json_out:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(format_human(result))

    sys.exit(0 if result.ok else 1)


if __name__ == "__main__":
    _cli()
