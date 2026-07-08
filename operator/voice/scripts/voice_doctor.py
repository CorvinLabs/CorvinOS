#!/usr/bin/env python3
"""corvin-voice doctor — ADR-0185 M5: real, non-mocked STT+TTS round-trip self-test.

Actually transcribes a real fixture WAV (``fixtures/stt_sample.wav``) through
the STT provider chain and actually synthesizes a real voice note through the
TTS provider chain (``adapter.py::synthesize_voice_note``), then reports a
clear per-check PASS/FAIL plus an overall summary. Exits non-zero on any
failure so a human — or CI — can tell at a glance that voice is broken.

This exists specifically to close the "fails silently" class of bug this
subsystem has a track record of (ADR-0185 context: ``adapter.py`` never
imported ``asyncio`` at module level, so every edge-tts call raised
``NameError`` that was swallowed into a log line, undetected for a long
time, on every platform). Nothing here is mocked: it hits the real
pywhispercpp model load and the real edge-tts/Piper synthesis path, so it
needs the same runtime state a live install has (model files fetched,
network reachable for the cloud/edge-tts legs). Run ``corvin-install``
first on a fresh checkout if the STT provider table below shows nothing
ready.

Usage:
    corvin-voice doctor [--stt-timeout SECONDS]

Exit codes:
    0   both STT and TTS round-trips passed
    1   at least one round-trip failed
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Make `stt` (this directory) and `adapter` (bridges/shared) importable when
# run standalone — same self-sufficiency pattern already used by
# transcribe.py/say.py in this directory — so `corvin-voice doctor` works
# whether invoked via the ops.launcher entry-point shim or directly as a
# script (e.g. `python operator/voice/scripts/voice_doctor.py doctor`).
_SCRIPT_DIR = Path(__file__).resolve().parent
_SHARED_DIR = (_SCRIPT_DIR / ".." / ".." / "bridges" / "shared").resolve()
for _p in (_SCRIPT_DIR, _SHARED_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from stt import resolver as _stt_resolver  # noqa: E402


_FIXTURE_WAV = _SCRIPT_DIR / "fixtures" / "stt_sample.wav"
# Local CPU whisper.cpp decode (+ possible first-run model download) can be
# slow on modest CI runners — generous default budget, overridable via CLI.
_DEFAULT_STT_TIMEOUT_S = 180.0
_DOCTOR_TTS_TEXT = "This is a voice doctor test."


def _stt_provider_rows() -> list[tuple[str, bool, str]]:
    """Per-provider readiness + a human-readable reason for the STT chain.

    Reuses ``resolver.provider_status()`` (ADR-0185 M4) instead of
    duplicating provider-internal probing here — the Console status panel
    (M4) and this doctor command (M5) must read the same SSOT so they
    never drift apart (this codebase has a documented history of exactly
    that class of bug: two call sites hand-rolling the same check and
    silently disagreeing).
    """
    rows: list[tuple[str, bool, str]] = []
    status = _stt_resolver.provider_status()
    labels = {
        "local": "local (pywhispercpp)",
        "openai": "openai (cloud Whisper)",
    }
    for name, label in labels.items():
        info = status.get(name)
        if info is None:
            rows.append((label, False, "unknown provider (missing from resolver status)"))
            continue
        rows.append((label, bool(info.get("ready")), str(info.get("detail", ""))))
    return rows


def _tts_provider_rows() -> list[tuple[str, bool, str]]:
    """Same best-effort diagnostics for the TTS chain — read-only probes of
    the exact conditions ``adapter.py``'s TTS chain itself checks. Never a
    second decision path: the round-trip check below is what actually
    decides pass/fail; this table only explains *why* to a human.
    """
    import adapter  # local import — only needed for this diagnostic table

    rows: list[tuple[str, bool, str]] = []

    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        for env_file in (
            adapter._VOICE_CONFIG_DIR / ".env",
            adapter._VOICE_CONFIG_DIR / "service.env",
        ):
            for env_key in ("OPENAI_API_KEY", "OPENAI_APIKEY"):
                try:
                    value = adapter._load_env_value(env_key, env_file)
                except Exception:  # noqa: BLE001 — diagnostics must never crash
                    value = None
                if value:
                    key = value
                    break
            if key:
                break
    if not key:
        rows.append(("openai (cloud TTS)", False, "no API key (OPENAI_API_KEY unset)"))
    else:
        try:
            import openai  # noqa: F401
            rows.append(("openai (cloud TTS)", True, "ready"))
        except ImportError:
            rows.append(("openai (cloud TTS)", False, "openai package missing (pip install openai)"))

    try:
        import edge_tts  # noqa: F401
        edge_ok, edge_reason = True, "ready (needs internet at call time)"
    except ImportError:
        edge_ok, edge_reason = False, "edge-tts package missing (pip install edge-tts)"
    if edge_ok:
        try:
            ffmpeg_bin = adapter._resolve_ffmpeg_bin()
        except Exception:  # noqa: BLE001
            ffmpeg_bin = None
        if not ffmpeg_bin:
            edge_ok, edge_reason = False, "ffmpeg not found (system PATH or bundled imageio-ffmpeg)"
    rows.append(("edge-tts", edge_ok, edge_reason))

    import shutil as _shutil
    piper_bin = os.environ.get("PIPER_BIN") or _shutil.which("piper")
    if not piper_bin:
        rows.append((
            "piper (local TTS)", False,
            "binary not found (install piper-tts or set PIPER_BIN)",
        ))
    else:
        rows.append((
            "piper (local TTS)", True,
            "binary found (model presence is checked at call time)",
        ))

    return rows


def _check_stt(timeout_s: float) -> tuple[bool, str]:
    """Real round-trip: transcribe the fixture WAV, verify non-empty text."""
    if not _FIXTURE_WAV.exists():
        return False, f"fixture WAV missing: {_FIXTURE_WAV}"
    try:
        result = _stt_resolver.transcribe(_FIXTURE_WAV, timeout_s=timeout_s)
    except Exception as exc:  # noqa: BLE001 — doctor reports, never crashes
        return False, f"STT round-trip raised: {exc}"
    if not result.text.strip():
        return False, f"STT round-trip returned EMPTY text (provider={result.provider})"
    return True, f"provider={result.provider!r} text={result.text!r}"


def _check_tts(text: str) -> tuple[bool, str, Path | None]:
    """Real round-trip: synthesize *text*, verify a nonzero-size audio file."""
    import adapter
    try:
        out_path = adapter.synthesize_voice_note(text, lang="en")
    except Exception as exc:  # noqa: BLE001
        return False, f"TTS round-trip raised: {exc}", None
    if out_path is None:
        reason = adapter.voice_skip_reason() or "no reason recorded"
        return False, f"no audio produced — {reason}", None
    try:
        size = out_path.stat().st_size
    except OSError as exc:
        return False, f"produced path does not exist: {exc}", out_path
    if size <= 0:
        return False, f"produced a zero-byte file: {out_path}", out_path
    return True, f"{out_path} ({size} bytes)", out_path


def _print_rows(rows: list[tuple[str, bool, str]]) -> None:
    for name, ok, reason in rows:
        mark = "ready  " if ok else "MISSING"
        print(f"    [{mark}] {name:<24s} {reason}")


def run_doctor(stt_timeout: float = _DEFAULT_STT_TIMEOUT_S) -> int:
    """Run the full doctor sequence. Returns a process exit code (0 = pass)."""
    print("corvin-voice doctor — ADR-0185 M5 real STT+TTS round-trip self-test")
    print("=" * 72)

    print("\n[STT] provider availability")
    _print_rows(_stt_provider_rows())

    print("\n[STT] round-trip: transcribe fixtures/stt_sample.wav")
    t0 = time.monotonic()
    stt_ok, stt_msg = _check_stt(stt_timeout)
    dt = time.monotonic() - t0
    print(f"    [{'PASS' if stt_ok else 'FAIL'}] ({dt:.1f}s) {stt_msg}")

    print("\n[TTS] provider availability")
    _print_rows(_tts_provider_rows())

    print(f"\n[TTS] round-trip: synthesize {_DOCTOR_TTS_TEXT!r}")
    t0 = time.monotonic()
    tts_ok, tts_msg, tts_path = _check_tts(_DOCTOR_TTS_TEXT)
    dt = time.monotonic() - t0
    print(f"    [{'PASS' if tts_ok else 'FAIL'}] ({dt:.1f}s) {tts_msg}")
    if tts_path is not None:
        try:
            tts_path.unlink()
        except OSError:
            pass  # best-effort cleanup of the doctor's own test artifact

    overall_ok = stt_ok and tts_ok
    print("\n" + "=" * 72)
    print(f"OVERALL: {'PASS' if overall_ok else 'FAIL'}")
    if not overall_ok:
        print("Voice subsystem has at least one broken round-trip (see FAIL line(s) above).")
    return 0 if overall_ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="corvin-voice",
        description="CorvinOS voice subsystem CLI (ADR-0185).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    doctor_p = sub.add_parser(
        "doctor",
        help=(
            "Real, non-mocked STT+TTS round-trip self-test — loud "
            "pass/fail, non-zero exit code on failure."
        ),
    )
    doctor_p.add_argument(
        "--stt-timeout",
        type=float,
        default=_DEFAULT_STT_TIMEOUT_S,
        help=f"STT transcription budget in seconds (default: {_DEFAULT_STT_TIMEOUT_S})",
    )
    args = parser.parse_args(argv)

    if args.command == "doctor":
        return run_doctor(stt_timeout=args.stt_timeout)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
