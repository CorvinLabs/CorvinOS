"""Operator-controlled data policy.

Loads ``<corvin_home>/global/data_policy.yaml`` (or a path override)
into a strongly-typed Python object. The same shape is consumed by
``redactor.apply_redaction`` and the MCP ``data_register`` /
``data_snapshot`` tools.

Schema (ADR-0012 §B):

```yaml
apiVersion: corvin/v1
kind: DataPolicy
spec:
  pii_backend: regex+headers          # regex+headers | presidio
  default_strategy: redact
  class_strategies:                   # per-PII-class default
    email: pseudonymize
    iban: drop
  column_overrides:                   # per-column wins on conflict
    customer_email: pseudonymize
    notes: aggregate_only
  column_pii_class:                   # operator-tagged free-text columns
    notes: name
  noise:
    rowcount_jitter: 5
    rowcount_jitter_threshold: 100
    distinct_jitter: 3
    extremes: p05_p95
  strict_mode: false                  # fail-closed on unknown column types
```

PyYAML is the runtime dependency. When PyYAML is unavailable, the
loader falls back to JSON files (``data_policy.json``) so operators
on minimal Python envs can still use a policy file.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .redactor import STRATEGIES, RedactionError, RedactionPolicy


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class NoiseConfig:
    """Snapshot-level noising knobs (mirrors SnapshotOptions)."""

    rowcount_jitter:           int = 5
    rowcount_jitter_threshold: int = 100
    distinct_jitter:           int = 3
    extremes:                  str = "p05_p95"   # "p05_p95" | "min_max" (operator opt-in)


@dataclass
class DataPolicy:
    """Top-level operator policy."""

    api_version:        str = "corvin/v1"
    kind:               str = "DataPolicy"
    pii_backend:        str = "regex+headers"     # regex+headers | presidio
    default_strategy:   str = "redact"
    class_strategies:   dict[str, str] = field(default_factory=dict)
    column_overrides:   dict[str, str] = field(default_factory=dict)
    column_pii_class:   dict[str, str] = field(default_factory=dict)
    noise:              NoiseConfig    = field(default_factory=NoiseConfig)
    strict_mode:        bool = False
    # ADR-0012 §E — operator-configurable prompt-token cap for the
    # snapshot payload. Triggers data.snapshot_oversized + falls back
    # to schema-only when the redacted snapshot's estimated token
    # count exceeds the cap. ~4 chars / token heuristic.
    snapshot_token_cap: int = 4_000
    # ADR-0023 Layer 32 — strict-anonymisation snapshot mode.
    # When True, every snapshot payload returned to the LLM is reduced
    # to a zero-value structural projection (no sample rows, no quantiles,
    # distinct counts bucketised to k-anonymity classes, rowcount
    # Laplace-noised) AND post-scanned for PII regex leakage.
    # Default OFF (Layer-24 behaviour preserved). Operators with
    # delegate-heavy tenants or untrusted worker engines should
    # explicitly opt in via data_policy.yaml.
    strict_anonymization:    bool  = False
    k_anonymity_threshold:   int   = 5
    rowcount_laplace_scale:  float = 1.0
    reject_on_pii_leak:      bool  = True

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        if self.api_version != "corvin/v1":
            raise PolicyError(f"unsupported apiVersion: {self.api_version!r}")
        if self.kind != "DataPolicy":
            raise PolicyError(f"unsupported kind: {self.kind!r}")
        if self.pii_backend not in {"regex+headers", "presidio"}:
            raise PolicyError(
                f"pii_backend must be one of "
                f"['regex+headers', 'presidio']; got {self.pii_backend!r}"
            )
        if self.default_strategy not in STRATEGIES:
            raise PolicyError(
                f"default_strategy {self.default_strategy!r} not in {STRATEGIES}"
            )
        for k, v in self.class_strategies.items():
            if v not in STRATEGIES:
                raise PolicyError(
                    f"class_strategies[{k!r}] = {v!r} not in {STRATEGIES}"
                )
        for k, v in self.column_overrides.items():
            if v not in STRATEGIES:
                raise PolicyError(
                    f"column_overrides[{k!r}] = {v!r} not in {STRATEGIES}"
                )
        if not isinstance(self.snapshot_token_cap, int) or self.snapshot_token_cap < 100:
            raise PolicyError(
                f"snapshot_token_cap must be int >= 100; got "
                f"{self.snapshot_token_cap!r}"
            )
        # ADR-0023 validations — fail-loud at policy-load time so a
        # misconfigured strict-mode tenant doesn't silently run
        # unprotected.
        if not isinstance(self.strict_anonymization, bool):
            raise PolicyError(
                f"strict_anonymization must be bool; got "
                f"{self.strict_anonymization!r}"
            )
        if (not isinstance(self.k_anonymity_threshold, int)
                or self.k_anonymity_threshold < 2):
            raise PolicyError(
                f"k_anonymity_threshold must be int >= 2; got "
                f"{self.k_anonymity_threshold!r}"
            )
        if (not isinstance(self.rowcount_laplace_scale, (int, float))
                or self.rowcount_laplace_scale < 0):
            raise PolicyError(
                f"rowcount_laplace_scale must be non-negative number; got "
                f"{self.rowcount_laplace_scale!r}"
            )
        if not isinstance(self.reject_on_pii_leak, bool):
            raise PolicyError(
                f"reject_on_pii_leak must be bool; got "
                f"{self.reject_on_pii_leak!r}"
            )

    def to_redaction_policy(self) -> RedactionPolicy:
        """Project the operator's full policy onto the
        redaction-time subset."""
        return RedactionPolicy(
            default_strategy=self.default_strategy,
            class_strategies=dict(self.class_strategies),
            column_overrides=dict(self.column_overrides),
            column_pii_class=dict(self.column_pii_class),
        )


class PolicyError(ValueError):
    """Raised on malformed policy file / unsupported apiVersion etc."""


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_policy(path: str | Path | None = None) -> DataPolicy:
    """Load policy from a file path or return defaults.

    Resolution:
      1. *path* explicit → load that file.
      2. ``CORVIN_DATA_POLICY`` env var → load that file.
      3. ``<corvin_home>/global/data_policy.yaml`` → load if present.
      4. ``<corvin_home>/global/data_policy.json`` → fallback.
      5. None of the above → return ``DataPolicy()`` (defaults).
    """
    import os

    candidates: list[Path] = []
    if path:
        candidates.append(Path(path))
    else:
        env_p = os.environ.get("CORVIN_DATA_POLICY")
        if env_p:
            candidates.append(Path(env_p))
        corvin_home = os.environ.get("CORVIN_HOME")
        if corvin_home:
            base = Path(corvin_home) / "global"
            candidates.append(base / "data_policy.yaml")
            candidates.append(base / "data_policy.json")

    for p in candidates:
        if p.is_file():
            return _load_from_file(p)

    return DataPolicy()


def _load_from_file(path: Path) -> DataPolicy:
    import stat as _stat
    _mode = _stat.S_IMODE(path.stat().st_mode)
    if _mode & 0o077:  # group or world readable/writable
        raise PolicyError(
            f"{path}: unsafe file permissions {oct(_mode)} — "
            "data_policy file must be mode 0600 or more restrictive"
        )
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")

    if suffix in (".json",):
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PolicyError(f"{path}: invalid JSON: {exc}") from exc
    elif suffix in (".yaml", ".yml"):
        raw = _load_yaml(text, path)
    else:
        # Try YAML first, then JSON
        try:
            raw = _load_yaml(text, path)
        except PolicyError:
            try:
                raw = json.loads(text)
            except json.JSONDecodeError as exc:
                raise PolicyError(
                    f"{path}: could not parse as YAML or JSON"
                ) from exc

    return _from_raw(raw, path)


def _load_yaml(text: str, path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as exc:
        raise PolicyError(
            f"{path}: YAML loading requires PyYAML. Install via "
            f"`pip install pyyaml`, or rename to .json and use JSON."
        ) from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:  # type: ignore[attr-defined]
        raise PolicyError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise PolicyError(f"{path}: top-level YAML must be a mapping")
    return raw


def _from_raw(raw: dict[str, Any], path: Path) -> DataPolicy:
    """Project a parsed YAML/JSON dict onto the DataPolicy dataclass."""
    if not isinstance(raw, dict):
        raise PolicyError(f"{path}: top-level value must be a mapping")

    spec = raw.get("spec", {})
    if not isinstance(spec, dict):
        raise PolicyError(f"{path}: spec must be a mapping")

    noise_raw = spec.get("noise", {}) or {}
    if not isinstance(noise_raw, dict):
        raise PolicyError(f"{path}: spec.noise must be a mapping")

    try:
        noise = NoiseConfig(
            rowcount_jitter=int(noise_raw.get("rowcount_jitter", 5)),
            rowcount_jitter_threshold=int(
                noise_raw.get("rowcount_jitter_threshold", 100)
            ),
            distinct_jitter=int(noise_raw.get("distinct_jitter", 3)),
            extremes=str(noise_raw.get("extremes", "p05_p95")),
        )
    except (TypeError, ValueError) as exc:
        raise PolicyError(f"{path}: spec.noise has invalid types: {exc}") from exc

    return DataPolicy(
        api_version=str(raw.get("apiVersion", "corvin/v1")),
        kind=str(raw.get("kind", "DataPolicy")),
        pii_backend=str(spec.get("pii_backend", "regex+headers")),
        default_strategy=str(spec.get("default_strategy", "redact")),
        class_strategies=dict(spec.get("class_strategies", {}) or {}),
        column_overrides=dict(spec.get("column_overrides", {}) or {}),
        column_pii_class=dict(spec.get("column_pii_class", {}) or {}),
        noise=noise,
        strict_mode=bool(spec.get("strict_mode", False)),
        snapshot_token_cap=int(spec.get("snapshot_token_cap", 4_000)),
        # ADR-0023 Layer 32 — strict-anonymisation projection.
        strict_anonymization=bool(spec.get("strict_anonymization", False)),
        k_anonymity_threshold=int(spec.get("k_anonymity_threshold", 5)),
        rowcount_laplace_scale=float(spec.get("rowcount_laplace_scale", 1.0)),
        reject_on_pii_leak=bool(spec.get("reject_on_pii_leak", True)),
    )
