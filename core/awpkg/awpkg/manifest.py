"""AWPKG manifest parsing and JSON Schema validation."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import jsonschema  # type: ignore[import]
    _HAS_JSONSCHEMA = True
except ImportError:
    _HAS_JSONSCHEMA = False

try:
    import yaml  # type: ignore[import]
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


_SCHEMA_PATH = Path(__file__).parent.parent / "schema" / "manifest.v1.json"
_TOOL_NAME_RE = re.compile(r"^code\.[a-z0-9_]+$")
_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+")


@dataclass
class AgentSpec:
    """Per-agent declaration: tools, skills, and instruction block."""
    description: str
    instructions: str
    tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)


class ManifestError(ValueError):
    """Raised when a manifest fails validation."""


@dataclass
class Manifest:
    awpkg: str
    id: str
    name: str
    version: str
    description: str
    author: str = ""
    license: str = ""
    homepage: str = ""
    min_corvin_version: str = ""
    max_corvin_version: str | None = None
    components: dict[str, list[str]] = field(default_factory=dict)
    permissions: dict[str, Any] = field(default_factory=dict)
    dependencies: list[dict[str, str]] = field(default_factory=list)
    signature: dict[str, str] | None = None
    workflow_description: str = ""
    ascii_chart: str = ""
    agents: dict[str, AgentSpec] = field(default_factory=dict)

    @property
    def all_component_paths(self) -> list[str]:
        paths: list[str] = []
        for lst in self.components.values():
            paths.extend(lst)
        return paths

    @property
    def network_allowed(self) -> bool:
        return bool(self.permissions.get("network", False))

    @property
    def compute_allowed(self) -> bool:
        return bool(self.permissions.get("compute", False))

    @property
    def required_secrets(self) -> list[str]:
        return list(self.permissions.get("secrets", []))


def _load_schema() -> dict[str, Any]:
    with open(_SCHEMA_PATH, encoding="utf-8") as fh:
        return json.load(fh)


# ── Signature verification (ADR-0141-style provenance for awpkg) ────────────
#
# A package MAY carry a `signature` block (algorithm=ed25519, public_key and
# value both base64url). The value is an Ed25519 signature over the SHA-256
# digest of the manifest's canonical JSON *with the signature field removed*
# (so the signature never covers itself). This gives installer.py a verifiable
# provenance signal it uses to gate arbitrary `code`-node execution (WF-A1).


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def manifest_signing_digest(raw: dict[str, Any]) -> bytes:
    """SHA-256 over the canonical JSON of the manifest with `signature` removed.
    This is the exact byte string an Ed25519 signature must be produced over."""
    body = {k: v for k, v in raw.items() if k != "signature"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).digest()


def verify_manifest_signature(raw: dict[str, Any]) -> bool:
    """Return True iff `raw` carries a well-formed ed25519 signature that
    verifies against the manifest signing digest. Returns False for any missing
    field, unsupported algorithm, bad encoding, verification failure, or when
    the `cryptography` backend is unavailable — i.e. fail-closed: an
    unverifiable package is treated as unsigned by the caller."""
    sig = raw.get("signature")
    if not isinstance(sig, dict):
        return False
    if sig.get("algorithm") != "ed25519":
        return False
    pub_b64 = sig.get("public_key")
    val_b64 = sig.get("value")
    if not isinstance(pub_b64, str) or not isinstance(val_b64, str):
        return False
    try:
        from cryptography.hazmat.primitives.serialization import load_der_public_key
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature
    except ImportError:
        return False  # no crypto backend → cannot verify → fail closed
    try:
        pub = load_der_public_key(_b64url_decode(pub_b64))
        if not isinstance(pub, Ed25519PublicKey):
            return False
        pub.verify(_b64url_decode(val_b64), manifest_signing_digest(raw))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def parse_raw(raw: dict[str, Any]) -> Manifest:
    """Validate *raw* against the JSON Schema and return a Manifest."""
    if _HAS_JSONSCHEMA:
        schema = _load_schema()
        validator = jsonschema.Draft7Validator(schema)
        errors = sorted(validator.iter_errors(raw), key=lambda e: list(e.path))
        if errors:
            msgs = "; ".join(e.message for e in errors[:3])
            raise ManifestError(f"manifest schema invalid: {msgs}")
    else:
        _minimal_validate(raw)

    comps = raw.get("components", {})
    if not any(comps.values()):
        raise ManifestError("components must contain at least one non-empty list")

    agents: dict[str, AgentSpec] = {}
    for agent_id, spec in raw.get("agents", {}).items():
        agents[agent_id] = AgentSpec(
            description=spec.get("description", ""),
            instructions=spec["instructions"],
            tools=list(spec.get("tools", [])),
            skills=list(spec.get("skills", [])),
        )

    return Manifest(
        awpkg=raw["awpkg"],
        id=raw["id"],
        name=raw["name"],
        version=raw["version"],
        description=raw["description"],
        author=raw.get("author", ""),
        license=raw.get("license", ""),
        homepage=raw.get("homepage", ""),
        min_corvin_version=raw.get("min_corvin_version", ""),
        max_corvin_version=raw.get("max_corvin_version"),
        components={k: list(v) for k, v in comps.items() if v},
        permissions=dict(raw.get("permissions", {})),
        dependencies=list(raw.get("dependencies", [])),
        signature=raw.get("signature"),
        workflow_description=raw.get("workflow_description", ""),
        ascii_chart=raw.get("ascii_chart", ""),
        agents=agents,
    )


def parse_bytes(data: bytes) -> Manifest:
    if _HAS_YAML:
        raw = yaml.safe_load(data.decode("utf-8"))
    else:
        raw = _naive_yaml_parse(data.decode("utf-8"))
    if not isinstance(raw, dict):
        raise ManifestError("manifest.yaml must be a YAML mapping")
    return parse_raw(raw)


def validate_tool_names(
    manifest: Manifest,
    tool_contents: dict[str, bytes] | None = None,
) -> list[str]:
    """Return list of forge tool paths that violate the code.* naming rule.

    When tool_contents is provided (archive path → raw bytes), the `name`
    field from the JSON is validated.  Otherwise the filename stem is used
    as a cheap fallback (e.g. during inspect without extraction).
    """
    import json as _json
    bad: list[str] = []
    for path in manifest.components.get("forge_tools", []):
        if tool_contents and path in tool_contents:
            try:
                name = _json.loads(tool_contents[path]).get("name", "")
            except Exception:
                name = Path(path).stem
        else:
            name = Path(path).stem
        if not _TOOL_NAME_RE.match(name):
            bad.append(path)
    return bad


def _minimal_validate(raw: dict[str, Any]) -> None:
    """Fallback validator when jsonschema is not installed."""
    required = ("awpkg", "id", "name", "version", "description", "components")
    for key in required:
        if key not in raw:
            raise ManifestError(f"manifest missing required field: {key!r}")
    if raw["awpkg"] != "1.0":
        raise ManifestError(f"unsupported awpkg version: {raw['awpkg']!r}")
    if not re.match(r"^[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)+$", str(raw["id"])):
        raise ManifestError(f"manifest.id invalid: {raw['id']!r}")
    if not _SEMVER_RE.match(str(raw["version"])):
        raise ManifestError(f"manifest.version not semver: {raw['version']!r}")


def _naive_yaml_parse(text: str) -> dict[str, Any]:
    """Extremely limited YAML parser — scalar strings and lists only.
    Only used when PyYAML is absent (which should not happen in production)."""
    result: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list[str] | None = None
    for line in text.splitlines():
        stripped = line.rstrip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("  - ") or stripped.startswith("- "):
            val = stripped.lstrip(" -").strip().strip('"').strip("'")
            if current_list is not None:
                current_list.append(val)
        elif ":" in stripped and not stripped.startswith(" "):
            k, _, v = stripped.partition(":")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if v:
                result[k] = v
                current_key = k
                current_list = None
            else:
                current_key = k
                current_list = None
        elif stripped.startswith("  ") and current_key and ":" in stripped:
            k, _, v = stripped.strip().partition(":")
            v = v.strip().strip('"').strip("'")
            if isinstance(result.get(current_key), dict):
                result[current_key][k] = v
            else:
                result[current_key] = {k: v}
    return result
