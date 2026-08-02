"""Persistent skill registry — markdown + meta.json on disk.

Workspace layout (per scope root, e.g. <scope_root>/skill-forge/):

    <root>/
    ├── skills_registry.json     manifest of all skills in this scope
    ├── skills/<name>/
    │   ├── SKILL.md             body with YAML front-matter
    │   └── meta.json            {sha256, created_at, ..., grades:[]}
    └── (audit lives ONE LEVEL UP at <scope_root>/audit.jsonl —
         shared with the forge plugin's audit trail.)
"""
from __future__ import annotations

import contextlib
try:
    import fcntl
except ImportError:  # Windows — POSIX advisory locks unavailable; degrade to no-op
    import types as _types
    fcntl = _types.SimpleNamespace(  # type: ignore[assignment]
        LOCK_SH=1, LOCK_EX=2, LOCK_NB=4, LOCK_UN=8,
        flock=lambda *a, **k: None, lockf=lambda *a, **k: None,
    )
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .linter import lint, LintResult


def _yaml_quote(s: str) -> str:
    """Quote a string safely for YAML embedded in front-matter.

    Handles special chars, newlines, and colon-prefixed values.
    """
    if not s:
        return "''"
    # If contains special chars or starts with special markers, use single-quoted form.
    if any(c in s for c in '\n:@#|>&*![]{}') or s.startswith(('-', '?', '!')):
        # Escape single quotes inside the string.
        escaped = s.replace("'", "''")
        return f"'{escaped}'"
    return s


# Forge ships with the hash-chain audit writer. Reuse it so SkillForge and
# Forge share one verifiable chain per scope_root. Insert the forge package
# into sys.path lazily; if it's missing we fall back to a JSONL append-only
# writer so the registry still works in standalone tests.
def _import_forge_audit():
    # registry.py lives at <plugins>/skill-forge/skill_forge/registry.py
    # We want the forge plugin's TOP DIR on sys.path so that
    # ``from forge.security_events import ...`` resolves to its
    # security_events submodule.
    plugins_dir = Path(__file__).resolve().parents[2]    # /plugins
    forge_top = plugins_dir / "forge"                    # /operator/forge
    if forge_top.is_dir() and str(forge_top) not in sys.path:
        sys.path.insert(0, str(forge_top))
    try:
        from forge.security_events import write_event, verify_chain
        return write_event, verify_chain
    except ImportError:
        return None, None


_write_event, _verify_chain = _import_forge_audit()


# -- plugin-slot mirror ------------------------------------------------------
#
# Every successful create() also lands a stripped-down SKILL.md in the
# plugin-source ``skills/dyn/<sanitized>/`` directory so the *next* claude
# subprocess discovers the dynamic skill via the engine's plugin-skill loader.
# The slot is gitignored — dynamic skills never land in a commit.
#
# The slot is per-repo (not per-scope). When the same skill exists in
# multiple scopes, the slot reflects whichever scope wrote last; promote()
# in MultiSkillRegistry re-writes after the source-side delete to make the
# higher scope "win".


def plugin_slot_dir() -> Path:
    """Resolve the plugin-source ``dyn/`` directory for slot mirrors.

    Resolution order:
      1. ``CORVIN_PLUGIN_SLOT_DIR`` env override — the ONLY test-isolation
         signal for this function. Every test in this codebase that
         exercises create()/delete() already sets this explicitly (see
         docs/claude-ref/layer-plugins.md's "Test isolation" note) — it is a
         dedicated, single-purpose variable used nowhere else, so its mere
         presence is an unambiguous "redirect me" signal on its own.
      2. Walk up from this file's location for a ``.corvin_repo``/``plugins/``
         marker → ``<repo>/operator/skill-forge/skills/dyn/`` — the REAL
         production path. Confirmed by `test_engine_visibility.py`'s actual
         `claude -p` subprocess run: the native engine's plugin-skill loader
         only ever sees skills mirrored here, not anywhere CORVIN_HOME-derived.
      3. Fallback ``~/.corvin/plugin-slot/`` (no repo marker found — e.g. a
         pip-installed wheel with no `plugins/` directory on disk).

    2026-08-02 fix (adversarial review of the Concept Gate mechanism, found
    while verifying a freshly-seeded SkillForge skill's actual reachability):
    a prior branch here treated bare ``CORVIN_HOME`` presence as a
    test-sandbox signal and redirected the slot to ``<CORVIN_HOME>/
    plugin-slot/`` too — but CORVIN_HOME is the canonical runtime root set in
    EVERY real CorvinOS session (CLAUDE.md: "Canonical runtime root:
    ~/.corvin/"), not just tests. That branch therefore silently redirected
    every real production install's plugin-slot mirror away from the path
    the native engine actually scans, making every freshly created
    project/user-scope skill invisible to Claude Code's own plugin loader —
    the exact "looks wired, isn't reachable" failure class this codebase's
    own `e2e-wiring-proof` skill exists to catch, just one level deeper than
    that skill's own file-tree checks would find. Removed the CORVIN_HOME
    branch entirely rather than gating it behind a second flag (e.g.
    CORVIN_TEST_MODE): every existing test already sets the more specific
    CORVIN_PLUGIN_SLOT_DIR directly (verified across all 15 call sites before
    this change), so removing it changes zero test behavior while fixing
    production. See Corvin-ADR/concepts/0001-self-learning-project-concept-
    archive.md's Production-Readiness Roadmap, item P0-1, for the fuller
    writeup and the two stale test assertions this change corrects in
    test_plugin_slot_compat.py.
    """
    env = os.environ.get("CORVIN_PLUGIN_SLOT_DIR")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    # Walk up from this file looking for the plugins/ marker — same heuristic
    # as forge.paths.corvin_home, but we don't import it to keep registry.py
    # standalone.
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
            return parent / "operator" / "skill-forge" / "skills" / "dyn"
    return Path.home() / ".corvin" / "plugin-slot"


def _sanitize_slot_name(name: str) -> str:
    """Map a dotted skill name to an undottered slot directory name.

    The engine prefers undottered skill names; we replace ``.`` with ``_``.
    All other characters are already constrained by SkillRegistry.create()'s
    validator (alnum + ``.`` + ``_``).
    """
    return name.replace(".", "_")


def _render_slot_md(sanitized_name: str, description: str, body_md: str) -> str:
    """Render the engine-facing SKILL.md — only ``name`` + ``description`` in
    the front-matter, then the source body verbatim.

    The canonical SKILL.md (with ``claim``, ``type``, ``references``) lives
    in the scope workspace. The slot is a projection that the engine can
    consume without being confused by SkillForge-specific keys.
    """
    body_md = body_md.lstrip()
    if not body_md.endswith("\n"):
        body_md += "\n"
    fm = (
        "---\n"
        f"name: {sanitized_name}\n"
        f"description: {description}\n"
        "---\n\n"
    )
    return fm + body_md


def _write_slot(name: str, description: str, body_md: str) -> None:
    """Mirror ``body_md`` into the plugin slot under a sanitized directory.

    Idempotent — overwrites any prior slot for the same sanitized name.
    """
    slot_dir = plugin_slot_dir() / _sanitize_slot_name(name)
    slot_dir.mkdir(parents=True, exist_ok=True)
    SkillRegistry._atomic_write_text(  # type: ignore[attr-defined]
        slot_dir / "SKILL.md",
        _render_slot_md(_sanitize_slot_name(name), description, body_md),
    )


def _purge_slot(name: str) -> None:
    """Remove the slot directory for ``name`` if it exists."""
    slot_dir = plugin_slot_dir() / _sanitize_slot_name(name)
    if slot_dir.exists():
        shutil.rmtree(slot_dir, ignore_errors=True)


class LinterError(Exception):
    """Linter rejected the skill body — see ``violations`` for details."""

    def __init__(self, violations: list[str]):
        super().__init__("linter rejected skill body: " + "; ".join(violations))
        self.violations = list(violations)


class PromotionGateError(Exception):
    """Promotion gate refused the requested move."""


@dataclass
class Grade:
    run_id: str
    score: float
    ts: float
    notes: str = ""


@dataclass
class SkillSpec:
    name: str
    type: str  # domain | persona-style | repo-context | learned-experience
    description: str
    claim: dict[str, Any]
    scope: str = "session"
    created_at: float = field(default_factory=time.time)
    created_by: str = ""
    sha256: str = ""
    grades: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SkillSpec":
        # tolerate legacy/extra fields
        known = {f for f in cls.__dataclass_fields__}
        kept = {k: v for k, v in d.items() if k in known}
        return cls(**kept)

    @property
    def n_grades(self) -> int:
        return len(self.grades)

    @property
    def mean_score(self) -> float:
        if not self.grades:
            return 0.0
        return sum(g.get("score", 0.0) for g in self.grades) / len(self.grades)


VALID_TYPES = ("domain", "persona-style", "repo-context", "learned-experience")


class SkillRegistry:
    MANIFEST_NAME = "skills_registry.json"
    SKILLS_DIR = "skills"

    # The audit lives ONE LEVEL UP from the SkillForge workspace so the
    # hash-chain is shared with forge in the same scope_root. The caller
    # passes a ``root`` like ``<scope_root>/skill-forge/`` and the audit
    # ends up at ``<scope_root>/audit.jsonl``.
    AUDIT_NAME = "audit.jsonl"

    def __init__(self, root: Path, *, hash_chain: bool = True):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / self.SKILLS_DIR).mkdir(exist_ok=True)
        self.manifest_path = self.root / self.MANIFEST_NAME
        self.lock_path = self.root / ".lock"
        self.hash_chain = hash_chain
        if not self.manifest_path.exists():
            self._atomic_write_text(self.manifest_path, "{}\n")

    # -- locking + atomic IO ----------------------------------------------

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @staticmethod
    def _atomic_write_text(path: Path, text: str) -> None:
        directory = path.parent
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(directory))
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    # -- manifest IO -------------------------------------------------------

    def _load(self) -> dict[str, dict]:
        try:
            text = self.manifest_path.read_text() or "{}"
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"skill registry manifest corrupted at "
                f"{self.manifest_path}: {e}"
            ) from e

    def _save(self, data: dict[str, dict]) -> None:
        self._atomic_write_text(
            self.manifest_path, json.dumps(data, indent=2) + "\n"
        )

    # -- public API --------------------------------------------------------

    def list(self) -> list[SkillSpec]:
        return [SkillSpec.from_dict(v) for v in self._load().values()]

    def get(self, name: str) -> SkillSpec | None:
        d = self._load().get(name)
        return SkillSpec.from_dict(d) if d else None

    def get_body(self, name: str) -> str | None:
        skill_dir = self.root / self.SKILLS_DIR / name
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            return None
        return skill_md.read_text()

    # ADR-0052 F9 — content hash binding ─────────────────────────────────────

    @staticmethod
    def _full_sha256(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def bind_content_hash(self, name: str) -> str | None:
        """Compute and store the full SHA-256 of the current SKILL.md.

        Called at promotion time. Returns the hex digest, or None if the
        skill does not exist.
        """
        body = self.get_body(name)
        if body is None:
            return None
        h = self._full_sha256(body)
        with self._locked():
            data = self._load()
            if name not in data:
                return None
            data[name]["content_hash_sha256"] = h
            self._save(data)
            # Also update meta.json
            skill_dir = self.root / self.SKILLS_DIR / name
            meta_path = skill_dir / "meta.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text())
                    meta["content_hash_sha256"] = h
                    self._atomic_write_text(meta_path, json.dumps(meta, indent=2) + "\n")
                except Exception:
                    pass
        return h

    def get_body_verified(self, name: str) -> tuple[str | None, str]:
        """Return (body, status) where status is one of:
        'ok'           — hash present and matches
        'no_hash'      — no stored hash yet (pre-ADR-0052 skill)
        'drift'        — hash mismatch detected
        'missing'      — SKILL.md does not exist

        On 'drift': emits skill_forge.content_drift WARNING. If re-lint
        passes, updates stored hash and emits skill_forge.content_rehash.
        If re-lint fails, does NOT update hash and emits
        skill_forge.injection_suspended CRITICAL — returns (None, 'suspended').
        """
        spec = self.get(name)
        if spec is None:
            return None, "missing"
        body = self.get_body(name)
        if body is None:
            return None, "missing"

        stored_hash = spec.meta.get("content_hash_sha256") if isinstance(spec.meta, dict) else None
        if stored_hash is None:
            # Fall back to manifest field (populated by bind_content_hash)
            d = self._load().get(name, {})
            stored_hash = d.get("content_hash_sha256")

        if stored_hash is None:
            return body, "no_hash"

        current_hash = self._full_sha256(body)
        if current_hash == stored_hash:
            return body, "ok"

        # Hash mismatch — emit drift event
        self._audit_event(
            "skill_forge.content_drift",
            severity="WARNING",
            details={"skill_name": name, "scope": spec.scope},
        )

        # Re-lint
        lint_result = lint(body)
        if not lint_result.ok:
            self._audit_event(
                "skill_forge.injection_suspended",
                severity="CRITICAL",
                details={"skill_name": name, "scope": spec.scope,
                         "lint_errors": lint_result.errors[:5]},
            )
            return None, "suspended"

        # Linter passed — rehash and continue
        self.bind_content_hash(name)
        self._audit_event(
            "skill_forge.content_rehash",
            severity="INFO",
            details={"skill_name": name, "scope": spec.scope},
        )
        return body, "drift_revalidated"

    def _audit_event(
        self, event_type: str, *, severity: str = "INFO", details: dict | None = None
    ) -> None:
        """Emit a standalone audit event (not tied to a SkillSpec)."""
        path = self.audit_path()
        if _write_event is not None:
            try:
                _write_event(
                    path, event_type,
                    severity=severity,
                    tool="",
                    details=details or {},
                    hash_chain=self.hash_chain,
                )
                return
            except OSError:
                pass
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(json.dumps({
                "ts": time.time(),
                "event_type": event_type,
                "severity": severity,
                "details": details or {},
            }) + "\n")

    def create(
        self,
        *,
        name: str,
        type: str,
        body_md: str,
        description: str,
        claim: dict[str, Any] | None = None,
        scope: str = "session",
        overwrite: bool = False,
        created_by: str = "",
    ) -> SkillSpec:
        # Name validation — same shape as forge.registry.create
        if not name or len(name) > 128:
            raise ValueError(f"skill name must be 1..128 chars: {name!r}")
        if "/" in name or ".." in name \
                or name.startswith(".") or name.endswith("."):
            raise ValueError(f"skill name has illegal sequence: {name!r}")
        if not all(c.isalnum() or c in "._" for c in name):
            raise ValueError(
                f"skill name must be alphanumeric + . + _ : {name!r}"
            )
        if type not in VALID_TYPES:
            raise ValueError(
                f"unsupported skill type: {type!r} (valid: {VALID_TYPES})"
            )

        # Linter — fail-closed: violations block the write
        result = lint(body_md)
        if not result.ok:
            raise LinterError(result.errors)

        with self._locked():
            data = self._load()
            if name in data and not overwrite:
                raise FileExistsError(
                    f"skill {name!r} already exists (use overwrite=True)"
                )

            skill_dir = self.root / self.SKILLS_DIR / name
            skill_dir.mkdir(parents=True, exist_ok=True)

            sha = hashlib.sha256(body_md.encode("utf-8")).hexdigest()[:16]
            spec = SkillSpec(
                name=name,
                type=type,
                description=description,
                claim=claim or {},
                scope=scope,
                created_by=created_by or os.environ.get("SKILL_FORGE_PERSONA", ""),
                sha256=sha,
                grades=[],
            )

            # Write SKILL.md atomically with front-matter, then meta.json
            full_md = _render_skill_md(spec, body_md)
            self._atomic_write_text(skill_dir / "SKILL.md", full_md)
            self._atomic_write_text(
                skill_dir / "meta.json",
                json.dumps(asdict(spec), indent=2) + "\n",
            )

            data[name] = asdict(spec)
            self._save(data)
            # Mirror to plugin slot so the next claude subprocess discovers
            # it. Layer-16 v2 scope-gate: only project- and user-scope skills
            # land in the engine plugin tree. Task/session skills stay
            # reachable in their origin chat via adapter-injection but
            # cannot leak across chats through the engine plugin loader.
            # Slot writes are best-effort — a slot failure must not
            # invalidate the canonical workspace write that already
            # happened.
            if scope in ("project", "user"):
                try:
                    _write_slot(name, description, body_md)
                except OSError:
                    pass
            self._audit("skill.create", spec)
            # ADR-0052 F9 — bind content hash at creation (baseline for drift detection)
            # Hash the rendered full_md, not body_md, so get_body_verified()
            # can compare against the same bytes that live in SKILL.md.
            try:
                data[name]["content_hash_sha256"] = self._full_sha256(full_md)
                self._save(data)
            except Exception:
                pass
            return spec

    def delete(
        self, name: str, *, reason: str = "", purge_slot: bool = True,
    ) -> bool:
        """Remove a skill from disk + manifest. Returns True if it existed.

        ``purge_slot=False`` keeps the plugin-slot mirror — used by
        MultiSkillRegistry.promote() so the higher-scope copy keeps its
        slot when the lower-scope source is dropped.
        """
        with self._locked():
            data = self._load()
            d = data.pop(name, None)
            if d is None:
                return False
            spec = SkillSpec.from_dict(d)
            skill_dir = self.root / self.SKILLS_DIR / name
            if skill_dir.exists():
                shutil.rmtree(skill_dir, ignore_errors=False)
            self._save(data)
            if purge_slot:
                try:
                    _purge_slot(name)
                except OSError:
                    pass
            self._audit("skill.delete", spec, extra={"reason": reason})
            return True

    def grade(
        self, name: str, run_id: str, score: float, *, notes: str = ""
    ) -> SkillSpec:
        """Append a grade to a skill's history. Score in [0.0, 1.0]."""
        if not (0.0 <= score <= 1.0):
            raise ValueError(f"score must be in [0,1], got {score!r}")
        with self._locked():
            data = self._load()
            if name not in data:
                raise KeyError(name)
            grades = list(data[name].get("grades") or [])
            grades.append(asdict(Grade(
                run_id=run_id, score=float(score), ts=time.time(), notes=notes
            )))
            data[name]["grades"] = grades
            self._save(data)

            # Persist into meta.json too so it stays human-readable
            skill_dir = self.root / self.SKILLS_DIR / name
            meta_path = skill_dir / "meta.json"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text())
                meta["grades"] = grades
                self._atomic_write_text(
                    meta_path, json.dumps(meta, indent=2) + "\n"
                )

            spec = SkillSpec.from_dict(data[name])
            self._audit(
                "skill.grade", spec,
                extra={"run_id": run_id, "score": float(score)},
            )
            return spec

    # -- audit -------------------------------------------------------------

    def audit_path(self) -> Path:
        """Shared audit lives ONE LEVEL UP, alongside (sibling to) the
        forge workspace — so both plugins extend the same hash-chain."""
        return self.root.parent / self.AUDIT_NAME

    def _audit(
        self,
        action: str,
        spec: SkillSpec,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        details: dict[str, Any] = {
            "sha":   spec.sha256,
            "scope": spec.scope,
            "type":  spec.type,
        }
        persona = os.environ.get("SKILL_FORGE_PERSONA", "")
        if persona:
            details["persona"] = persona
        # Layer 9 — caller-persona attribution. The bridge adapter exports
        # CORVIN_CALLER_PERSONA per turn so the audit chain shows which
        # cowork persona created / graded / promoted / deleted a skill.
        # Empty value means the call ran without an attached persona
        # (CLI use).
        caller_persona = os.environ.get("CORVIN_CALLER_PERSONA") or ""
        if caller_persona:
            details["caller_persona"] = caller_persona
        if extra:
            details.update(extra)

        path = self.audit_path()
        if _write_event is not None:
            try:
                _write_event(
                    path, action,
                    tool=spec.name,
                    details=details,
                    hash_chain=self.hash_chain,
                )
                return
            except OSError:
                pass
        # Fallback when forge isn't on PYTHONPATH at all.
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(json.dumps({
                "ts": time.time(),
                "event_type": action,
                "tool": spec.name,
                "details": details,
            }) + "\n")


def _render_skill_md(spec: SkillSpec, body_md: str) -> str:
    """Render a SkillSpec + body into a SKILL.md with YAML front-matter.

    The body may already start with ``---``-fenced front-matter; if it does
    we trust the caller and pass it through. Otherwise we emit the canonical
    block.
    """
    body_md = body_md.lstrip()
    if body_md.startswith("---"):
        return body_md if body_md.endswith("\n") else body_md + "\n"
    fm_lines = [
        "---",
        f"name: {_yaml_quote(spec.name)}",
        f"type: {_yaml_quote(spec.type)}",
        f"description: {_yaml_quote(spec.description)}",
        "claim:",
    ]
    for k, v in (spec.claim or {}).items():
        fm_lines.append(f"  {_yaml_quote(str(k))}: {_yaml_quote(str(v))}")
    fm_lines.append("references: []")
    fm_lines.append("---")
    fm_lines.append("")
    fm = "\n".join(fm_lines) + "\n"
    if not body_md.endswith("\n"):
        body_md += "\n"
    return fm + body_md
