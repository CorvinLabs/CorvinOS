"""Tier 3: Advanced Loss Signals — Concept Drift + Skill Mismatch Detection

Detects when learned model changes (concept drift) or skill routing breaks.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from enum import Enum
import math


class LossSignalType(Enum):
    CONCEPT_DRIFT = "concept_drift"
    SKILL_MISMATCH = "skill_mismatch"
    CONVERGENCE_STALL = "convergence_stall"
    OUTLIER = "outlier"
    REGIME_CHANGE = "regime_change"


@dataclass
class LossObservation:
    """Single loss measurement with metadata."""
    timestamp: str
    loss: float
    model: str
    task_type: str
    confidence: float
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow().isoformat() + "Z"


@dataclass
class ConceptDriftSignal:
    """Detected concept drift (distribution shifted)."""
    signal_type: LossSignalType = LossSignalType.CONCEPT_DRIFT
    severity: float  # 0-1
    mean_shift: float  # How much mean changed
    variance_shift: float
    window_size: int
    timestamp: str = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow().isoformat() + "Z"


@dataclass
class SkillMismatchSignal:
    """Detected skill → task mismatch."""
    signal_type: LossSignalType = LossSignalType.SKILL_MISMATCH
    skill_id: str
    expected_loss: float
    actual_loss: float
    error_rate: float  # % of tasks that broke routing
    recommendation: str
    timestamp: str = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow().isoformat() + "Z"


class AdvancedLossSignals:
    """Detect advanced loss pathologies."""
    
    def __init__(self, window_size: int = 100):
        self.observations: List[LossObservation] = []
        self.window_size = window_size
        self.signals: List[Dict] = []
    
    async def record_observation(self, obs: LossObservation) -> None:
        """Record loss observation."""
        self.observations.append(obs)
        if len(self.observations) > 10000:
            self.observations.pop(0)
    
    def detect_concept_drift(self, threshold: float = 0.3) -> Optional[ConceptDriftSignal]:
        """Detect concept drift via mean shift detection."""
        if len(self.observations) < self.window_size * 2:
            return None
        
        # Split into two windows
        window1 = self.observations[-self.window_size*2:-self.window_size]
        window2 = self.observations[-self.window_size:]
        
        # Compute statistics
        mean1 = sum(o.loss for o in window1) / len(window1)
        mean2 = sum(o.loss for o in window2) / len(window2)
        
        var1 = sum((o.loss - mean1) ** 2 for o in window1) / len(window1)
        var2 = sum((o.loss - mean2) ** 2 for o in window2) / len(window2)
        
        # Z-score for mean shift
        pooled_std = math.sqrt((var1 + var2) / 2)
        if pooled_std == 0:
            return None
        
        z_score = abs(mean2 - mean1) / (pooled_std / math.sqrt(self.window_size))
        
        # If z > 2.58 (99% confidence), drift detected
        if z_score > 2.58:
            severity = min(1.0, z_score / 5.0)
            signal = ConceptDriftSignal(
                severity=severity,
                mean_shift=mean2 - mean1,
                variance_shift=var2 - var1,
                window_size=self.window_size
            )
            self.signals.append({"type": "concept_drift", "signal": signal})
            return signal
        
        return None
    
    def detect_skill_mismatch(self, skill_id: str, expected_loss: float, tolerance: float = 0.2) -> Optional[SkillMismatchSignal]:
        """Detect when skill routing breaks."""
        recent = [o for o in self.observations[-50:] if o.model == skill_id]
        
        if len(recent) < 10:
            return None
        
        actual_loss = sum(o.loss for o in recent) / len(recent)
        
        # If actual >> expected, skill is broken
        if actual_loss > expected_loss * (1 + tolerance):
            error_rate = sum(1 for o in recent if o.loss > expected_loss * 1.5) / len(recent)
            
            signal = SkillMismatchSignal(
                skill_id=skill_id,
                expected_loss=expected_loss,
                actual_loss=actual_loss,
                error_rate=error_rate,
                recommendation=f"Retrain {skill_id} or bypass for this task type"
            )
            self.signals.append({"type": "skill_mismatch", "signal": signal})
            return signal
        
        return None
    
    def detect_convergence_stall(self, stall_threshold: int = 50) -> bool:
        """Detect when learning has stalled (no improvement)."""
        if len(self.observations) < stall_threshold:
            return False
        
        recent = self.observations[-stall_threshold:]
        mean_loss = sum(o.loss for o in recent) / len(recent)
        variance = sum((o.loss - mean_loss) ** 2 for o in recent) / len(recent)
        
        # If variance < 0.01 and mean > 0.2, likely stalled
        if variance < 0.01 and mean_loss > 0.2:
            self.signals.append({"type": "convergence_stall", "data": {
                "mean_loss": mean_loss,
                "variance": variance,
                "window": stall_threshold
            }})
            return True
        
        return False
    
    def get_signals(self) -> List[Dict]:
        """Get all detected signals."""
        return self.signals
    
    def clear_signals(self) -> None:
        """Clear signal history."""
        self.signals = []
