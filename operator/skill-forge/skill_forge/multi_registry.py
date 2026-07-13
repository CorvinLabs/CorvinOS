"""Multi-scope skill registry.

Lookup-Order on list/get: task -> session -> project -> user
(higher scope shadows lower). create() defaults to ``detect_scope()``
from forge.scope (ws_scope axis only — there is no permission-scope axis
for skills).

Promotion gates (LDD twist over forge):
  task    -> session: requires >=1 grade with score > 0
  session -> project: requires >=3 grades with mean >= 0.5
  project -> user   : requires force=True
Failed gate -> PromotionGateError with reason.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

from .registry import SkillRegistry, SkillSpec, PromotionGateError


def _import_forge_scope():
    plugins_dir = Path(__file__).resolve().parents[2]
    forge_top = plugins_dir / "forge"
    if forge_top.is_dir() and str(forge_top) not in sys.path:
        sys.path.insert(0, str(forge_top))
    from forge.scope import VALID_SCOPES, detect_scope, scope_root
    return VALID_SCOPES, detect_scope, scope_root


VALID_SCOPES, detect_scope, scope_root = _import_forge_scope()

# Promotion target -> minimum bar
_PROMOTE_GATES: dict[tuple[str, str], str] = {
    ("task", "session"):    ">=1 grade with score > 0",
    ("session", "project"): ">=3 grades and mean_score >= 0.5",
    ("project", "user"):    "operator force=True",
}


class MultiSkillRegistry:
    """Composes per-scope SkillRegistries with shadowing semantics."""

    # SkillForge workspaces are nested ONE LEVEL DEEP under each scope_root,
    # so the audit lives at the scope_root (sibling to forge/ and shared
    # with it). The directory name is "skill-forge".
    SUBDIR = "skill-forge"

    def __init__(
        self,
        *,
        channel_id: str | None = None,
        task_id: str | None = None,
        project_root: Path | None = None,
        hash_chain: bool = True,
    ):
        self._kwargs = dict(
            channel_id=channel_id,
            task_id=task_id,
            project_root=project_root,
        )
        self._hash_chain = hash_chain
        self._registries: dict[str, SkillRegistry] = {}

    # forge.scope.scope_root returns "<...>/forge" — we strip the trailing
    # forge/ and append skill-forge/ so the two plugins SHARE the parent
    # (audit.jsonl lives in the parent).
    def _root_for(self, scope: str) -> Path:
        forge_root = scope_root(scope, **self._kwargs)
        return forge_root.parent / self.SUBDIR

    def _registry(self, scope: str) -> SkillRegistry:
        if scope not in VALID_SCOPES:
            raise ValueError(
                f"unknown scope: {scope!r} (valid: {VALID_SCOPES})"
            )
        if scope not in self._registries:
            root = self._root_for(scope)
            root.mkdir(parents=True, exist_ok=True)
            self._registries[scope] = SkillRegistry(
                root, hash_chain=self._hash_chain,
            )
        return self._registries[scope]

    # -- create / get / list / delete -------------------------------------

    def create(
        self,
        *,
        scope: str | None = None,
        name: str,
        type: str,
        body_md: str,
        description: str,
        claim: dict[str, Any] | None = None,
        overwrite: bool = False,
        created_by: str = "",
    ) -> SkillSpec:
        ws_scope = scope or detect_scope()
        return self._registry(ws_scope).create(
            name=name,
            type=type,
            body_md=body_md,
            description=description,
            claim=claim,
            scope=ws_scope,
            overwrite=overwrite,
            created_by=created_by,
        )

    def get(self, name: str) -> SkillSpec | None:
        for ws_scope in VALID_SCOPES:
            spec = self._registry(ws_scope).get(name)
            if spec:
                return spec
        return None

    def get_in_scope(self, name: str, scope: str) -> SkillSpec | None:
        return self._registry(scope).get(name)

    def find_scope(self, name: str) -> str | None:
        for ws_scope in VALID_SCOPES:
            if self._registry(ws_scope).get(name):
                return ws_scope
        return None

    def get_body(self, name: str) -> str | None:
        scope = self.find_scope(name)
        if scope is None:
            return None
        return self._registry(scope).get_body(name)

    def list(self) -> list[SkillSpec]:
        seen: dict[str, SkillSpec] = {}
        for ws_scope in VALID_SCOPES:
            for spec in self._registry(ws_scope).list():
                if spec.name not in seen:
                    seen[spec.name] = spec
        return list(seen.values())

    def list_with_scope(self) -> list[tuple[str, SkillSpec]]:
        seen: dict[str, tuple[str, SkillSpec]] = {}
        for ws_scope in VALID_SCOPES:
            for spec in self._registry(ws_scope).list():
                if spec.name not in seen:
                    seen[spec.name] = (ws_scope, spec)
        return list(seen.values())

    def delete(
        self, name: str, *, scope: str | None = None, reason: str = "",
    ) -> bool:
        if scope is not None:
            return self._registry(scope).delete(name, reason=reason)
        for ws_scope in VALID_SCOPES:
            reg = self._registry(ws_scope)
            if reg.get(name):
                return reg.delete(name, reason=reason)
        return False

    def grade(
        self, name: str, run_id: str, score: float, *, notes: str = "",
    ) -> SkillSpec:
        scope = self.find_scope(name)
        if scope is None:
            raise KeyError(name)
        return self._registry(scope).grade(name, run_id, score, notes=notes)

    # -- promotion --------------------------------------------------------

    def promote(self, name: str, *, to: str, force: bool = False) -> SkillSpec:
        if to not in ("session", "project", "user"):
            raise ValueError(
                f"unknown target scope: {to!r} (valid: session|project|user)"
            )
        from_scope = self.find_scope(name)
        if from_scope is None:
            raise KeyError(f"skill not found in any scope: {name!r}")
        if from_scope == to:
            return self._registry(to).get(name)  # already there

        spec = self._registry(from_scope).get(name)
        # Evaluate gate
        self._enforce_gate(spec, from_scope, to, force=force)
        # Layer-11 dialectic gate (skill_promotion site). Heat is highest
        # for project->user (cross-session reach) and lower for the smaller
        # steps. Low n_grades means high uncertainty; mean closer to 0.5
        # also raises uncertainty (judgment call). Best-effort — silent on
        # any import / decide failure.
        self._dialectic_promote(
            spec=spec, from_scope=from_scope, to=to, force=force,
        )

        body = self._registry(from_scope).get_body(name) or ""
        # Strip front-matter if present so SkillRegistry re-renders one
        body_only = _strip_front_matter(body)

        new_spec = self._registry(to).create(
            name=spec.name,
            type=spec.type,
            body_md=body_only,
            description=spec.description,
            claim=spec.claim,
            scope=to,
            overwrite=True,
            created_by=spec.created_by,
        )
        # Carry over grades so subsequent promotion gates can see the
        # accumulated history. (forge.promote doesn't have grades — for
        # SkillForge they are the gate's only signal.)
        for g in spec.grades:
            self._registry(to).grade(
                spec.name,
                run_id=g.get("run_id", ""),
                score=float(g.get("score", 0.0)),
                notes=g.get("notes", ""),
            )
        # MOVE semantics: drop the source-scope copy so subsequent
        # find_scope() reports the new (higher) scope and the next
        # promotion gate is evaluated against the right (from, to) pair.
        # This also avoids stale shadowed copies polluting list().
        # purge_slot=False because the target-scope create() above already
        # wrote the (now authoritative) slot mirror and we don't want the
        # source-side delete to wipe it.
        self._registry(from_scope).delete(
            spec.name, reason=f"promoted to {to}", purge_slot=False,
        )
        # Re-read from target so caller gets the post-grade spec.
        return self._registry(to).get(spec.name) or new_spec

    def _dialectic_promote(
        self, *, spec: SkillSpec, from_scope: str, to: str, force: bool,
    ) -> None:
        """Best-effort dialectic decision-point for skill promotion."""
        try:
            import sys as _sys
            from pathlib import Path as _Path
            here = _Path(__file__).resolve().parent
            shared = (here.parent.parent / "voice"
                      / "bridges" / "shared")
            if str(shared) not in _sys.path:
                _sys.path.insert(0, str(shared))
            import dialectic as _dialectic  # type: ignore
        except Exception:
            return
        # Consequence ordinal:
        # task->session = 0.3 (in-session reach)
        # session->project = 0.6 (cross-session, project-bound)
        # project->user = 1.0 (cross-project, irreversible reach)
        c_map = {("task", "session"): 0.3,
                 ("session", "project"): 0.6,
                 ("project", "user"): 1.0}
        consequence = c_map.get((from_scope, to), 0.5)
        # Uncertainty: more grades → less uncertainty; mean closer to 0.5
        # raises uncertainty (judgment call); below 0.3 mean is suspect.
        n = max(1, spec.n_grades)
        u_grades = max(0.0, min(1.0, 1.0 / n))      # 1g=1.0, 5g=0.2, 10g=0.1
        u_score = 1.0 - abs(spec.mean_score - 0.5) * 2  # 0.5 → 1.0, 0/1 → 0.0
        uncertainty = max(u_grades, u_score) * (0.3 if force else 1.0)
        # Scope: 1=task, 3=project-bound, 5=user-scope.
        s_map = {"session": 2, "project": 3, "user": 5}
        scope_n = s_map.get(to, 1)
        _dialectic.decide(
            site="skill_promotion",
            thesis={"action": "promote", "name": spec.name,
                    "from": from_scope, "to": to,
                    "n_grades": spec.n_grades,
                    "mean_score": round(spec.mean_score, 3)},
            antithesis={"reason": "low-grade-count-or-borderline-mean",
                        "n_grades": spec.n_grades,
                        "mean_score": round(spec.mean_score, 3)},
            consequence=consequence,
            uncertainty=uncertainty,
            scope=scope_n,
        )

    def _enforce_gate(
        self,
        spec: SkillSpec,
        from_scope: str,
        to: str,
        *,
        force: bool,
    ) -> None:
        gate = (from_scope, to)
        # Allow shortcut multi-step promotions only if every intermediate
        # gate is met; we enforce based on the (from, to) pair directly.
        if gate not in _PROMOTE_GATES:
            # not a defined direct gate (e.g. user->task) — refuse unless force
            if not force:
                raise PromotionGateError(
                    f"undefined promotion path {from_scope}->{to}; "
                    f"use force=True to override"
                )
            return

        if gate == ("task", "session"):
            if spec.n_grades < 1 or all(
                g.get("score", 0) <= 0 for g in spec.grades
            ):
                raise PromotionGateError(
                    f"task->session needs >=1 grade with score>0 "
                    f"(have {spec.n_grades} grades)"
                )
        elif gate == ("session", "project"):
            # Defense-in-depth: grade() already rejects NaN scores, but a
            # NaN could still reach storage through another path (direct
            # meta.json edit, legacy-data migration, a future grade()
            # regression). NaN comparisons are always False, so an
            # unguarded `mean_score < 0.5` would let a corrupted skill
            # silently satisfy the gate — reject explicitly instead.
            if (
                spec.n_grades < 3
                or math.isnan(spec.mean_score)
                or spec.mean_score < 0.5
            ):
                raise PromotionGateError(
                    f"session->project needs >=3 grades and mean>=0.5 "
                    f"(have {spec.n_grades} grades, mean={spec.mean_score:.2f})"
                )
        elif gate == ("project", "user"):
            if not force:
                raise PromotionGateError(
                    "project->user requires force=True (operator-only step)"
                )


def _strip_front_matter(text: str) -> str:
    """Return body without leading ``---``-fenced YAML block, if any."""
    text = text.lstrip()
    if not text.startswith("---"):
        return text
    # find the closing ---
    rest = text[3:]
    nl_idx = rest.find("\n")
    if nl_idx < 0:
        return text
    rest = rest[nl_idx + 1:]
    # search for line-starting ---
    end = rest.find("\n---")
    if end < 0:
        return text
    after = rest[end + 4:]
    # consume the rest of that line
    nl2 = after.find("\n")
    if nl2 < 0:
        return ""
    return after[nl2 + 1:]
