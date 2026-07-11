"""RAM+CPU-adaptive local-STT model tiers (2026-07-11).

The local Whisper provider picks its default GGML model by USABLE RAM
(cgroup-aware) and CPU count across three tiers — base (< 3 GB), small
(3–16 GB, or ≥ 16 GB but < 8 CPUs), medium (≥ 16 GB AND ≥ 8 CPUs). The
installer step (corvinOS/installer/steps/stt.py) prefetches the SAME model, so
these tests pin the tier boundaries, the CPU gate, the cgroup clamp, and the
provider↔installer mirror that keeps install-time download and runtime load
from ever diverging.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

import pytest

_REPO = Path(__file__).resolve().parents[1]
for _p in (_REPO, _REPO / "operator" / "voice" / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from stt import local_whisper as lw  # type: ignore  # noqa: E402
from corvinOS.installer.steps import stt as installer_stt  # noqa: E402


# ── provider: three RAM tiers (with plenty of CPUs so the high tier is reachable)


@pytest.mark.parametrize(
    "ram_mb, expected",
    [
        (None, "small-q5_1"),      # unmeasurable → safe quality default, never heavy
        (1024, "base-q5_1"),       # 1 GB → floor
        (2999, "base-q5_1"),       # just under the low threshold
        (3000, "small-q5_1"),      # exactly the low threshold → quality
        (8000, "small-q5_1"),      # typical laptop
        (15999, "small-q5_1"),     # just under the high threshold
        (16000, "medium-q5_0"),    # exactly the high threshold + enough CPUs → medium
        (32000, "medium-q5_0"),    # workstation
    ],
)
def test_default_local_model_ram_tiers(ram_mb, expected):
    # Pin the CPU quota to unlimited so a constrained CI runner's real
    # cgroup CPUQuota can't drag a medium-expecting tier down to small.
    with mock.patch.object(lw, "_total_ram_mb", return_value=ram_mb), \
         mock.patch.object(lw, "_cgroup_cpu_quota", return_value=None), \
         mock.patch.object(lw.os, "cpu_count", return_value=16):
        assert lw._default_local_model() == expected


def test_high_ram_but_too_few_cpus_stays_on_small():
    """A 32 GB / 4-slow-core mini-PC must NOT get medium — it would time out
    decoding it and return nothing (worse than small's poor-but-present)."""
    with mock.patch.object(lw, "_total_ram_mb", return_value=32000), \
         mock.patch.object(lw.os, "cpu_count", return_value=4):
        assert lw._default_local_model() == "small-q5_1"


def test_high_ram_exactly_min_cpus_gets_medium():
    with mock.patch.object(lw, "_total_ram_mb", return_value=16000), \
         mock.patch.object(lw, "_cgroup_cpu_quota", return_value=None), \
         mock.patch.object(lw.os, "cpu_count", return_value=lw._STT_HIGH_MIN_CPUS):
        assert lw._default_local_model() == "medium-q5_0"


def test_cpu_count_none_is_treated_as_one_cpu():
    with mock.patch.object(lw, "_total_ram_mb", return_value=64000), \
         mock.patch.object(lw.os, "cpu_count", return_value=None):
        assert lw._default_local_model() == "small-q5_1"


def test_explicit_env_overrides_every_tier(monkeypatch):
    """CORVIN_STT_LOCAL_MODEL must win regardless of RAM/CPU — the resolver
    reads it directly, so a big box can still be pinned to a tiny model."""
    monkeypatch.setenv("CORVIN_STT_LOCAL_MODEL", "tiny-q5_1")
    with mock.patch.object(lw, "_total_ram_mb", return_value=32000), \
         mock.patch.object(lw.os, "cpu_count", return_value=16):
        chosen = os.environ.get("CORVIN_STT_LOCAL_MODEL", "").strip() or lw._default_local_model()
    assert chosen == "tiny-q5_1"


# ── cgroup awareness ─────────────────────────────────────────────────────────


def test_total_ram_clamped_to_cgroup_limit():
    """A memory-limited container on a big host must report its LIMIT, so it
    never picks the ~1.5 GB-peak medium model and gets OOM-killed."""
    with mock.patch.object(lw, "_cgroup_limit_mb", return_value=2000):
        # host RAM is whatever this box has; the clamp must cap it at 2000
        assert lw._total_ram_mb() == 2000
    # and that limit lands on the low/quality tier, never medium
    with mock.patch.object(lw, "_cgroup_limit_mb", return_value=2000), \
         mock.patch.object(lw.os, "cpu_count", return_value=64):
        assert lw._default_local_model() == "base-q5_1"


def test_cgroup_unlimited_leaves_host_ram_untouched():
    with mock.patch.object(lw, "_cgroup_limit_mb", return_value=None):
        assert lw._total_ram_mb() == lw._total_ram_mb()  # no exception, returns host


# ── cgroup HIERARCHY walk (VOICE-F2) ─────────────────────────────────────────
# The limit that OOM-kills a systemd unit / Docker --cgroupns=host / K8s pod
# sits DEEP in the tree, not at the namespace root. The gate must walk the
# process's own cgroup up to the root and take the tightest real limit.


def test_cgroup_self_dirs_walks_up_to_root(monkeypatch):
    monkeypatch.setattr(
        lw, "_read_cgroup_str",
        lambda p: "0::/system.slice/corvin.service" if p == "/proc/self/cgroup" else None,
    )
    assert lw._cgroup_self_dirs() == [
        "/sys/fs/cgroup/system.slice/corvin.service",
        "/sys/fs/cgroup/system.slice",
        "/sys/fs/cgroup",
    ]


def test_cgroup_limit_finds_systemd_unit_limit_root_unlimited(tmp_path, monkeypatch):
    """A MemoryMax= on the unit while the root cgroup is 'max' — the OLD
    root-only read reported unlimited here and picked a too-big model (OOM)."""
    unit = tmp_path / "system.slice" / "corvin.service"
    unit.mkdir(parents=True)
    (unit / "memory.max").write_text("1073741824")            # 1 GiB
    (tmp_path / "system.slice" / "memory.max").write_text("max")
    (tmp_path / "memory.max").write_text("max")
    dirs = [str(unit), str(tmp_path / "system.slice"), str(tmp_path)]
    monkeypatch.setattr(lw, "_cgroup_self_dirs", lambda: dirs)
    monkeypatch.setattr(lw, "_cgroup_v1_dirs", lambda c: [])
    assert lw._cgroup_limit_mb() == 1024


def test_cgroup_limit_takes_tightest_across_levels(tmp_path, monkeypatch):
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    (deep / "memory.max").write_text(str(4 * 1024 * 1024 * 1024))   # 4 GiB
    (tmp_path / "a" / "memory.max").write_text(str(2 * 1024 * 1024 * 1024))  # 2 GiB (tightest)
    (tmp_path / "memory.max").write_text("max")
    dirs = [str(deep), str(tmp_path / "a"), str(tmp_path)]
    monkeypatch.setattr(lw, "_cgroup_self_dirs", lambda: dirs)
    monkeypatch.setattr(lw, "_cgroup_v1_dirs", lambda c: [])
    assert lw._cgroup_limit_mb() == 2048


def test_cgroup_all_unlimited_is_none(tmp_path, monkeypatch):
    (tmp_path / "memory.max").write_text("max")
    monkeypatch.setattr(lw, "_cgroup_self_dirs", lambda: [str(tmp_path)])
    monkeypatch.setattr(lw, "_cgroup_v1_dirs", lambda c: [])
    assert lw._cgroup_limit_mb() is None


# ── cgroup CPU quota (VOICE-F2) ──────────────────────────────────────────────


def test_cgroup_cpu_quota_reads_v2_cpu_max(tmp_path, monkeypatch):
    unit = tmp_path / "corvin.service"
    unit.mkdir()
    (unit / "cpu.max").write_text("200000 100000")   # 2 effective CPUs
    (tmp_path / "cpu.max").write_text("max 100000")  # root unlimited
    monkeypatch.setattr(lw, "_cgroup_self_dirs", lambda: [str(unit), str(tmp_path)])
    monkeypatch.setattr(lw, "_cgroup_v1_dirs", lambda c: [])
    assert lw._cgroup_cpu_quota() == 2


def test_cgroup_cpu_quota_unlimited_is_none(tmp_path, monkeypatch):
    (tmp_path / "cpu.max").write_text("max 100000")
    monkeypatch.setattr(lw, "_cgroup_self_dirs", lambda: [str(tmp_path)])
    monkeypatch.setattr(lw, "_cgroup_v1_dirs", lambda c: [])
    assert lw._cgroup_cpu_quota() is None


def test_cpu_quota_gates_medium_down_to_small():
    """32 GB + 16 host cores, but a cgroup CPUQuota of 2 → must NOT pick medium
    (it would time out decoding on 2 throttled cores and return nothing)."""
    with mock.patch.object(lw, "_total_ram_mb", return_value=32000), \
         mock.patch.object(lw, "_cgroup_cpu_quota", return_value=2), \
         mock.patch.object(lw.os, "cpu_count", return_value=16):
        assert lw._default_local_model() == "small-q5_1"


# ── provider ↔ installer mirror (drift guard) ────────────────────────────────


def test_installer_mirrors_provider_tier_constants():
    """The installer's fallback constants must match the provider SSOT exactly,
    or a high/low-RAM box would prefetch one model and load another."""
    assert installer_stt._STT_MODEL_HIGH == lw._STT_MODEL_HIGH
    assert installer_stt._STT_MODEL_QUALITY == lw._STT_MODEL_QUALITY
    assert installer_stt._STT_MODEL_LOWRAM == lw._STT_MODEL_LOWRAM
    assert installer_stt._STT_LOWRAM_THRESHOLD_MB == lw._STT_LOWRAM_THRESHOLD_MB
    assert installer_stt._STT_HIGHRAM_THRESHOLD_MB == lw._STT_HIGHRAM_THRESHOLD_MB
    assert installer_stt._STT_HIGH_MIN_CPUS == lw._STT_HIGH_MIN_CPUS


def test_installer_default_model_delegates_to_provider():
    """When the provider module is importable, the installer must return the
    provider's pick verbatim (true Single Source of Truth)."""
    with mock.patch.object(lw, "_total_ram_mb", return_value=16000), \
         mock.patch.object(lw, "_cgroup_cpu_quota", return_value=None), \
         mock.patch.object(lw.os, "cpu_count", return_value=16):
        assert installer_stt._default_model() == "medium-q5_0"


def test_installer_fallback_is_three_tier_with_cpu_gate(monkeypatch):
    """If the provider module can't be imported, the self-contained fallback
    must still honour all three tiers AND the CPU gate."""
    monkeypatch.setattr(installer_stt, "_provider_default_model", lambda: None)

    def fake_sysconf(name):
        if name == "SC_PHYS_PAGES":
            return 20000 * 1024 * 1024 // 4096  # ~20 GB in pages
        if name == "SC_PAGE_SIZE":
            return 4096
        raise ValueError(name)

    if not hasattr(os, "sysconf"):
        pytest.skip("no os.sysconf on this platform")
    monkeypatch.setattr(os, "sysconf", fake_sysconf)
    # 20 GB + many CPUs → medium
    monkeypatch.setattr(os, "cpu_count", lambda: 16)
    assert installer_stt._default_model() == "medium-q5_0"
    # 20 GB + few CPUs → small (CPU gate)
    monkeypatch.setattr(os, "cpu_count", lambda: 4)
    assert installer_stt._default_model() == "small-q5_1"
