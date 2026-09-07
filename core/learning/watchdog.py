"""
Divergence Watchdog (ADR-0625 + Fix #2: Watchdog Circumvention Mitigation)

Three-layer safeguard:
1. Bounds enforcement (immutable)
2. Divergence detection (NaN, Inf, loss explosion)
3. Conservative mode (adaptive learning rate reduction)

Security Enhancement (Fix #2):
- All checkpoints are signed with Merkle root + HMAC tenant signature
- Verification fails if checkpoint is tampered (fail-closed)
- Tenant isolation: checkpoints from one tenant cannot be used by another
"""

import math
import json
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime

from core.learning.checkpoint_signer import CheckpointSigner, CheckpointSignatureError


class DivergenceWatchdog:
    """Monitor Meta Loop state for divergence.

    This is the ONLY watchdog. A ``WatchdogIntegration`` wrapper once existed
    (deleted in 4f9c5b5d) and ``nine_d_loss`` kept importing it, which made the
    whole 9D optimizer unimportable (adversarial review F-L1). The optimizer now
    drives this class directly: ``save_checkpoint`` → ``apply_gradients`` →
    ``validate_state`` → ``restore_checkpoint`` on divergence.

    Security (Fix #2): All checkpoints are now signed with Merkle root + tenant
    signature. Restoration verifies signature; any tampering → fail-closed.
    The signing key is the tenant's SECRET 0600 key file (round-4 review, F2 —
    it used to be ``sha256("checkpoint.signer:" + tenant_id)``, i.e. public).

    ``restore_checkpoint`` deliberately reads ONLY ``self.signed_checkpoints``
    (in-process). The on-disk copies written by ``save_checkpoint`` are
    forensic: an operator can inspect what the meta loop held at step N. They
    are NOT a restore source, and must not become one without going through
    ``CheckpointSigner.verify_checkpoint`` first — that is what makes an
    on-disk forgery unreachable rather than merely unlikely. A checkpoint
    written before 2026-09-07 carries a signature made with the old PUBLIC
    derivation and will not verify; that is intended (see checkpoint_signer).
    """

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.checkpoint_count = 0
        self.last_checkpoint = None
        self.rollback_count = 0

        # Checkpoint signing (Fix #2)
        self.signer = CheckpointSigner(tenant_id)
        self.signed_checkpoints = {}  # id -> {state, merkle_root, signature}

        # Bounds (immutable)
        self.bounds = {
            'α_core': (0.001, 0.3),
            'α_infra': (0.001, 0.3),
            'damping_core': (0.8, 0.99),
            'damping_infra': (0.8, 0.99),
        }

    def validate_state(self, state: Dict[str, float]) -> bool:
        """
        Layer 2: Divergence detection.
        Returns False if divergence detected.
        """
        for param_name, (min_val, max_val) in self.bounds.items():
            value = state.get(param_name)
            if value is None:
                continue

            # Check for NaN, Inf
            if math.isnan(value) or math.isinf(value):
                print(f"ERROR: {param_name} = {value} (NaN/Inf)")
                return False

            # Check bounds
            if value < min_val or value > max_val:
                print(f"ERROR: {param_name} = {value} outside [{min_val}, {max_val}]")
                return False

        # Check loss magnitude
        loss = state.get('loss', 0.0)
        if loss > 10.0:
            print(f"ERROR: loss = {loss} (> 10x threshold)")
            return False

        return True

    def save_checkpoint(self, state: Dict[str, Any], directory: Optional[Path] = None) -> str:
        """Save state for rollback (in memory; on disk too when ``directory`` is given).

        The on-disk copy lives under the caller-supplied directory — the 9D
        optimizer passes ``<CORVIN_HOME>/tenants/<tenant>/learning/meta_checkpoints``;
        this class never derives a path from ``Path.home()``.

        Security (Fix #2): Checkpoints are signed with Merkle root + HMAC tenant signature.
        """
        checkpoint_id = f"ckpt_{self.checkpoint_count:04d}_{int(datetime.now().timestamp())}"

        # Sign checkpoint (Fix #2)
        state_copy = state.copy()
        signing_result = self.signer.sign_checkpoint(state_copy)

        self.last_checkpoint = {
            'id': checkpoint_id,
            'state': state_copy,
            'timestamp': datetime.now().isoformat(),
            'merkle_root': signing_result['merkle_root'],
            'signature': signing_result['signature'],
        }

        # Store signed checkpoint for verification
        self.signed_checkpoints[checkpoint_id] = self.last_checkpoint

        self.checkpoint_count += 1

        if directory is not None:
            directory = Path(directory)
            directory.mkdir(parents=True, exist_ok=True)

            # Write signed checkpoint to disk
            payload = {
                'id': checkpoint_id,
                'state': state_copy,
                'timestamp': self.last_checkpoint['timestamp'],
                'merkle_root': signing_result['merkle_root'],
                'signature': signing_result['signature'],
                'tenant_id': self.tenant_id,
            }
            (directory / f"{checkpoint_id}.json").write_text(
                json.dumps(payload, sort_keys=True, default=str), encoding="utf-8"
            )

        return checkpoint_id

    def restore_checkpoint(self, checkpoint_id: str) -> Dict[str, Any]:
        """Restore from checkpoint.

        Security (Fix #2): Verifies checkpoint signature before restoration.
        Fail-closed: raises CheckpointSignatureError if verification fails.

        Args:
            checkpoint_id: ID of checkpoint to restore

        Returns:
            Restored state dict

        Raises:
            CheckpointSignatureError: If checkpoint signature invalid
        """
        # Any signed checkpoint is restorable by id, not just the newest one.
        # Until 2026-09-07 this compared against ``last_checkpoint`` only and
        # returned None for every earlier id, although ``save_checkpoint``
        # records them all in ``signed_checkpoints`` — a rollback target the
        # caller had legitimately kept was silently unreachable.
        checkpoint = self.signed_checkpoints.get(checkpoint_id)
        if checkpoint is None and self.last_checkpoint and self.last_checkpoint['id'] == checkpoint_id:
            checkpoint = self.last_checkpoint
        if checkpoint is None:
            return None  # unknown id — the caller checks (see test_watchdog_rejects_fake_checkpoint_id)

        # Verify signature before restoration (fail-closed)
        try:
            self.signer.verify_checkpoint(
                checkpoint['state'],
                checkpoint['merkle_root'],
                checkpoint['signature'],
                self.tenant_id
            )
        except CheckpointSignatureError as e:
            raise CheckpointSignatureError(
                f"Checkpoint {checkpoint_id} verification failed: {e}"
            )

        self.rollback_count += 1
        return checkpoint['state'].copy()

    def on_divergence(self, reason: str):
        """Handle divergence event."""
        print(f"⚠️  DIVERGENCE: {reason}")
        print(f"   Rollback #{self.rollback_count + 1}")
        if self.last_checkpoint:
            print(f"   Restoring checkpoint: {self.last_checkpoint['id']}")

    def detect_oscillation(self, loss_history: list[float], window_size: int = 20) -> bool:
        """
        Detect oscillation pattern in loss history.

        Oscillation detected if gradient sign flips > 50% of the time.

        Args:
            loss_history: list of recent loss values
            window_size: how many steps to analyze

        Returns:
            True if oscillation detected
        """
        if len(loss_history) < window_size:
            return False

        recent = loss_history[-window_size:]
        gradients = [recent[i+1] - recent[i] for i in range(len(recent)-1)]
        sign_flips = sum(1 for i in range(len(gradients)-1) if gradients[i] * gradients[i+1] < 0)
        oscillation_rate = sign_flips / len(gradients) if gradients else 0.0

        return oscillation_rate > 0.5

    def get_divergence_reason(self, state: Dict[str, float]) -> str:
        """
        Diagnose the reason for divergence.

        Returns:
            Human-readable reason string
        """
        for param_name, (min_val, max_val) in self.bounds.items():
            value = state.get(param_name)
            if value is None:
                continue

            if math.isnan(value):
                return f"{param_name} became NaN"
            if math.isinf(value):
                return f"{param_name} became Inf"
            if value < min_val or value > max_val:
                return f"{param_name} = {value} outside [{min_val}, {max_val}]"

        loss = state.get('loss', 0.0)
        if loss > 10.0:
            return f"Loss explosion: {loss} > 10.0"

        return "Unknown divergence"

    def health_check(self, state: Dict[str, float]) -> Dict[str, bool]:
        """
        Comprehensive health check of Meta Loop state.

        Returns:
            {
                'valid_bounds': bool,
                'no_nan_inf': bool,
                'loss_reasonable': bool,
                'overall_healthy': bool,
            }
        """
        results = {}

        # Check bounds
        results['valid_bounds'] = all(
            self.bounds[p][0] <= state.get(p, self.bounds[p][0]) <= self.bounds[p][1]
            for p in self.bounds
        )

        # Check for NaN/Inf
        results['no_nan_inf'] = all(
            not math.isnan(state.get(p, 0.0)) and not math.isinf(state.get(p, 0.0))
            for p in self.bounds
        )

        # Check loss magnitude
        loss = state.get('loss', 0.0)
        results['loss_reasonable'] = 0.0 <= loss <= 10.0

        # Overall
        results['overall_healthy'] = all(results.values())

        return results
