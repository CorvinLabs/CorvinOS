"""Layer 38 v3 — Deterministic compute engine for A2A demos + lightweight ops.

A WorkerEngine implementation that runs a *deterministic* pipeline on
CSV inputs instead of invoking an LLM subprocess. Useful for:

  * **Reproducible demos** of the A2A attachment path (no LLM-side
    nondeterminism distracting from the protocol invariants).
  * **Cheap ops endpoints** — a "cloud" caller can trigger an
    on-premise compute over a private dataset without burning model
    tokens.
  * **E2E tests** that need a *real* worker producing *real* file
    outputs (CSV summary + matplotlib histogram PNG).

Pipeline
--------

Currently a single operation is supported, picked deterministically:

  ``csv_summary``  — for each numeric column in ``in/*.csv``, compute
  ``count``, ``mean``, ``stdev``, ``min``, ``max``. Render a histogram
  of the first numeric column as ``out/histogram.png``. Write the
  summary as ``out/summary.json``.

The operation is selected by content, not by instruction parsing — the
worker honours the framing-block discipline (instruction is *not* code),
and the action is structurally fixed at the engine layer. This is the
right model for trustless compute: the *origin* picks the engine via the
``allowed_personas`` config; the engine itself is deterministic.

CI lint: this module MUST NOT ``import anthropic``.
"""
from __future__ import annotations

import csv
import io
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

# Engine-side dependencies are intentionally MINIMAL — only stdlib +
# matplotlib (which is in the project venv) + PIL (also in venv).
try:
    import matplotlib
    matplotlib.use("Agg")  # headless backend; must be before pyplot import
    import matplotlib.pyplot as _plt  # type: ignore[import-not-found]
    _MPL_OK = True
except Exception:
    _plt = None
    _MPL_OK = False


# ── Mirror the StreamEvent shape from agents/__init__.py ────────────────
# We avoid importing from agents to keep this module standalone (the
# receiver's lazy import path still works either way).

@dataclass
class StreamEvent:
    type: str
    text: str | None = None
    usage: dict | None = None
    error: str | None = None


# ── Engine ───────────────────────────────────────────────────────────────

class DeterministicComputeEngine:
    """WorkerEngine implementation that runs a fixed CSV-summary pipeline.

    Conforms to the WorkerEngine Protocol (``name``, ``capabilities``,
    ``spawn``, ``cancel``). Reads attachments from
    ``<working_dir>/in/``, writes outputs to ``<working_dir>/out/``.
    """
    name = "compute-csv"
    capabilities: dict = {
        "deterministic": True,
        "supports_attachments": True,
        "mid_stream_inject": False,
        "hooks": False,
        "skills_tool": False,
    }

    def spawn(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        working_dir: Path | None = None,
        timeout: float = 120.0,
        extra_args: list[str] | None = None,
        env: dict[str, str] | None = None,
        # Accept and ignore the additional ClaudeCodeEngine kwargs so
        # the WorkerEngine call surface stays compatible.
        **_ignored: Any,
    ) -> Iterator[StreamEvent]:
        # The compute pipeline ignores prompt content — the instruction
        # contributed by the framing block is structurally untrusted.
        # The deterministic action is fixed at the engine level.
        if working_dir is None:
            yield StreamEvent(type="error", error="compute_no_working_dir")
            yield StreamEvent(type="turn_completed", text="")
            return

        in_dir = Path(working_dir) / "in"
        out_dir = Path(working_dir) / "out"
        out_dir.mkdir(exist_ok=True)

        csv_files = sorted([p for p in in_dir.glob("*.csv")]) if in_dir.exists() else []
        if not csv_files:
            err = json.dumps({"error": "no_csv_input_attachment"})
            yield StreamEvent(type="text_delta", text=err)
            yield StreamEvent(type="turn_completed", text=err)
            return

        try:
            summary = self._summarize_csv(csv_files[0])
        except Exception as exc:
            err = json.dumps({"error": f"compute_failed:{type(exc).__name__}"})
            yield StreamEvent(type="text_delta", text=err)
            yield StreamEvent(type="turn_completed", text=err)
            return

        # Write summary.json
        (out_dir / "summary.json").write_text(
            json.dumps(summary, sort_keys=True, indent=2),
        )

        # Render histogram of the first numeric column (if any)
        first_col = next(iter(summary.get("columns", {})), None)
        if first_col and _MPL_OK:
            try:
                self._render_histogram(
                    csv_files[0], first_col, out_dir / "histogram.png",
                )
            except Exception:
                # Histogram is a nice-to-have; summary remains valid.
                pass
        elif first_col and not _MPL_OK:
            # Without matplotlib, drop a tiny placeholder so the caller
            # still receives a "histogram" attachment slot.
            (out_dir / "histogram.png").write_bytes(
                _minimal_png_placeholder(),
            )

        # Emit a compact JSON summary as the worker text output.
        payload = json.dumps({
            "ok": True,
            "rows_total": summary["rows_total"],
            "numeric_columns": list(summary.get("columns", {}).keys()),
            "histogram_column": first_col,
            "engine": self.name,
        })
        yield StreamEvent(type="text_delta", text=payload)
        yield StreamEvent(type="turn_completed", text=payload)

    def cancel(self) -> None:
        # No subprocess to kill.
        pass

    # ── Compute primitives ────────────────────────────────────────────

    @staticmethod
    def _summarize_csv(path: Path) -> dict:
        """Return a per-numeric-column summary.

        Numeric column = at least 1 row parses as float in the first
        500 sampled rows AND ≥80% of sampled values parse as float.
        """
        rows_total = 0
        numeric_cols: dict[str, list[float]] = {}
        sample_cap_rows = 50_000  # cap memory in-process
        with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None:
                return {"rows_total": 0, "columns": {}}
            for row in reader:
                rows_total += 1
                if rows_total > sample_cap_rows:
                    break
                for k, v in row.items():
                    if v is None or v == "":
                        continue
                    try:
                        f = float(v)
                        if math.isnan(f) or math.isinf(f):
                            continue
                        numeric_cols.setdefault(k, []).append(f)
                    except (TypeError, ValueError):
                        pass

        # Drop columns that didn't reach the 80% parse rate.
        out_cols: dict[str, dict] = {}
        for col, vals in numeric_cols.items():
            if len(vals) < max(1, int(0.8 * min(rows_total, sample_cap_rows))):
                continue
            out_cols[col] = {
                "count":  len(vals),
                "mean":   round(statistics.fmean(vals), 4),
                "stdev":  round(statistics.pstdev(vals), 4),
                "min":    min(vals),
                "max":    max(vals),
            }
        return {"rows_total": rows_total, "columns": out_cols}

    @staticmethod
    def _render_histogram(csv_path: Path, column: str, out_path: Path) -> None:
        if not _MPL_OK:
            return
        vals: list[float] = []
        with csv_path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                raw = row.get(column)
                if not raw:
                    continue
                try:
                    vals.append(float(raw))
                except (TypeError, ValueError):
                    pass
                if len(vals) >= 100_000:
                    break

        fig, ax = _plt.subplots(figsize=(6, 4), dpi=120)
        ax.hist(vals, bins=40, color="#3a86ff", edgecolor="#1f3a8a")
        ax.set_title(f"Histogram — {column} (n={len(vals)})")
        ax.set_xlabel(column)
        ax.set_ylabel("frequency")
        ax.grid(axis="y", alpha=0.3)
        fig.tight_layout()
        fig.savefig(out_path, format="png")
        _plt.close(fig)


def _minimal_png_placeholder() -> bytes:
    """A 1×1 transparent PNG (smallest valid file). Used when matplotlib
    is missing so the response still carries the declared attachment slot."""
    import base64
    return base64.b64decode(
        b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
    )


__all__ = ["DeterministicComputeEngine", "StreamEvent"]
