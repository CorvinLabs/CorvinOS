"""Local Whisper provider via ``pywhispercpp`` (a whisper.cpp binding).

ADR-0185 M1: ``pywhispercpp`` replaces ``faster-whisper`` as the canonical,
cross-platform local STT engine. ``faster-whisper``'s hard dependency on
``av`` (PyAV) has historically lacked reliable Windows wheels, so local STT
was never a real fallback on Windows. ``pywhispercpp`` ships genuine
``win32``/``win_amd64`` wheels for Python 3.9-3.14 with no ``av``/``torch``/
``ctranslate2`` dependency — same local STT engine, same code path, on
Windows, macOS, and Linux.

Lazy-imports ``pywhispercpp.model.Model``. If the package is missing,
``is_available()`` returns False and the resolver falls through. No GPU is
required — CPU works fine for short voice notes.

Model selection: ``CORVIN_STT_LOCAL_MODEL`` env (default ``tiny-q5_1``, a
Q5_1-quantized whisper.cpp GGML model, ~31 MB — small enough to
auto-download during install with a visible progress bar; see
``corvinOS/installer/steps/stt.py::_download_whisper_model``). Other
options: any name from ``pywhispercpp.constants.AVAILABLE_MODELS``, e.g.
``"base"``, ``"base-q5_1"``, ``"small"``, ``"medium"``, ``"large-v3"``, and
their ``.en``-only variants. Model files cache under
``voice_config_dir()/"whisper-models"`` — the same directory the installer
downloads into, so this provider never re-downloads a model the installer
already fetched.

Useful when:
  * the operator runs in an air-gapped environment;
  * a tenant's data-residency policy forbids OpenAI;
  * the OPENAI_API_KEY isn't available and the bridge must still work.

Opt-in accelerated alternative: operators who already have a working
``av``/CTranslate2 install can set
``CORVIN_STT_LOCAL_ENGINE=faster-whisper`` (and
``pip install "corvinos[voice]"`` or ``pip install faster-whisper``) to use
CTranslate2-accelerated inference instead. This is never required and never
the default — ``pywhispercpp`` is the engine that must work out of the box
on every platform.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from .base import (
    STTProvider,
    STTProviderUnavailable,
    STTTimeout,
    STTTranscriptionFailed,
    TranscriptResult,
)


_DEFAULT_MODEL = "tiny-q5_1"
_DEFAULT_TIMEOUT_S = 120.0  # Local CPU runs are slower than OpenAI.
_ENGINE_ENV = "CORVIN_STT_LOCAL_ENGINE"  # unset/"pywhispercpp" (default) | "faster-whisper"


def _resolve_voice_config_dir() -> Path:
    """SSOT for the corvin-voice config dir — byte-identical mirror of
    forge.paths.voice_config_dir() (same mirror openai_whisper.py keeps):
    VOICE_CONFIG_DIR → XDG_CONFIG_HOME → ~/.config, uniform on every
    platform. Guard: tests/test_voice_config_ssot.py.
    """
    override = os.environ.get("VOICE_CONFIG_DIR", "").strip()
    if override:
        return Path(os.path.expanduser(os.path.expandvars(override)))
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(os.path.expanduser(xdg)) if xdg else (Path.home() / ".config")
    return base / "corvin-voice"


def _models_dir() -> Path:
    """Where pywhispercpp GGML model files live — same dir the installer's
    ``_download_whisper_model`` populates, so provider and installer never
    disagree on where the model is cached (path-audit-class SSOT).
    """
    return _resolve_voice_config_dir() / "whisper-models"


def _prefer_faster_whisper() -> bool:
    """Opt-in escape hatch for operators who already have a working
    faster-whisper/av/CTranslate2 install and want its GPU-accelerated
    inference instead of pywhispercpp's CPU-only whisper.cpp binding.
    Never the default — see the ``voice`` extra in pyproject.toml.
    """
    return os.environ.get(_ENGINE_ENV, "").strip().lower() in (
        "faster-whisper", "faster_whisper", "ctranslate2",
    )


def _faster_whisper_importable() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


# Module-level singleton — model loads take a few seconds on first call.
# (engine_name, size_name, model_instance)
_loaded_model: tuple[str, str, object] | None = None

# Guards _load_model()'s check-then-set + the actual load/download below.
# Without this, two near-simultaneous first-use transcribe() calls (e.g. from
# two different messenger bridge workers) can race into pywhispercpp's own
# download_model() concurrently, both writing the SAME destination file with
# no locking on its side — risking a truncated/corrupted model (ADR-0185
# review finding). The lock is held across the whole load, including any
# download, and released only when that load actually finishes (see
# _load_model's done-callback) — never on a caller's own timeout — so a
# second caller waiting on the lock can't start a redundant parallel load.
_load_lock = threading.Lock()


class LocalWhisperProvider:
    """``pywhispercpp`` (whisper.cpp)-backed local transcription.

    ADR-0185 M1: the canonical local STT engine on every platform. Falls
    back to the legacy faster-whisper path only when
    ``CORVIN_STT_LOCAL_ENGINE=faster-whisper`` is set AND the package is
    importable — an explicit opt-in, never the default.
    """

    name = "local"

    def is_available(self) -> bool:
        if _prefer_faster_whisper() and _faster_whisper_importable():
            return True
        try:
            import pywhispercpp.model  # noqa: F401
        except ImportError:
            return False
        return True

    def _load_model(self, budget: float | None = None) -> tuple[str, object]:
        """Load (or return the cached singleton for) the local model.

        ``budget`` bounds the WHOLE load, including any model download
        pywhispercpp's ``Model(...)`` constructor may trigger on first use.
        Without this, a stalled connection during that download hangs
        indefinitely — outside the ``transcribe()``/``abort_callback`` budget
        entirely — which can exhaust the adapter's whole worker pool under a
        handful of concurrent first-time voice notes (ADR-0185 review
        finding).

        The actual load/download runs on a plain, ``daemon=True``
        ``threading.Thread`` — deliberately NOT a ``concurrent.futures.
        ThreadPoolExecutor`` (an earlier version of this fix used one, and
        review round 3 found two real bugs in it: (a) releasing the lock from
        a ``Future`` done-callback races with the calling thread publishing
        ``_loaded_model``, since ``Future.set_result()`` notifies waiters
        *before* invoking callbacks — reproduced empirically at ~11% of
        concurrent trials; (b) ``ThreadPoolExecutor``'s process-wide ``atexit``
        hook joins every worker thread ever created, so one stalled
        first-time download can hang the whole long-lived bridge daemon at
        shutdown). A daemon thread fixes both: this method's own code (not a
        callback firing on a different schedule) publishes ``_loaded_model``
        and releases ``_load_lock`` in that exact order, so no other thread
        can observe "lock released" before "singleton published"; and daemon
        threads are never joined at interpreter exit, so an orphaned stalled
        download can't block shutdown.
        """
        global _loaded_model
        size = os.environ.get("CORVIN_STT_LOCAL_MODEL", _DEFAULT_MODEL)
        engine = (
            "faster-whisper"
            if (_prefer_faster_whisper() and _faster_whisper_importable())
            else "pywhispercpp"
        )

        if _loaded_model is not None and _loaded_model[0] == engine and _loaded_model[1] == size:
            return engine, _loaded_model[2]

        # A negative-but-not-None budget (e.g. a caller-computed "remaining
        # time" that already went negative) must fail as STTTimeout, not as
        # a raw ValueError — Lock.acquire(timeout=X) raises ValueError for
        # any X < -1, and -1 itself is Python's own "block forever" sentinel,
        # which would silently turn "no time left" into "no timeout at all"
        # (round-4 review finding). Reachable via BRIDGE_TRANSCRIBE_TIMEOUT
        # (adapter.py) or transcribe.py's --timeout-s, both unvalidated.
        if budget is not None and budget < 0:
            raise STTTimeout(f"local STT model load budget already exhausted ({budget}s)")

        wait_start = time.monotonic()
        acquire_timeout = budget if budget is not None else -1
        if not _load_lock.acquire(timeout=acquire_timeout):
            raise STTTimeout(
                f"timed out after {budget}s waiting for a concurrent local STT "
                f"model load/download to finish"
            )

        try:
            # Another thread may have just finished loading while we waited.
            # MUST release the lock before returning here — we already hold
            # it (the acquire() above succeeded); this is the exact fast
            # path every second-and-later caller takes once the first
            # caller's background load finishes, so forgetting this release
            # deadlocks every subsequent transcribe() call after the first
            # concurrent access.
            if _loaded_model is not None and _loaded_model[0] == engine and _loaded_model[1] == size:
                _load_lock.release()
                return engine, _loaded_model[2]

            def _do_load() -> object:
                if engine == "faster-whisper":
                    from faster_whisper import WhisperModel
                    try:
                        return WhisperModel(size)
                    except Exception as exc:  # noqa: BLE001
                        raise STTProviderUnavailable(
                            f"faster-whisper model {size!r} could not be loaded: {exc}"
                        ) from exc

                try:
                    from pywhispercpp.model import Model
                except ImportError as exc:
                    raise STTProviderUnavailable(
                        "pywhispercpp not installed (pip install pywhispercpp)"
                    ) from exc
                try:
                    n_threads = int(os.environ.get("CORVIN_STT_LOCAL_THREADS", "4"))
                except ValueError:
                    n_threads = 4
                try:
                    return Model(
                        size,
                        models_dir=str(_models_dir()),
                        n_threads=n_threads,
                        # Quiet init: whisper.cpp is chatty on stderr by
                        # default (model-load banner) — this is a background
                        # voice provider, not a CLI tool, keep bridge logs
                        # clean.
                        redirect_whispercpp_logs_to=None,
                        print_progress=False,
                        print_realtime=False,
                    )
                except Exception as exc:  # noqa: BLE001
                    raise STTProviderUnavailable(
                        f"pywhispercpp model {size!r} could not be loaded: {exc}"
                    ) from exc

            result_box: dict[str, object] = {}
            done = threading.Event()

            def _run() -> None:
                global _loaded_model
                try:
                    result_box["model"] = _do_load()
                except BaseException as exc:  # noqa: BLE001
                    result_box["error"] = exc
                else:
                    # Publish BEFORE releasing the lock — any thread that
                    # only proceeds after acquiring _load_lock is guaranteed
                    # to see this, closing the TOCTOU window the callback-
                    # based version had.
                    _loaded_model = (engine, size, result_box["model"])
                finally:
                    _load_lock.release()
                    done.set()

            thread = threading.Thread(target=_run, daemon=True, name="stt-model-load")
            try:
                thread.start()
            except BaseException:
                # _run() never got to run at all — the lock is still ours.
                # The only fallible op between acquiring the lock and here
                # is Thread.start() itself (e.g. OS thread-creation failure).
                _load_lock.release()
                raise

            remaining = None
            if budget is not None:
                remaining = budget - (time.monotonic() - wait_start)
            if not done.wait(timeout=remaining if remaining is None or remaining > 0 else 0):
                # Timed out waiting — do NOT release the lock here. The
                # background thread is still running and owns the release;
                # releasing it now would let a second caller start a second,
                # racing load against the same destination file.
                raise STTTimeout(
                    f"local STT model load/download exceeded the {budget}s "
                    f"budget — likely a stalled network connection fetching "
                    f"the model"
                )

            # From here on `_run()`'s finally has already released the lock
            # (done.wait() only returns True after done.set(), which happens
            # after the release) — nothing left for us to clean up.
            if "error" in result_box:
                raise result_box["error"]
            return engine, result_box["model"]
        except Exception:
            # No further lock cleanup needed here: the cache-hit early return
            # above never touched the lock in this branch (it's above the
            # thread-start try/except, which already owns lock release for
            # its own failure case), and every path below it either already
            # released the lock (thread.start() failing) or deliberately
            # leaves it held for the still-running background thread
            # (STTTimeout / result_box["error"]) to release itself.
            raise

    def transcribe(
        self,
        audio_path: Path,
        *,
        lang: str | None = None,
        timeout_s: float | None = None,
    ) -> TranscriptResult:
        budget = timeout_s if timeout_s is not None else _DEFAULT_TIMEOUT_S
        t0 = time.monotonic()
        engine, model = self._load_model(budget=budget)
        remaining = budget - (time.monotonic() - t0)
        if remaining <= 0:
            raise STTTimeout(
                f"local STT model load consumed the entire {budget}s budget"
            )
        if engine == "faster-whisper":
            return self._transcribe_faster_whisper(model, audio_path, lang=lang, budget=remaining)
        return self._transcribe_pywhispercpp(model, audio_path, lang=lang, budget=remaining)

    # ── pywhispercpp (default) ───────────────────────────────────────────

    def _transcribe_pywhispercpp(
        self, model, audio_path: Path, *, lang: str | None, budget: float,
    ) -> TranscriptResult:
        if not Path(audio_path).exists():
            raise STTTranscriptionFailed(f"audio file not found: {audio_path}")

        t0 = time.monotonic()
        aborted = False

        def _abort_check() -> bool:
            nonlocal aborted
            if time.monotonic() - t0 > budget:
                aborted = True
                return True
            return False

        kwargs: dict = {}
        if lang and lang != "auto":
            kwargs["language"] = lang
        # No hint: leave `language` unset. whisper.cpp auto-detects the
        # language as part of the normal decode pass and still produces
        # segments (confirmed empirically — passing `detect_language=True`
        # instead makes whisper.cpp run a detect-only pass and return ZERO
        # segments, silently dropping the transcript; do not set that flag
        # here).

        try:
            segments = model.transcribe(
                str(audio_path), abort_callback=_abort_check, **kwargs,
            )
        except FileNotFoundError as exc:
            raise STTTranscriptionFailed(
                f"audio file not found: {audio_path}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise STTTranscriptionFailed(
                f"local whisper (pywhispercpp) failed: {exc}"
            ) from exc

        if aborted:
            raise STTTimeout(f"local whisper exceeded budget {budget}s")

        text = "".join(seg.text for seg in segments).strip()

        detected_lang = lang if (lang and lang != "auto") else None
        if detected_lang is None:
            # Best-effort: whisper.cpp exposes the auto-detected language id
            # via a direct C-binding call on the model's context. Never
            # let this fail the transcription — absence of a language tag
            # is an acceptable degradation, not an error.
            try:
                import _pywhispercpp as _pw  # type: ignore[import-not-found]
                lang_id = _pw.whisper_full_lang_id(model._ctx)  # noqa: SLF001
                detected_lang = _pw.whisper_lang_str(lang_id)
            except Exception:  # noqa: BLE001
                detected_lang = None

        duration = None
        if segments:
            # t1 is in whisper.cpp's 10ms ticks; last segment's end time.
            duration = segments[-1].t1 / 100.0

        return TranscriptResult(
            text=text,
            provider=self.name,
            lang=detected_lang,
            duration_s=duration,
        )

    # ── faster-whisper (opt-in legacy path) ──────────────────────────────

    def _transcribe_faster_whisper(
        self, model, audio_path: Path, *, lang: str | None, budget: float,
    ) -> TranscriptResult:
        t0 = time.monotonic()
        try:
            kwargs: dict = {}
            if lang and lang != "auto":
                kwargs["language"] = lang
            segments, info = model.transcribe(str(audio_path), **kwargs)
            # Eagerly materialise segments — they're a generator and the
            # transcription only happens when we iterate.
            pieces: list[str] = []
            for seg in segments:
                if time.monotonic() - t0 > budget:
                    raise STTTimeout(
                        f"local whisper exceeded budget {budget}s"
                    )
                pieces.append(seg.text)
        except STTTimeout:
            raise
        except FileNotFoundError as exc:
            raise STTTranscriptionFailed(
                f"audio file not found: {audio_path}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise STTTranscriptionFailed(
                f"local whisper (faster-whisper) failed: {exc}"
            ) from exc

        text = "".join(pieces).strip()
        detected_lang = getattr(info, "language", None) or lang or None
        duration = getattr(info, "duration", None)
        return TranscriptResult(
            text=text,
            provider=self.name,
            lang=detected_lang,
            duration_s=float(duration) if duration is not None else None,
        )


assert isinstance(LocalWhisperProvider(), STTProvider)
