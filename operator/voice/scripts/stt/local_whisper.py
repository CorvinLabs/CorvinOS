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

Model selection: ``CORVIN_STT_LOCAL_MODEL`` env overrides everything. With no
override the default is RAM-aware across THREE tiers (``_default_local_model``):
``base-q5_1`` on a low-RAM box (< ~3 GB), ``small-q5_1`` on a normal machine
(3–16 GB), and ``medium-q5_0`` on a capable box (≥ 16 GB usable RAM AND ≥ 8
CPUs — RAM is cgroup-aware, and CPU is gated too because RAM alone is not a
proxy for decode speed) as an automatic accuracy upgrade. History: the default
rose tiny→base (2026-07-09) then
base→small (2026-07-10), and the high tier was added (2026-07-11) — ``tiny`` and
``base`` both mis-transcribed real German/accented voice notes often enough to
be a recurring support issue, even though both pass ``corvin-voice doctor``'s
clean-fixture round-trip; ``small`` is the accuracy floor for the zero-config
promise while still fitting a 4 GB machine, and ``medium`` lifts accuracy
further where the hardware allows. Other options: any name from
``pywhispercpp.constants.AVAILABLE_MODELS``, e.g. ``"tiny-q5_1"``,
``"small"``, ``"medium"``, ``"large-v3"``, and their ``.en``-only variants.
Model files cache under
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
import re
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


# whisper.cpp emits bracketed NON-SPEECH markers for silence/noise segments
# (``[BLANK_AUDIO]``, ``[ Silence ]``, ``[MUSIC]`` …). Left in, an empty/near-empty
# voice note surfaces to the model — and the user — as literal "[BLANK_AUDIO]"
# (the reported "[BLANK_AUDIO] HELLO"). Strip ONLY this known, closed set of
# artifact tokens (never free-form bracketed text the user actually spoke).
_NONSPEECH_MARKER_RE = re.compile(
    r"[\[(]\s*(?:blank[\s_]?audio|silence|no[\s_]?speech|music|sound|noise|"
    r"inaudible|unintelligible|applause|laughter|pause|beep|click|typing|"
    r"footsteps|coughing|breathing)\s*[\])]",
    re.IGNORECASE,
)


def _strip_nonspeech_markers(text: str) -> str:
    """Remove whisper.cpp non-speech artifact tokens, collapse the whitespace
    they leave behind. Faithful: only the closed marker set above is touched."""
    cleaned = _NONSPEECH_MARKER_RE.sub(" ", text)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


# Default local STT model, chosen for TRANSCRIPTION QUALITY on the fresh,
# keyless install (the product's zero-config voice promise). `base` mis-
# transcribes German/accented/noisy audio badly — the field failure that
# motivated raising tiny→base still bites at `base`. `small-q5_1` (~190 MB on
# disk, ~500 MB peak RAM) is a large accuracy jump, especially non-English, and
# still fits a 4 GB machine. On genuinely RAM-starved boxes we fall back to
# `base-q5_1` automatically (see `_default_local_model`). `medium` is
# deliberately NOT the floor default — too heavy for the "unknown grandma
# machine" guarantee — but a capable, high-RAM box DOES get it as an automatic
# accuracy upgrade (see `_STT_MODEL_HIGH` below). Override any tier with
# CORVIN_STT_LOCAL_MODEL.
_STT_MODEL_HIGH = "medium-q5_0"     # ~539 MB disk / ~1.5 GB peak — best accuracy
_STT_MODEL_QUALITY = "small-q5_1"   # ~190 MB disk / ~500 MB peak — the default
_STT_MODEL_LOWRAM = "base-q5_1"     # ~57 MB — weakest-hardware floor

# Three automatic tiers by available RAM (multilingual q5 models only — never
# `.en`, we must transcribe German). Explicit CORVIN_STT_LOCAL_MODEL wins.
#   < 3 GB   → base   (stay responsive on a 2–4 GB box instead of swapping)
#   3–16 GB  → small  (the zero-config quality default)
#   ≥ 16 GB AND ≥ 8 CPUs → medium (best accuracy, but ONLY on a box that can
#                      also decode it inside the STT budget — RAM alone is not a
#                      proxy for CPU speed: a 16 GB / 4-slow-core mini-PC would
#                      time out on medium and return NOTHING, strictly worse
#                      than small's "poor but present" transcription).
# The RAM figure is cgroup-aware (see `_total_ram_mb`): a memory-limited
# container reports its LIMIT, not the host's physical RAM, so a 2 GB container
# on a 64 GB host never picks the ~1.5 GB-peak medium model and gets OOM-killed.
_STT_LOWRAM_THRESHOLD_MB = 3000
_STT_HIGHRAM_THRESHOLD_MB = 16000
_STT_HIGH_MIN_CPUS = 8


def _read_cgroup_str(path: str) -> "str | None":
    """Read a single cgroup control file, stripped, or None on any error."""
    try:
        with open(path, "r", encoding="ascii", errors="ignore") as fh:
            return fh.read().strip()
    except OSError:
        return None


def _read_cgroup_int(path: str) -> "int | None":
    """Read a cgroup control file as a positive, non-sentinel int, or None.

    Returns None for missing/blank files, the literal ``max`` (v2 unlimited),
    non-numeric contents, and the exabyte-range sentinels v1 uses for
    "unlimited" (``val >= 1<<62``)."""
    raw = _read_cgroup_str(path)
    if raw is None or raw == "" or raw == "max":
        return None
    try:
        val = int(raw)
    except ValueError:
        return None
    if val <= 0 or val >= (1 << 62):
        return None
    return val


def _cgroup_self_dirs() -> "list[str]":
    """Directories of THIS process's own cgroup, tightest (deepest) first,
    walking UP to the mount root.

    Why walk the hierarchy: a systemd ``MemoryMax=`` / ``CPUQuota=`` on the
    unit, a Docker ``--cgroupns=host``, or a K8s parent-cgroup limit all sit
    at ``/sys/fs/cgroup/<...deep path...>/memory.max`` — NOT at the namespace
    root ``/sys/fs/cgroup/memory.max`` (which is usually absent/``max`` for
    the root cgroup). Reading only the root therefore reports "unlimited" and
    the RAM/CPU gate picks the heavy tier and gets OOM-killed / times out.
    We resolve the process's own cgroup from ``/proc/self/cgroup`` (the
    unified ``0::<path>`` line on cgroup-v2) and yield every ancestor dir so
    the caller can take the TIGHTEST real limit anywhere in the chain.

    cgroup-v2 unified only (the modern default). Returns [] on non-Linux, on
    legacy v1-only hosts (handled separately below), or when unresolvable.
    """
    raw = _read_cgroup_str("/proc/self/cgroup")
    if not raw:
        return []
    rel = None
    for line in raw.splitlines():
        parts = line.split(":", 2)
        # Unified cgroup-v2 line: "0::<path>".
        if len(parts) == 3 and parts[0] == "0" and parts[1] == "":
            rel = parts[2].strip()
            break
    if rel is None:
        return []
    base = "/sys/fs/cgroup"
    segs = [s for s in rel.split("/") if s]
    dirs: list[str] = []
    for i in range(len(segs), -1, -1):  # deepest → root
        dirs.append(os.path.join(base, *segs[:i]) if segs[:i] else base)
    return dirs


def _cgroup_v1_dirs(controller: str) -> "list[str]":
    """Ancestor dirs for a legacy cgroup-v1 *controller*, deepest first."""
    raw = _read_cgroup_str("/proc/self/cgroup")
    if not raw:
        return []
    rel = None
    for line in raw.splitlines():
        parts = line.split(":", 2)
        # v1 line: "N:<controllers>:<path>"; controllers is comma-joined.
        if len(parts) == 3 and parts[0] != "0" and controller in parts[1].split(","):
            rel = parts[2].strip()
            break
    if rel is None:
        return []
    base = f"/sys/fs/cgroup/{controller}"
    segs = [s for s in rel.split("/") if s]
    dirs: list[str] = []
    for i in range(len(segs), -1, -1):
        dirs.append(os.path.join(base, *segs[:i]) if segs[:i] else base)
    return dirs


def _cgroup_limit_mb() -> "int | None":
    """Tightest cgroup memory limit in MB anywhere in THIS process's cgroup
    hierarchy, or None when unlimited / not containerized / not Linux.

    Walks the process's own cgroup up to the root (``_cgroup_self_dirs`` for
    v2 ``memory.max``, ``_cgroup_v1_dirs`` for v1 ``memory.limit_in_bytes``)
    and takes the MINIMUM real limit — so a systemd ``MemoryMax=`` on the unit
    or a K8s parent-cgroup cap is honoured, not just the (usually-unlimited)
    namespace root. ``SC_PHYS_PAGES`` is blind to all of these; without this a
    memory-capped container reads the host's full RAM and picks a model too
    big for its limit (OOM)."""
    best: "int | None" = None
    for d in _cgroup_self_dirs():
        val = _read_cgroup_int(os.path.join(d, "memory.max"))
        if val is not None:
            best = val if best is None else min(best, val)
    for d in _cgroup_v1_dirs("memory"):
        val = _read_cgroup_int(os.path.join(d, "memory.limit_in_bytes"))
        if val is not None:
            best = val if best is None else min(best, val)
    if best is None:
        return None
    return int(best / (1024 * 1024))


def _cgroup_cpu_quota() -> "int | None":
    """Effective CPU count from the tightest cgroup CPU quota in THIS
    process's hierarchy, or None when unlimited / unmeasurable / not Linux.

    cgroup-v2 ``cpu.max`` is ``"<quota> <period>"`` (quota ``max`` =
    unlimited); v1 uses ``cpu.cfs_quota_us`` / ``cpu.cfs_period_us`` (quota
    ``-1`` = unlimited). ``effective_cpus = floor(quota / period)`` (>= 1).
    ``os.cpu_count()`` reports the host's cores and is blind to the quota, so
    a box throttled to a fraction of a core would still pick the heavy tier
    and time out decoding it. Taking the MINIMUM across the hierarchy honours
    a systemd ``CPUQuota=`` on the unit as well as a parent-cgroup cap."""
    best: "float | None" = None
    for d in _cgroup_self_dirs():
        raw = _read_cgroup_str(os.path.join(d, "cpu.max"))
        if not raw:
            continue
        parts = raw.split()
        if len(parts) != 2 or parts[0] == "max":
            continue
        try:
            quota, period = float(parts[0]), float(parts[1])
        except ValueError:
            continue
        if quota > 0 and period > 0:
            cpus = quota / period
            best = cpus if best is None else min(best, cpus)
    for d in _cgroup_v1_dirs("cpu"):
        q = _read_cgroup_int(os.path.join(d, "cpu.cfs_quota_us"))
        p = _read_cgroup_int(os.path.join(d, "cpu.cfs_period_us"))
        if q is None or p is None:
            continue
        cpus = q / p
        best = cpus if best is None else min(best, cpus)
    if best is None:
        return None
    return max(1, int(best))  # floor, but never below one whole CPU


def _total_ram_mb() -> "int | None":
    """Best-effort usable RAM in MB, cross-platform, no hard deps.

    POSIX: os.sysconf. Windows: GlobalMemoryStatusEx via ctypes. On Linux the
    host figure is clamped to the cgroup memory limit (`_cgroup_limit_mb`) so a
    containerized bridge sizes the model to its container, not the host.
    Returns None when it can't be determined — callers then assume "enough"
    (pick quality), matching the installer's conservative-default posture.
    """
    host: "int | None" = None
    try:
        import os as _os
        if hasattr(_os, "sysconf") and "SC_PHYS_PAGES" in _os.sysconf_names and "SC_PAGE_SIZE" in _os.sysconf_names:
            host = int(_os.sysconf("SC_PHYS_PAGES") * _os.sysconf("SC_PAGE_SIZE") / (1024 * 1024))
    except (ValueError, OSError, AttributeError):
        host = None
    if host is None:
        try:
            import ctypes

            class _MEMSTAT(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MEMSTAT()
            stat.dwLength = ctypes.sizeof(_MEMSTAT)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):  # type: ignore[attr-defined]
                host = int(stat.ullTotalPhys / (1024 * 1024))
        except (AttributeError, OSError):
            host = None
    limit = _cgroup_limit_mb()
    if host is None:
        return limit
    if limit is None:
        return host
    return min(host, limit)


def _default_local_model() -> str:
    """The shipped default model, adapted to the host (3 tiers).

    Gated on BOTH usable RAM and CPU count so the heavy `medium` tier is only
    picked where the box can actually decode it inside the STT budget. Unknown
    RAM → the quality default (never the heavy tier): matches the installer's
    conservative-default posture and never strands a box we couldn't measure on
    a model too big for it.
    """
    import os as _os
    ram = _total_ram_mb()
    if ram is None:
        return _STT_MODEL_QUALITY
    if ram < _STT_LOWRAM_THRESHOLD_MB:
        return _STT_MODEL_LOWRAM
    # Effective CPUs = host cores clamped to any cgroup CPU quota. `cpu_count()`
    # is host-visible and blind to a `CPUQuota=` / `--cpus` / cfs cap, so a box
    # throttled to a few effective cores would otherwise pick the heavy `medium`
    # tier and time out decoding it (returning NOTHING — worse than `small`).
    cpus = _os.cpu_count() or 1
    quota = _cgroup_cpu_quota()
    if quota is not None:
        cpus = min(cpus, quota)
    if ram >= _STT_HIGHRAM_THRESHOLD_MB and cpus >= _STT_HIGH_MIN_CPUS:
        return _STT_MODEL_HIGH
    return _STT_MODEL_QUALITY


# Back-compat alias — some call sites / tests reference _DEFAULT_MODEL directly.
_DEFAULT_MODEL = _STT_MODEL_QUALITY


def _resolve_ffmpeg() -> "str | None":
    """Locate ffmpeg: FFMPEG_BIN env → PATH → bundled imageio-ffmpeg.

    Mirrors the adapter's TTS-side `_resolve_ffmpeg_bin()`. The installer
    deliberately skips system ffmpeg on Windows, so without the bundled
    fallback every real voice note (.ogg/.opus — never a 16-kHz WAV) failed
    local STT on a fresh Windows install (ADR-0185 review finding).
    """
    import shutil as _shutil  # noqa: PLC0415
    ffmpeg_bin = os.environ.get("FFMPEG_BIN") or _shutil.which("ffmpeg")
    if ffmpeg_bin:
        return ffmpeg_bin
    try:
        import imageio_ffmpeg  # noqa: PLC0415
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None


def _ensure_wav_16k(audio_path: Path, budget: float | None = None) -> "tuple[Path, bool]":
    """Return a 16-kHz mono 16-bit WAV path for whisper.cpp, converting if
    needed. Second element: True when a temp file was created (caller
    deletes it).

    pywhispercpp's own non-WAV path shells out to a bare PATH-``ffmpeg`` —
    unavailable on stock Windows — and its WAV reader rejects anything that
    is not exactly 16 kHz/16-bit. Converting here with the robustly resolved
    ffmpeg keeps the vendored code entirely on its dependency-free WAV path.

    ``budget`` caps the ffmpeg conversion so it stays inside the caller's
    overall transcription budget (a fixed 120s cap let a tiny
    BRIDGE_TRANSCRIBE_TIMEOUT still block the turn thread for two minutes).
    """
    import subprocess as _sp  # noqa: PLC0415
    import tempfile as _tf  # noqa: PLC0415
    import wave as _wave  # noqa: PLC0415

    p = Path(audio_path)
    # Case-SENSITIVE ".wav": pywhispercpp dispatches on `endswith('.wav')`
    # (case-sensitive), so a passthrough of "NOTE.WAV" would land in its
    # bare-PATH-ffmpeg branch and fail on stock Windows — the exact hole
    # this helper closes. An uppercase extension is converted (its temp
    # output is lowercase .wav) rather than passed through.
    if str(p).endswith(".wav"):
        try:
            with _wave.open(str(p), "rb") as wf:
                if (wf.getframerate() == 16000
                        and wf.getnchannels() in (1, 2)
                        and wf.getsampwidth() == 2):
                    return p, False
        except Exception:  # noqa: BLE001
            pass  # unreadable/nonstandard header — convert below

    ffmpeg = _resolve_ffmpeg()
    if ffmpeg is None:
        raise STTTranscriptionFailed(
            "ffmpeg not found (checked FFMPEG_BIN, PATH, bundled "
            "imageio-ffmpeg) — required to decode this audio format for "
            "local transcription"
        )
    tmp = _tf.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    # Cap conversion inside the caller's budget (fall back to 120s only when
    # unbounded); never below 1s so a near-exhausted budget still attempts it.
    _conv_timeout = 120.0 if budget is None else max(1.0, min(budget, 120.0))
    try:
        _sp.run(
            [ffmpeg, "-i", str(p), "-ac", "1", "-ar", "16000",
             "-acodec", "pcm_s16le", tmp.name, "-y"],
            check=True, stdout=_sp.DEVNULL, stderr=_sp.PIPE, timeout=_conv_timeout,
        )
    except Exception as exc:  # noqa: BLE001
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        stderr_tail = ""
        if isinstance(exc, _sp.CalledProcessError) and exc.stderr:
            stderr_tail = f": {exc.stderr[-300:]!r}"
        raise STTTranscriptionFailed(
            f"audio conversion for local transcription failed "
            f"({type(exc).__name__}){stderr_tail}"
        ) from exc
    return Path(tmp.name), True
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


def _get_logger():
    import logging  # noqa: PLC0415
    return logging.getLogger("corvin.stt.local")


def _explicit_model_env_set() -> bool:
    """True when the operator pinned CORVIN_STT_LOCAL_MODEL explicitly.

    A pinned model is honored verbatim (download it if missing) — the
    offline fallback only applies to the shipped DEFAULT, whose value
    changed across versions (tiny-q5_1 → base-q5_1) and must not strand
    an install that only has the old default on disk.
    """
    return bool(os.environ.get("CORVIN_STT_LOCAL_MODEL", "").strip())


def _model_file_present(size: str) -> bool:
    """Whether the ggml file for ``size`` is already downloaded on disk.

    pywhispercpp caches as ``ggml-<size>.bin``; a hand-placed
    ``<size>.bin`` / ``<size>`` (resolve_model_path's preferred shapes)
    also counts as present.
    """
    d = _models_dir()
    for name in (f"ggml-{size}.bin", f"{size}.bin", size):
        p = d / name
        try:
            if p.is_file() and p.stat().st_size > 0:
                return True
        except OSError:
            continue
    return False


# Accuracy rank of the whisper model families, worst → best. Used to pick the
# BEST already-present model as the offline fallback instead of the
# alphabetically-first one (``base`` sorts before ``medium``/``small`` — the
# old ``sorted()`` pick silently selected the weakest model on disk even when a
# better one sat right next to it, defeating the tier ladder).
_MODEL_FAMILY_RANK = {"tiny": 0, "base": 1, "small": 2, "medium": 3, "large": 4}


def _model_family_rank(model_name: str) -> int:
    """Accuracy rank for a model name (``small-q5_1`` → family ``small``)."""
    fam = model_name.split("-", 1)[0].split(".", 1)[0].lower()
    return _MODEL_FAMILY_RANK.get(fam, -1)


def _first_present_model() -> "str | None":
    """Name of the BEST already-downloaded pywhispercpp model, or None.

    Used as the offline-safe fallback when the configured default model
    file is absent. Picks the highest-accuracy family present on disk
    (deterministic: family rank, then name) so a lingering ``base`` never
    shadows a better ``small``/``medium`` in the same directory.
    """
    d = _models_dir()
    try:
        entries = sorted(d.glob("ggml-*.bin"))
    except OSError:
        return None
    present: list[str] = []
    for p in entries:
        try:
            if p.is_file() and p.stat().st_size > 0:
                # ggml-<size>.bin → <size>
                present.append(p.name[len("ggml-"):-len(".bin")])
        except OSError:
            continue
    if not present:
        return None
    # Highest family rank wins; ties broken by name for determinism.
    return max(present, key=lambda m: (_model_family_rank(m), m))


# Conservative on-disk floor sizes (bytes) for the ggml q5 model families. A
# file FAR below its family's real size is a truncated / aborted download and
# worth re-fetching; a full-size file that still won't load is NOT truncation
# (malloc failure, ABI/CPU mismatch, ggml-format version drift) and must NOT be
# re-downloaded on every voice note. Thresholds are deliberately well under the
# true sizes (medium-q5_0 ~539 MB, small-q5_1 ~190 MB, base-q5_1 ~57 MB) so only
# a clearly-partial file trips the "truncated" branch. Unknown family → treated
# as NOT truncated (never re-download something we can't size-check).
_MODEL_MIN_PLAUSIBLE_BYTES = {
    "tiny": 20 * 1024 * 1024,
    "base": 40 * 1024 * 1024,
    "small": 130 * 1024 * 1024,
    "medium": 400 * 1024 * 1024,
    "large": 900 * 1024 * 1024,
}


def _looks_truncated(model_file: Path, size: str) -> bool:
    """True iff *model_file* is implausibly small for its model family — the
    signature of an aborted/partial download, the only load failure a
    re-download can actually repair (VOICE-F3)."""
    fam = size.split("-", 1)[0].split(".", 1)[0].lower()
    floor = _MODEL_MIN_PLAUSIBLE_BYTES.get(fam)
    if floor is None:
        return False
    try:
        return model_file.stat().st_size < floor
    except OSError:
        return False


def _plan_model_heal(size: str, model_file: Path, now: float) -> "tuple[bool, str]":
    """Decide whether a failed model load warrants a quarantine+re-download
    self-heal (VOICE-F3).

    Returns ``(healable, detail)``. Heals ONLY when the file is present, looks
    TRUNCATED for its family, AND we haven't already healed it inside the
    current ``_HEAL_COOLDOWN_S`` window — bounding downloads to
    ``_HEAL_MAX_HEALS`` per window so a full-size-but-unloadable model (malloc
    failure, ABI/CPU mismatch, ggml version drift) is never re-fetched on every
    voice note. When it returns True it has ALREADY recorded the attempt, so a
    crash mid-download still consumes the window's budget. ``detail`` explains
    the give-up reason for the surfaced error. Callers hold ``_load_lock``, so
    the ``_heal_attempts`` read-modify-write is already serialized."""
    heal_count, heal_ts = _heal_attempts.get(size, (0, 0.0))
    if now - heal_ts > _HEAL_COOLDOWN_S:
        heal_count = 0  # cooldown elapsed → window resets
    if model_file.exists() and _looks_truncated(model_file, size) and heal_count < _HEAL_MAX_HEALS:
        _heal_attempts[size] = (heal_count + 1, now)
        return True, ""
    if not model_file.exists():
        return False, " (model file absent)"
    if not _looks_truncated(model_file, size):
        return False, " (full-size file; not a truncated download)"
    return False, " (already re-downloaded once this window)"


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

# Serializes actual inference on the shared model singleton. whisper.cpp
# contexts are NOT thread-safe: pywhispercpp's Model mutates shared _params
# (_set_params / assign_abort_callback) and whisper_full() runs on one C
# context — two adapter worker threads transcribing concurrently is undefined
# behaviour (segfault, or segments of user A bleeding into user B's turn).
# Inference is CPU-bound anyway, so serializing costs latency only under
# concurrent voice notes; waiting time is charged against the caller's budget
# and fails as STTTimeout, never blocks unbounded.
_transcribe_lock = threading.Lock()

# Corrupt-model self-heal throttle (VOICE-F3). Maps model size → (heal_count,
# last_heal_monotonic). A model may be quarantined+re-downloaded at most
# `_HEAL_MAX_HEALS` times per `_HEAL_COOLDOWN_S` window; past that we give up
# and raise STTProviderUnavailable (resolver falls through to cloud) instead of
# re-fetching hundreds of MB on every single voice note. Only ever mutated
# inside `_do_load`, which runs while `_load_lock` is held, so no extra lock is
# needed.
_HEAL_COOLDOWN_S = 3600.0
_HEAL_MAX_HEALS = 1
_heal_attempts: "dict[str, tuple[int, float]]" = {}


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
        size = os.environ.get("CORVIN_STT_LOCAL_MODEL", "").strip() or _default_local_model()
        # Offline-safe model selection. When 453f026 raised the default from
        # tiny-q5_1 to base-q5_1, an upgraded install that already has ONLY the
        # old tiny model (autoupdate/pip don't run the model downloader) would,
        # on the first voice note, try an in-band download of base-q5_1 — which
        # never completes on an air-gapped/offline box, leaving previously-
        # working voice input permanently dead. If the configured model file is
        # absent but ANOTHER already-downloaded model is present, use that
        # instead of forcing a download that offline cannot satisfy. Only the
        # pywhispercpp engine caches ggml files this way; faster-whisper manages
        # its own cache, so this fallback is scoped to the default engine below.
        if not _explicit_model_env_set() and not _model_file_present(size):
            _fallback = _first_present_model()
            if _fallback is not None and _fallback != size:
                log = _get_logger()
                log.warning(
                    "local STT: configured model %r not on disk; using "
                    "already-present %r instead of an in-band download "
                    "(offline-safe). Run `corvin-voice doctor` to fetch %r.",
                    size, _fallback, size,
                )
                size = _fallback
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
            # STTProviderUnavailable, NOT STTTimeout: "another caller's load/
            # download is still in progress" is a structural local-provider
            # condition — the resolver must fall through to the next provider
            # (e.g. OpenAI) instead of failing the turn. A stalled first-use
            # download once turned every subsequent voice note into a hard
            # timeout despite a configured cloud fallback (review finding).
            raise STTProviderUnavailable(
                f"local STT model load/download still in progress after "
                f"{budget}s — treating local as unavailable for this call"
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

                def _construct() -> object:
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

                try:
                    return _construct()
                except Exception as exc:  # noqa: BLE001
                    # Self-heal a TRUNCATED model file — but ONLY that, and at
                    # most once per cooldown window (VOICE-F3). An aborted first
                    # download (Ctrl-C, power loss, dropped connection) leaves a
                    # half-written ggml file that every later check short-
                    # circuits on (exists()/st_size>0 both pass), so local STT
                    # would stay broken forever; quarantining + re-downloading
                    # fixes that. But the OLD code re-downloaded on ANY load
                    # failure (malloc, ABI/CPU mismatch, ggml version drift) —
                    # none of which a re-download repairs — so a persistently-
                    # unloadable full-size model re-fetched ~539 MB on EVERY
                    # voice note, unbounded. We now re-download only when the
                    # file is implausibly small for its family AND we haven't
                    # already healed it this window; otherwise we give up and
                    # raise STTProviderUnavailable so the resolver falls through
                    # to the next provider (e.g. OpenAI) instead of looping
                    # downloads.
                    model_file = _models_dir() / f"ggml-{size}.bin"
                    healable, detail = _plan_model_heal(
                        size, model_file, time.monotonic())
                    if not healable:
                        raise STTProviderUnavailable(
                            f"pywhispercpp model {size!r} could not be loaded"
                            f"{detail}: {exc}"
                        ) from exc
                    quarantine = model_file.with_name(model_file.name + ".corrupt")
                    try:
                        model_file.replace(quarantine)
                    except OSError:
                        raise STTProviderUnavailable(
                            f"pywhispercpp model {size!r} appears truncated but "
                            f"the file could not be quarantined: {exc}"
                        ) from exc
                    try:
                        return _construct()
                    except Exception as exc2:  # noqa: BLE001
                        raise STTProviderUnavailable(
                            f"pywhispercpp model {size!r} failed to load even "
                            f"after re-downloading a truncated model file: {exc2}"
                        ) from exc2

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
                # STTProviderUnavailable (not STTTimeout) so the resolver
                # falls through to the next provider — see the acquire-
                # timeout branch above for the rationale.
                raise STTProviderUnavailable(
                    f"local STT model load/download exceeded the {budget}s "
                    f"budget (likely a stalled network connection fetching "
                    f"the model) — treating local as unavailable for this call"
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

        # Convert BEFORE taking the inference lock — conversion is I/O-bound
        # and safe to run concurrently; only whisper.cpp inference needs
        # serialization. Charge conversion time against the budget so the
        # combined wall time stays inside the caller's budget, not ~2×.
        _conv_start = time.monotonic()
        wav_path, _converted = _ensure_wav_16k(Path(audio_path), budget=budget)
        _remaining = budget - (time.monotonic() - _conv_start)
        if _remaining <= 0:
            if _converted:
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass
            raise STTProviderUnavailable(
                f"audio conversion consumed the entire {budget}s local STT "
                f"budget — treating local as unavailable for this call"
            )
        try:
            return self._transcribe_pywhispercpp_wav(
                model, wav_path, orig_path=Path(audio_path),
                lang=lang, budget=_remaining,
            )
        finally:
            if _converted:
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass

    def _transcribe_pywhispercpp_wav(
        self, model, wav_path: Path, *, orig_path: Path,
        lang: str | None, budget: float,
    ) -> TranscriptResult:
        wait_start = time.monotonic()
        if not _transcribe_lock.acquire(timeout=budget):
            # STTProviderUnavailable (not STTTimeout): "another caller holds
            # the shared model" is the same structural local-busy condition
            # as the load-lock branch — the resolver must fall through to the
            # next provider (OpenAI), not fail the turn. Keeping STTTimeout
            # here left concurrent voice notes hard-failing despite a
            # configured cloud fallback (round-2 review finding).
            raise STTProviderUnavailable(
                f"local transcription busy (concurrent call held the model "
                f">{budget}s) — treating local as unavailable for this call"
            )
        try:
            remaining = budget - (time.monotonic() - wait_start)
            if remaining <= 0:
                raise STTProviderUnavailable(
                    f"waiting for a concurrent local transcription consumed "
                    f"the entire {budget}s budget — treating local as "
                    f"unavailable for this call"
                )
            t0 = time.monotonic()
            aborted = False

            def _abort_check() -> bool:
                nonlocal aborted
                if time.monotonic() - t0 > remaining:
                    aborted = True
                    return True
                return False

            # Always pass `language` EXPLICITLY — the model is a process-wide
            # singleton (_MODEL_CACHE) and pywhispercpp's `_set_params` persists
            # every override "for future calls". Omitting the arg on a no-hint
            # call leaves a PREVIOUS call's `language="de"` pinned, so a later
            # English note force-decodes as German (and lang-id then reports the
            # pinned language, masking it). "auto" resets the singleton to
            # whisper.cpp's normal auto-detect decode pass. (Do NOT use the
            # detect_language flag: it runs a detect-only pass and returns ZERO
            # segments, silently dropping the transcript.)
            kwargs: dict = {
                "language": lang if (lang and lang != "auto") else "auto",
            }

            try:
                segments = model.transcribe(
                    str(wav_path), abort_callback=_abort_check, **kwargs,
                )
            except FileNotFoundError as exc:
                raise STTTranscriptionFailed(
                    f"audio file not found: {orig_path}"
                ) from exc
            except Exception as exc:  # noqa: BLE001
                raise STTTranscriptionFailed(
                    f"local whisper (pywhispercpp) failed: {exc}"
                ) from exc

            if aborted:
                raise STTTimeout(f"local whisper exceeded budget {remaining}s")

            text = _strip_nonspeech_markers("".join(seg.text for seg in segments))

            detected_lang = lang if (lang and lang != "auto") else None
            if detected_lang is None:
                # Best-effort: whisper.cpp exposes the auto-detected language
                # id via a direct C-binding call on the model's context — it
                # reads state from the run above, so it MUST stay inside the
                # inference lock. Never let this fail the transcription —
                # absence of a language tag is an acceptable degradation.
                try:
                    import _pywhispercpp as _pw  # type: ignore[import-not-found]
                    lang_id = _pw.whisper_full_lang_id(model._ctx)  # noqa: SLF001
                    detected_lang = _pw.whisper_lang_str(lang_id)
                except Exception:  # noqa: BLE001
                    detected_lang = None
        finally:
            _transcribe_lock.release()

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

        text = _strip_nonspeech_markers("".join(pieces))
        detected_lang = getattr(info, "language", None) or lang or None
        duration = getattr(info, "duration", None)
        return TranscriptResult(
            text=text,
            provider=self.name,
            lang=detected_lang,
            duration_s=float(duration) if duration is not None else None,
        )


assert isinstance(LocalWhisperProvider(), STTProvider)
