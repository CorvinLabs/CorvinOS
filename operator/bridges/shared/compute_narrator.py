"""L25 extension — LLM narrative for finished compute runs.

Generates a spoken-style experiment summary from run artifacts (manifest +
summary + iteration trajectory). Uses the ``claude`` CLI (Max-subscription
OAuth) — the same subprocess pattern as ``summarize.py`` and
``helper_model.py``.

Result is cached in ``<run_dir>/narrative.json`` (mode 0600).

CI lint: this module MUST NOT ``import anthropic``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# MUST NOT import anthropic — CI AST lint enforces.

# ── System prompts ─────────────────────────────────────────────────────────
# Modelled after summarize.py: outcome-first, faithfulness as top rule,
# completeness as second rule, spoken-style prose.  Specialised for
# experiment results: the listener wants to know WHAT was found, HOW the
# search went, and what it means practically.

_SYSTEM_DE = (
    "Du bist ein Datenwissenschaftler, der das Ergebnis eines Optimierungs-"
    "experiments einem Kollegen mündlich erklärt — so wie man es jemandem "
    "ohne Bildschirm erzählen würde. Du paraphrasierst die Daten; du "
    "erfindest keine Zahlen, Empfehlungen oder Zusammenhänge, die nicht "
    "in den Eingabe-Daten stehen.\n"
    "\n"
    "AUFBAU (Outcome-First — das Ergebnis kommt zuerst):\n"
    "1. Lead-Satz im Hörer-Mental-Model: was hat das Experiment gefunden? "
    "   Nenne den besten Verlust- oder Score-Wert und die wichtigsten "
    "   zugehörigen Parameter. Keine 'Ich habe'-Eröffnung — immer aus "
    "   Sicht des Ergebnisses.\n"
    "2. Suchverlauf: Wie lief die Optimierung? Strategie, Anzahl Iterationen, "
    "   wann und warum die Suche endete (Konvergenzgrund), ob das Minimum "
    "   früh oder spät gefunden wurde. Nenne den Fortschritt konkret, "
    "   wenn er sich aus den Daten ablesen lässt (z.B. 'bereits nach "
    "   einem Drittel der Iterationen').\n"
    "3. Einordnung: Was bedeutet das Ergebnis praktisch? Nur was die Daten "
    "   tragen — keine erfundenen Empfehlungen.\n"
    "4. Optional — Folge-Idee: Eine konkrete nächste Maßnahme, wenn sie "
    "   sich direkt aus den Daten ergibt (z.B. 'Da alle guten Werte im "
    "   oberen Bereich lagen, könnte der Suchraum dort eingegrenzt "
    "   werden'). Weglassen, wenn die Daten das nicht stützen.\n"
    "\n"
    "TREUE (oberste Regel, schlägt alle anderen): Sage ausschließlich, "
    "was die Eingabe-Daten wirklich enthalten. Keine erfundenen Zahlen, "
    "keine Extrapolationen, keine Code-Bezeichner wörtlich vorlesen "
    "(umschreiben: 'learning_rate' → 'Lernrate', 'batch_size' → "
    "'Batch-Größe'). Im Zweifel weglassen statt erfinden.\n"
    "\n"
    "VOLLSTÄNDIGKEIT (zweite Regel): Nenne alle Best-Parameter, die in "
    "den Daten stehen — kein Parameter der Bestlösung darf fehlen.\n"
    "\n"
    "SPRECHSTIL: Natürlicher Fließtext, wie gesprochen. Keine Aufzählungs-"
    "zeichen, kein Markdown, keine Klammern, keine nummerierten Listen. "
    "Verbinde Punkte mit 'dabei', 'außerdem', 'am Ende', 'zuerst', "
    "'danach' statt nüchterner Listenform. Variiere Satzlänge und "
    "Wortwahl.\n"
    "\n"
    "Länge: So lang wie nötig, um alle wesentlichen Punkte zu sagen — "
    "typisch 150–500 Wörter. Vollständigkeit schlägt Kürze.\n"
    "\n"
    "Antworte nur mit dem Vorlese-Text selbst, ohne Überschrift oder "
    "Erklärung."
)

_SYSTEM_EN = (
    "You are a data scientist explaining the result of an optimisation "
    "experiment to a colleague out loud — the way you'd tell someone "
    "without a screen. You paraphrase the data; you invent no numbers, "
    "recommendations, or relationships not present in the input data.\n"
    "\n"
    "OUTPUT SHAPE (outcome-first — the result leads):\n"
    "1. Lead sentence in the listener's mental model: what did the "
    "   experiment find? Name the best loss or score and the most "
    "   important parameters. Never open with 'I' — always from the "
    "   result's perspective.\n"
    "2. Search narrative: how did the optimisation go? Strategy, "
    "   number of iterations, when and why the search ended (convergence "
    "   reason), whether the minimum was found early or late. Be concrete "
    "   about progress when the data supports it (e.g. 'already in the "
    "   first third of iterations').\n"
    "3. Interpretation: what does the result mean in practice? Only "
    "   what the data carries — no invented recommendations.\n"
    "4. Optional — follow-up idea: one concrete next step if it follows "
    "   directly from the data (e.g. 'since all good values clustered at "
    "   the high end, narrowing the search space there makes sense'). "
    "   Drop if unsupported.\n"
    "\n"
    "FAITHFULNESS (top rule, beats all others): say only what the input "
    "data actually contains. No invented numbers, no extrapolations, no "
    "code identifiers spoken verbatim (paraphrase: 'learning_rate' → "
    "'learning rate', 'batch_size' → 'batch size'). When in doubt, "
    "drop rather than invent.\n"
    "\n"
    "COMPLETENESS (second rule): name every best-parameter in the data — "
    "none missing from the best solution.\n"
    "\n"
    "SPEAKING STYLE: natural prose, as spoken. No bullets, no markdown, "
    "no parentheses, no numbered lists. Connect points with 'also', "
    "'in addition', 'by the end', 'first', 'then' rather than a flat "
    "list. Vary sentence length and word choice.\n"
    "\n"
    "Length: as long as needed to cover all key points — typically "
    "150–500 words. Completeness beats brevity.\n"
    "\n"
    "Respond with only the spoken text, no heading or explanation."
)

_SYSTEMS: dict[str, str] = {"de": _SYSTEM_DE, "en": _SYSTEM_EN}

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"


# ── Internal helpers ───────────────────────────────────────────────────────

def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _build_input(run_dir: Path) -> str:
    """Construct a human-readable experiment description from run artifacts."""
    manifest = _read_json(run_dir / "manifest.json") or {}
    summary = _read_json(run_dir / "summary.json") or {}

    tool = manifest.get("tool_name") or "unknown tool"
    strategy = manifest.get("strategy") or "unknown strategy"
    objective = manifest.get("objective") or ""
    best_loss = summary.get("best_loss")
    best_iter = summary.get("best_iter")
    convergence = summary.get("convergence_reason") or ""
    state = summary.get("state") or "unknown"

    # Best params — prefer summary.best_params, fall back to the best iteration's file.
    best_params: dict[str, Any] = summary.get("best_params") or {}
    if not best_params and best_iter is not None:
        iters_dir = run_dir / "iterations"
        for candidate in (
            iters_dir / f"iter_{int(best_iter):03d}.json",
            iters_dir / f"iter_{int(best_iter)}.json",
        ):
            if candidate.exists():
                d = _read_json(candidate) or {}
                best_params = d.get("params") or {}
                break

    # Iteration trajectory — all losses in order
    iters_dir = run_dir / "iterations"
    losses: list[float] = []
    if iters_dir.is_dir():
        try:
            files = sorted(
                (f for f in iters_dir.iterdir() if f.suffix == ".json"),
                key=lambda f: int(f.stem.split("_")[-1]) if f.stem.split("_")[-1].isdigit() else 0,
            )
            for f in files:
                d = _read_json(f) or {}
                if isinstance(d.get("loss"), (int, float)):
                    losses.append(float(d["loss"]))
        except OSError:
            pass

    n_iters = len(losses)

    lines: list[str] = [
        f"Tool / experiment: {tool}",
        f"Search strategy: {strategy}",
        f"Run state: {state}",
        f"Total iterations completed: {n_iters}",
    ]
    if objective:
        lines.append(f"Objective: {objective}")
    if best_loss is not None:
        lines.append(f"Best loss / score found: {best_loss:.6g}")
    if best_iter is not None:
        lines.append(f"Best result found at iteration: {best_iter} of {n_iters}")
    if convergence:
        lines.append(f"Search stopped because: {convergence.replace('_', ' ')}")
    if best_params:
        params_str = ", ".join(f"{k} = {v}" for k, v in best_params.items())
        lines.append(f"Best parameter configuration: {params_str}")

    # Trajectory: first 3, last 3 (with ellipsis if long)
    if losses:
        if len(losses) <= 8:
            traj = [f"{l:.4g}" for l in losses]
        else:
            traj = [f"{l:.4g}" for l in losses[:3]] + ["..."] + [f"{l:.4g}" for l in losses[-3:]]
        lines.append(f"Loss trajectory (sample): {', '.join(traj)}")
        if losses[0] > 0:
            improvement = (losses[0] - min(losses)) / losses[0] * 100
            lines.append(f"Overall improvement: {improvement:.1f}% from start to best")

    return "\n".join(lines)


def _run_haiku(system: str, prompt: str, model: str) -> str | None:
    """Call ``claude -p`` and return stdout. Returns None on any failure."""
    try:
        from helper_model import resolve_claude_bin as _resolve_bin  # type: ignore
        _bin = _resolve_bin()
    except Exception:  # noqa: BLE001
        _bin = "claude"
    if not (shutil.which(_bin) or os.path.isfile(_bin)):
        print("[compute_narrator] claude CLI not found", file=sys.stderr)
        return None
    env = os.environ.copy()
    env["VOICE_HOOK_RECURSION"] = "1"  # prevent recursive TTS/summary trigger
    try:
        out = subprocess.run(
            [
                _bin, "-p", prompt,
                "--append-system-prompt", system,
                "--model", model,
                "--disallowedTools", "*",
            ],
            capture_output=True, text=True, env=env, timeout=90, check=True,
        )
        return out.stdout.strip() or None
    except subprocess.CalledProcessError as exc:
        print(f"[compute_narrator] claude exited {exc.returncode}: {exc.stderr[:200]}", file=sys.stderr)
        return None
    except subprocess.TimeoutExpired:
        print("[compute_narrator] claude timed out after 90s", file=sys.stderr)
        return None


# ── Public API ─────────────────────────────────────────────────────────────

def narrate_run(
    run_dir: Path,
    *,
    locale: str = "de",
    model: str = _DEFAULT_MODEL,
    force: bool = False,
) -> dict[str, Any] | None:
    """Generate (or load cached) narrative for a compute run.

    Parameters
    ----------
    run_dir:
        Path to the run directory containing ``manifest.json`` and
        ``summary.json``.
    locale:
        BCP-47 locale for the spoken output (``de`` or ``en``; any
        non-English code falls back to ``de``).
    model:
        Haiku model ID to use for generation.
    force:
        Re-generate even if ``narrative.json`` already exists.

    Returns
    -------
    dict with keys ``text``, ``locale``, ``lang``, ``model``,
    ``generated_at``, or ``None`` if generation failed.
    Result is cached atomically in ``<run_dir>/narrative.json``.
    """
    cache = run_dir / "narrative.json"
    if cache.exists() and not force:
        data = _read_json(cache)
        if data and data.get("text"):
            return data

    try:
        input_text = _build_input(run_dir)
    except Exception as exc:
        print(f"[compute_narrator] _build_input failed: {exc}", file=sys.stderr)
        return None

    lang = "en" if locale.lower().startswith("en") else "de"
    system = _SYSTEMS[lang]
    text = _run_haiku(system, input_text, model)
    if not text:
        return None

    result: dict[str, Any] = {
        "text": text,
        "locale": locale,
        "lang": lang,
        "model": model,
        "generated_at": time.time(),
    }

    # Atomic write, mode 0600 — same pattern as other L25/L28 cached files.
    import tempfile
    raw = json.dumps(result, indent=2, ensure_ascii=False)
    fd, tmp = tempfile.mkstemp(dir=str(run_dir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(raw)
        os.chmod(tmp, 0o600)
        os.replace(tmp, cache)
    except Exception as exc:
        print(f"[compute_narrator] cache write failed: {exc}", file=sys.stderr)
        try:
            os.unlink(tmp)
        except OSError:
            pass

    return result
