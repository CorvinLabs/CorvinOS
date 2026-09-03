"""OS-Skill Manager: Install, activate, execute skills.

Security contract (adversarial review 2026-09-03):

* ``install_skill`` validates ``manifest.name`` / ``manifest.version`` before
  they touch a path and refuses any destination that resolves outside the
  tenant skills dir — ``name: ../../../escaped`` used to ``rmtree`` and
  ``copytree`` three levels above the tenant.
* ``SkillRegistry.get_skill_path`` binds the traversal guard to the STORED
  path: a ``registry.yaml`` entry pointing outside the tenant dir is refused,
  never returned.
* ``registry.yaml`` read-modify-write runs under ``SkillRegistry._lock``.
* ``SkillExecutor.execute`` enforces ``timeout_ms`` with a daemon-thread join
  (same model as ``skill_registry_phase1``), applies the manifest's
  ``sanitization.disallow_fields`` BEFORE inputs are persisted to
  ``run_state.json``, and emits a metadata-only ``skill.executed`` event to the
  tenant core audit chain (``core.skills.skill_audit``).
* ``SkillManager.execute_skill(trigger)`` resolves the skill by the trigger
  name declared in its manifest — nothing is hard-coded.
"""

import re
import shutil
import threading
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from datetime import datetime
import logging
import time

from core.skills.skill_audit import emit_skill_audit

logger = logging.getLogger(__name__)

# Manifest identifiers are path components: lowercase, dotted, no separators.
SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
SKILL_VERSION_RE = re.compile(r"^[0-9a-zA-Z][0-9a-zA-Z._-]*$")


def _valid_component(value: Any, pattern: re.Pattern[str], max_len: int = 128) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= max_len
        and ".." not in value
        and pattern.match(value) is not None
    )


@dataclass
class ExecutionResult:
    success: bool
    output: Optional[Dict[str, Any]] = None
    errors: Optional[List[str]] = None
    phase_completed: int = 0
    run_id: Optional[str] = None


@dataclass
class SkillStatus:
    skill_id: str
    enabled: bool
    version: str
    score: Optional[float] = None
    runs_24h: int = 0
    errors_24h: int = 0


def _load_manifest_file(manifest_file: Path) -> Dict[str, Any]:
    import yaml

    with open(manifest_file) as f:
        content = f.read()
    if content.startswith('---'):
        content = content.split('---', 2)[2]
    data = yaml.safe_load(content)
    return data if isinstance(data, dict) else {}


def _manifest_triggers(manifest: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    for trig in manifest.get('triggers') or []:
        if isinstance(trig, dict) and isinstance(trig.get('name'), str):
            names.append(trig['name'])
        elif isinstance(trig, str):
            names.append(trig)
    return names


def _manifest_disallow_fields(manifest: Dict[str, Any]) -> List[str]:
    """``sanitization.disallow_fields`` — top level or under ``learning_signal``."""
    for container in (manifest, manifest.get('learning_signal') or {}):
        san = container.get('sanitization') if isinstance(container, dict) else None
        if isinstance(san, dict):
            fields = san.get('disallow_fields') or []
            return [f for f in fields if isinstance(f, str)]
    return []


class SkillRegistry:
    """Maintain registry of installed skills (tenant-scoped)."""

    def __init__(self, registry_path: Path, tenant_id: str = None, corvin_home: Optional[Path] = None):
        self._lock = threading.Lock()
        self.registry_path = registry_path
        self.tenant_id = tenant_id
        self.corvin_home = corvin_home
        # Note: tenant_id passed for context; actual isolation via registry_path scoping
        self.registry_file = registry_path / 'registry.yaml'
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_registry()

    def _ensure_registry(self):
        with self._lock:
            if not self.registry_file.exists():
                self.registry_file.write_text("skills: {}\n")

    def _read(self) -> Dict[str, Any]:
        import yaml

        with open(self.registry_file) as f:
            registry = yaml.safe_load(f) or {}
        if not isinstance(registry, dict) or not isinstance(registry.get('skills'), dict):
            registry = {'skills': {}}
        return registry

    def _write(self, registry: Dict[str, Any]) -> None:
        import yaml

        tmp = self.registry_file.with_suffix('.tmp')
        with open(tmp, 'w') as f:
            yaml.dump(registry, f)
        tmp.replace(self.registry_file)

    def register_skill(
        self,
        skill_id: str,
        version: str,
        path: str,
        enabled: bool = True,
        triggers: Optional[List[str]] = None,
    ):
        """Register skill in registry (locked read-modify-write)."""
        with self._lock:
            registry = self._read()
            entry: Dict[str, Any] = {
                'version': version,
                'path': path,
                'enabled': enabled,
                'loaded_at': datetime.utcnow().isoformat(),
            }
            if triggers is not None:
                entry['triggers'] = list(triggers)
            registry['skills'][skill_id] = entry
            self._write(registry)

    def list_skills(self) -> Dict[str, Dict[str, Any]]:
        """All registry entries keyed by skill_id (a copy)."""
        with self._lock:
            return dict(self._read()['skills'])

    def get_entry(self, skill_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._read()['skills'].get(skill_id)

    def _resolve_inside(self, skill_id: str, stored: Any) -> Optional[Path]:
        resolved = Path(str(stored)).resolve()
        root = self.registry_path.resolve()
        if not resolved.is_relative_to(root):
            logger.error(
                "Refusing skill path outside tenant skills dir (traversal?): %s not in %s",
                resolved, root,
            )
            if self.tenant_id:
                emit_skill_audit(
                    self.tenant_id, "skill.path_refused", tool=skill_id,
                    details={"skill_id": skill_id, "reason": "outside_tenant_skills_dir"},
                    severity="WARNING", corvin_home=self.corvin_home,
                )
            return None
        return resolved

    def get_skill_path(self, skill_id: str, version: Optional[str] = None) -> Optional[Path]:
        """Get skill path by ID and optional version.

        The path recorded in ``registry.yaml`` is the value an attacker who can
        write that file controls, so THAT is what the traversal check binds:
        a stored path outside the tenant skills dir is refused, not returned.
        """
        skill_info = self.get_entry(skill_id)
        if skill_info is None:
            return None
        if version and skill_info.get('version') != version:
            return None
        return self._resolve_inside(skill_id, skill_info.get('path', ''))

    def find_by_trigger(self, trigger: str) -> Optional[str]:
        """skill_id of the first ENABLED skill declaring ``trigger``.

        Entries registered without a ``triggers`` list are resolved from their
        manifest on disk (only inside the tenant skills dir).
        """
        for skill_id, entry in self.list_skills().items():
            if not entry.get('enabled', True):
                continue
            triggers = entry.get('triggers')
            if triggers is None:
                path = self._resolve_inside(skill_id, entry.get('path', ''))
                manifest_file = path / 'manifest.yaml' if path else None
                if manifest_file is None or not manifest_file.exists():
                    continue
                try:
                    triggers = _manifest_triggers(_load_manifest_file(manifest_file))
                except Exception as exc:  # noqa: BLE001 — one bad manifest must not hide the rest
                    logger.warning("manifest unreadable for %s: %s", skill_id, exc)
                    continue
            if trigger in triggers:
                return skill_id
        return None


class SkillExecutor:
    """Execute skill phases with gating and timeout enforcement."""

    PHASE_TIMEOUTS_MS = {
        0: 100,    # Intake
        1: 500,    # Context
        2: 2000,   # Clarify
        3: 3000,   # Plan
        4: 5000,   # Execution start
        5: 5000,   # Execution mid
        6: 5000,   # Execution end
        7: 500,    # Feedback
        8: 1000,   # Validation
        9: 200,    # Output
        10: 200,   # Return
        11: 0,     # Grading (async)
    }

    def __init__(self, skill_dir: Path, tenant_id: Optional[str] = None, corvin_home: Optional[Path] = None):
        self._lock = threading.Lock()
        self.skill_dir = skill_dir
        self.tenant_id = tenant_id
        self.corvin_home = corvin_home
        self.manifest = self._load_manifest()

    def _load_manifest(self) -> Dict[str, Any]:
        return _load_manifest_file(self.skill_dir / 'manifest.yaml')

    def sanitize_inputs(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Drop the manifest's ``sanitization.disallow_fields`` before persistence."""
        disallowed = set(_manifest_disallow_fields(self.manifest))
        if not disallowed:
            return dict(inputs)
        return {k: v for k, v in inputs.items() if k not in disallowed}

    def _audit(self, event_type: str, run_id: Optional[str], details: Dict[str, Any]) -> None:
        if not self.tenant_id:
            return
        emit_skill_audit(
            self.tenant_id, event_type,
            tool=str(self.manifest.get('name', '')), run_id=run_id or "",
            details=details, corvin_home=self.corvin_home,
        )

    def execute(
        self,
        inputs: Dict[str, Any],
        timeout_ms: int = 5000,
        trigger: str = 'before_delegation_decision',
    ) -> ExecutionResult:
        """Execute skill phases 0-10 on a worker thread, bounded by ``timeout_ms``.

        A run that overruns is reported as a failure and abandoned (daemon
        thread — it cannot block interpreter exit). Only metadata is audited:
        skill_id, version, status, latency_ms, run_id — never inputs/outputs.
        """
        from core.skills.state_manager import StateManager

        skill_id = str(self.manifest.get('name', ''))
        version = str(self.manifest.get('version', ''))
        state_mgr = StateManager(self.skill_dir)
        safe_inputs = self.sanitize_inputs(inputs)
        run_id = state_mgr.start_run(
            skill_id=skill_id,
            version=version,
            trigger=trigger,
            inputs=safe_inputs,
        ).run_id

        started = time.monotonic()
        holder: Dict[str, Any] = {}

        def _runner() -> None:
            try:
                holder['result'] = self._run_phases(state_mgr, run_id, safe_inputs)
            except BaseException as exc:  # noqa: BLE001 — surfaced on the caller side
                holder['exc'] = exc

        worker = threading.Thread(target=_runner, name=f"skill:{skill_id}", daemon=True)
        worker.start()
        worker.join(timeout=max(int(timeout_ms), 0) / 1000.0)
        latency_ms = round((time.monotonic() - started) * 1000, 3)

        if worker.is_alive():
            self._audit("skill.executed", run_id, {
                "skill_id": skill_id, "skill_version": version, "status": "timeout",
                "latency_ms": latency_ms, "run_id": run_id, "timeout_ms": int(timeout_ms),
            })
            return ExecutionResult(
                success=False,
                errors=[f"timeout after {timeout_ms}ms"],
                phase_completed=0,
                run_id=run_id,
            )

        if 'exc' in holder:
            exc = holder['exc']
            logger.error("skill %s run %s failed: %s", skill_id, run_id, exc)
            self._audit("skill.executed", run_id, {
                "skill_id": skill_id, "skill_version": version, "status": "failure",
                "latency_ms": latency_ms, "run_id": run_id, "exc_type": type(exc).__name__,
            })
            return ExecutionResult(success=False, errors=[str(exc)], phase_completed=0, run_id=run_id)

        result: ExecutionResult = holder['result']
        self._audit("skill.executed", run_id, {
            "skill_id": skill_id, "skill_version": version,
            "status": "success" if result.success else "failure",
            "latency_ms": latency_ms, "run_id": run_id,
            "phase_completed": result.phase_completed,
        })
        return result

    def _run_phases(self, state_mgr, run_id: str, inputs: Dict[str, Any]) -> ExecutionResult:
        # Phase 0: Intake (input validation)
        try:
            state_mgr.commit_phase_output(run_id, 0, {'validated': True})
        except Exception as e:
            logger.error(f"Phase 0 failed: {e}")
            return ExecutionResult(success=False, errors=[str(e)], phase_completed=0, run_id=run_id)

        # Phase 3: Plan + Decision (hard-coded for MVP)
        decision = self._make_decision(inputs)
        state_mgr.commit_phase_output(run_id, 3, decision)

        # Phase 9-10: Output validation
        output = {
            'decision': decision['decision'],
            'confidence': decision['confidence'],
            'reasoning': decision['reasoning']
        }

        state_mgr.commit_phase_output(run_id, 10, output)

        return ExecutionResult(
            success=True,
            output=output,
            phase_completed=10,
            run_id=run_id
        )

    def _make_decision(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Hard-coded MVP decision logic."""
        task_shape = inputs.get('task_shape', 'small_code')

        # MVP heuristic: big_data -> ACS, else native
        if task_shape == 'big_data':
            return {
                'decision': 'acs',
                'confidence': 0.75,
                'reasoning': 'big_data workload -> delegate to ACS for cost efficiency'
            }
        else:
            return {
                'decision': 'native',
                'confidence': 0.80,
                'reasoning': f'{task_shape} workload -> keep native for latency'
            }


class SkillManager:
    """Manage skill lifecycle."""

    def __init__(self, corvin_home: Path, tenant_id: str):
        self._lock = threading.Lock()
        self.corvin_home = corvin_home
        self.tenant_id = tenant_id
        # ADR-0007: Tenant-scoped skills directory
        self.skills_dir = corvin_home / 'tenants' / tenant_id / 'skills'
        self.registry = SkillRegistry(self.skills_dir, tenant_id=tenant_id, corvin_home=corvin_home)

    def install_skill(self, skill_bundle: Path) -> Dict[str, Any]:
        """Install skill from a directory bundle into the tenant skills dir."""
        if not skill_bundle.exists():
            return {'success': False, 'error': f'Skill bundle not found: {skill_bundle}'}

        manifest_file = skill_bundle / 'manifest.yaml'
        if not manifest_file.exists():
            return {'success': False, 'error': 'manifest.yaml not found'}

        try:
            manifest = _load_manifest_file(manifest_file)
        except Exception as exc:  # noqa: BLE001 — unreadable manifest == refused install
            return {'success': False, 'error': f'manifest.yaml unreadable: {type(exc).__name__}'}

        skill_id = manifest.get('name')
        version = manifest.get('version')
        if not skill_id or not version:
            return {'success': False, 'error': 'manifest.name or manifest.version missing'}
        version = str(version)

        # The identifiers become path components: validate BEFORE any join.
        if not _valid_component(skill_id, SKILL_NAME_RE):
            return {'success': False, 'error': f'manifest.name rejected: {skill_id!r}'}
        if not _valid_component(version, SKILL_VERSION_RE, max_len=64):
            return {'success': False, 'error': f'manifest.version rejected: {version!r}'}

        root = self.skills_dir.resolve()
        dest = (self.skills_dir / f"{skill_id}_v{version}")
        if dest.resolve().parent != root or not dest.resolve().is_relative_to(root):
            logger.error("install refused: destination escapes tenant skills dir: %s", dest)
            emit_skill_audit(
                self.tenant_id, "skill.install_refused", tool=str(skill_id),
                details={"skill_id": str(skill_id), "reason": "destination_outside_tenant_skills_dir"},
                severity="WARNING", corvin_home=self.corvin_home,
            )
            return {'success': False, 'error': 'destination escapes tenant skills dir'}

        with self._lock:
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(skill_bundle, dest)
            self.registry.register_skill(
                skill_id, version, str(dest), enabled=True,
                triggers=_manifest_triggers(manifest),
            )

        emit_skill_audit(
            self.tenant_id, "skill.installed", tool=skill_id,
            details={"skill_id": skill_id, "skill_version": version,
                     "triggers": _manifest_triggers(manifest)},
            corvin_home=self.corvin_home,
        )
        logger.info(f"Installed skill {skill_id} v{version}")
        return {'success': True, 'skill_id': skill_id, 'version': version}

    def list_active_skills(self) -> List[str]:
        """skill_ids that are enabled AND whose stored path resolves inside the tenant dir."""
        active: List[str] = []
        for skill_id, entry in self.registry.list_skills().items():
            if not entry.get('enabled', True):
                continue
            if self.registry.get_skill_path(skill_id) is None:
                continue
            active.append(skill_id)
        return active

    def execute_skill(self, trigger: str, inputs: Dict[str, Any], timeout_ms: int = 5000) -> ExecutionResult:
        """Execute the enabled skill that declares ``trigger``."""
        skill_id = self.registry.find_by_trigger(trigger)
        if skill_id is None:
            return ExecutionResult(success=False, errors=[f'No enabled skill declares trigger: {trigger}'])

        skill_path = self.registry.get_skill_path(skill_id)
        if not skill_path:
            return ExecutionResult(success=False, errors=[f'Skill not found: {skill_id}'])

        executor = SkillExecutor(skill_path, tenant_id=self.tenant_id, corvin_home=self.corvin_home)
        return executor.execute(inputs, timeout_ms, trigger=trigger)

    def get_skill_status(self, skill_id: str) -> Optional[SkillStatus]:
        """Get skill status from the registry entry (path must resolve inside the tenant)."""
        entry = self.registry.get_entry(skill_id)
        if entry is None or self.registry.get_skill_path(skill_id) is None:
            return None

        # Load grading stats (TODO: implement in Phase 2)
        score = None

        return SkillStatus(
            skill_id=skill_id,
            enabled=bool(entry.get('enabled', True)),
            version=str(entry.get('version', '')),
            score=score,
            runs_24h=0,
            errors_24h=0
        )
