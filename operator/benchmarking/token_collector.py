"""Token tracking instrumentation for benchmarks.

Records every LLM call with tokens in/out, latency, engine, and model.
Supports deterministic replay and audit trail export.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass
class LLMCall:
    """Record of one LLM API call."""

    timestamp: float
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    engine: str
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


@dataclass
class BenchmarkTokenCollector:
    """Collect and aggregate token counts for a benchmark run."""

    run_id: str = field(default_factory=lambda: str(uuid4())[:8])
    task_id: str = ""
    mode: str = ""  # "claude_code" or "tde"
    calls: list[LLMCall] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        """Total billable tokens (input + output)."""
        return self.tokens_in + self.tokens_out

    @property
    def tokens_in(self) -> int:
        """Sum of input tokens."""
        return sum(c.tokens_in for c in self.calls)

    @property
    def tokens_out(self) -> int:
        """Sum of output tokens."""
        return sum(c.tokens_out for c in self.calls)

    @property
    def cache_read_tokens(self) -> int:
        """Sum of cache read tokens (free)."""
        return sum(c.cache_read_tokens for c in self.calls)

    @property
    def cache_creation_tokens(self) -> int:
        """Sum of cache creation tokens."""
        return sum(c.cache_creation_tokens for c in self.calls)

    @property
    def total_latency_ms(self) -> float:
        """Sum of all latencies."""
        return sum(c.latency_ms for c in self.calls)

    @property
    def avg_latency_ms(self) -> float:
        """Average latency per call."""
        if not self.calls:
            return 0.0
        return self.total_latency_ms / len(self.calls)

    def record_call(
        self,
        model: str,
        tokens_in: int,
        tokens_out: int,
        latency_ms: int,
        engine: str,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
    ):
        """Record one LLM call."""
        call = LLMCall(
            timestamp=time.time(),
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            engine=engine,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
        )
        self.calls.append(call)

    def to_dict(self) -> dict[str, Any]:
        """Export as dict (JSON-serializable)."""
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "mode": self.mode,
            "total_tokens": self.total_tokens,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "total_latency_ms": self.total_latency_ms,
            "avg_latency_ms": self.avg_latency_ms,
            "num_calls": len(self.calls),
            "calls": [asdict(c) for c in self.calls],
        }

    def export_json(self, output_file: Path):
        """Export raw data as JSON."""
        data = {
            "metadata": {
                "exported_at": datetime.now().isoformat(),
                "version": "1.0",
            },
            "collector": self.to_dict(),
        }
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(data, indent=2))
