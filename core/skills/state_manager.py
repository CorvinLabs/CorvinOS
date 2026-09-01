"""State persistence and recovery for skill runs."""

import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional


class StateManager:
    """Persist and recover run state."""

    def __init__(self, skill_dir: Path):
        self.skill_dir = skill_dir
        self.runs_dir = skill_dir / 'runs'
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def start_run(self, skill_id: str, version: str, trigger: str, inputs: Dict[str, Any]):
        """Create run and return RunState."""
        now = datetime.utcnow().isoformat().replace(':', '-')
        run_id = f"run_{now}"
        run_dir = self.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        state = RunState(
            run_id=run_id,
            skill_id=skill_id,
            skill_version=version,
            trigger=trigger,
            inputs=inputs,
            phase_completed=0,
            state_file=run_dir / 'run_state.json'
        )

        state.save()
        return state

    def commit_phase_output(self, run_id: str, phase: int, output: Dict[str, Any]):
        """Commit phase output atomically."""
        run_dir = self.runs_dir / run_id
        state_file = run_dir / 'run_state.json'

        with open(state_file) as f:
            state_dict = json.load(f)

        state_dict['phase_completed'] = phase
        if 'phase_output' not in state_dict:
            state_dict['phase_output'] = {}
        state_dict['phase_output'][str(phase)] = output

        # Atomic write: .tmp -> rename
        tmp_file = state_file.with_suffix('.tmp')
        with open(tmp_file, 'w') as f:
            json.dump(state_dict, f)
        tmp_file.replace(state_file)

    def load_run(self, run_id: str) -> Optional['RunState']:
        """Load run state from disk."""
        state_file = self.runs_dir / run_id / 'run_state.json'
        if not state_file.exists():
            return None

        return RunState.load(state_file)


class RunState:
    """Single run state."""

    def __init__(self, run_id: str, skill_id: str, skill_version: str,
                 trigger: str, inputs: Dict[str, Any], phase_completed: int,
                 state_file: Path):
        self.run_id = run_id
        self.skill_id = skill_id
        self.skill_version = skill_version
        self.skill_version_at_start = skill_version  # For crash recovery
        self.trigger = trigger
        self.inputs = inputs
        self.phase_completed = phase_completed
        self.phase_output: Dict[int, Dict[str, Any]] = {}
        self.state_file = state_file

    def save(self):
        """Save state atomically."""
        state_dict = {
            'run_id': self.run_id,
            'skill_id': self.skill_id,
            'skill_version': self.skill_version,
            'skill_version_at_start': self.skill_version_at_start,
            'trigger': self.trigger,
            'inputs': self.inputs,
            'phase_completed': self.phase_completed,
            'phase_output': {str(k): v for k, v in self.phase_output.items()},
        }

        tmp_file = self.state_file.with_suffix('.tmp')
        with open(tmp_file, 'w') as f:
            json.dump(state_dict, f, indent=2)
        tmp_file.replace(self.state_file)

    @staticmethod
    def load(state_file: Path) -> 'RunState':
        """Load state from file."""
        with open(state_file) as f:
            d = json.load(f)

        state = RunState(
            run_id=d['run_id'],
            skill_id=d['skill_id'],
            skill_version=d['skill_version'],
            trigger=d['trigger'],
            inputs=d['inputs'],
            phase_completed=d['phase_completed'],
            state_file=state_file
        )

        state.skill_version_at_start = d.get('skill_version_at_start', d['skill_version'])
        state.phase_output = {int(k): v for k, v in d.get('phase_output', {}).items()}

        return state
