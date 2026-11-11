"""T-0395 / ADR-2323 Decision 11 - the validators lifted into core/bundle_common.

The forge suite's own hostile fixtures (imported, not copied) go through the lifted functions directly, once with the
Forge Bundle limits and once with the project-bundle limits of ADR-2323 Decision 9; each must be refused at the same stage
the Forge Bundle validator refuses it. The forge aliases must still be importable, and refusals carry a content-free
``detail`` (the project bundle's messages must never echo the archive).
"""
from __future__ import annotations

import gzip
import io
import zipfile
from types import MappingProxyType

import pytest

from core.bundle_common import BundleCheckError
from core.bundle_common import names, ratio, secrets
from core.forge_bundle.envelope import LIMITS as FORGE_LIMITS
from tests.forge_bundle import test_validate_bundle as fv

#: ADR-2323 Decision 9 (the project bundle module pins these; the test of T-0396 checks the two agree)
PROJECT_LIMITS = MappingProxyType({**FORGE_LIMITS, "max_compressed_bytes": 500 << 20, "max_uncompressed_bytes": 2 << 30,
                                   "max_entries": 50_000, "max_envelope_bytes": 4 << 20, "max_name_chars": 1000,
                                   "max_name_bytes": 1000})
LIMIT_SETS = pytest.mark.parametrize("limits", [FORGE_LIMITS, PROJECT_LIMITS], ids=["forge", "project"])


def _container(data: bytes, limits) -> list[zipfile.ZipInfo]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return names.check_container(zf, where="bundle", limits=limits)


def _stage(fn, *a, **k) -> BundleCheckError:
    with pytest.raises(BundleCheckError) as exc:
        fn(*a, **k)
    return exc.value


@LIMIT_SETS
@pytest.mark.parametrize("name", [
    "artifacts/tool/x@1.0.0/../../../etc/passwd", "/etc/passwd", "artifacts\\tool\\evil.py", "C:/Windows/evil.py",
    "artifacts//double.py", "artifacts/tool/./x.py", "artifacts/tool/ctl\x01.py",
])
def test_unsafe_names_are_container_refusals_under_both_limit_sets(limits, name):
    data = fv.make_bundle(extra={name: b"x"})
    with pytest.raises(fv.BundleRejected) as forge:
        fv.validate_bundle(data)
    err = _stage(_container, data, limits)
    assert err.stage == forge.value.stage == "container"
    assert "\x01" not in err.detail and "passwd" not in err.detail and "Windows" not in err.detail


@LIMIT_SETS
def test_symlink_duplicate_and_bomb_entries(limits):
    buf = io.BytesIO(fv.make_bundle())
    with zipfile.ZipFile(buf, "a") as zf:
        info = zipfile.ZipInfo("artifacts/tool/link")
        info.external_attr = (0o120777) << 16
        zf.writestr(info, "/etc/passwd")
    assert _stage(_container, buf.getvalue(), limits).detail == "an entry is a symbolic link"
    dup = fv.make_bundle(extra={"artifacts/tool/A.py": b"1", "artifacts/tool/a.py": b"2"})
    assert _stage(_container, dup, limits).detail == "two entries have the same name"
    bomb = fv.make_bundle(extra={"artifacts/tool/zeros.bin": b"\0" * (8 * 1024 * 1024)})
    err = _stage(_container, bomb, limits)
    assert err.stage == "container" and "compressed more than" in err.detail
    # below the ratio floor nothing is refused at this stage (same as the forge positive control)
    assert _container(fv.make_bundle(extra={"artifacts/tool/zeros.bin": b"\0" * 10_000}), limits)


def test_too_many_entries_follow_the_limit_set():
    data = fv.make_bundle()
    assert _stage(_container, data, {**FORGE_LIMITS, "max_entries": 3}).detail == "too many entries"
    assert _container(data, PROJECT_LIMITS)


def test_name_length_limit_differs_between_the_sets():
    long = "artifacts/tool/" + "a" * 700
    data = fv.make_bundle(extra={long: b"x"})
    assert _stage(_container, data, FORGE_LIMITS).detail == "an entry name is empty or too long"
    _container(data, PROJECT_LIMITS)                         # 1000 bytes allowed for a project bundle
    assert _stage(names.check_name, "p/" + "é" * 600, "b", limits=PROJECT_LIMITS).detail == "an entry name is empty or too long"


def test_entry_rule_is_called_and_defaults_to_no_extra_rule():
    data = fv.make_bundle()
    seen = []

    def rule(info):
        seen.append(info.filename)
        return ("a protected entry", f"protected {info.filename!r}") if info.filename.endswith("spec.json") else None
    err = _stage(lambda: _container_rule(data, rule))
    assert err.detail == "a protected entry" and seen
    assert _container(data, FORGE_LIMITS)


def _container_rule(data, rule):
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return names.check_container(zf, where="bundle", limits=PROJECT_LIMITS, entry_rule=rule)


@LIMIT_SETS
def test_ratio_and_budget(limits):
    blob = gzip.compress(b"0" * 5_000_000)
    err = _stage(ratio.inflate, blob, "bomb.gz", ratio.Budget(limits["max_uncompressed_bytes"], limits=limits), limits=limits)
    assert err.stage == "container" and "expands more than" in err.reason
    small = ratio.Budget(10, limits=limits)
    assert _stage(small.spend, 11, "x").detail == "the content exceeds the uncompressed budget"
    assert ratio.inflate(gzip.compress(b"abc"), "ok.gz", ratio.Budget(1 << 20, limits=limits), limits=limits) == b"abc"
    assert ratio.inflate(b"plain", "p", ratio.Budget(1, limits=limits)) is None


@pytest.mark.parametrize("blob", [
    b"\xff\xfe junk " + fv.AWS + b" \x00\x81", ("x " + fv.AWS.decode()).encode("utf-16"),
    b'{"k": "\\u0041KIAABCDEFGHIJKLMNOP"}', b"\x80\x04\x95" + bytes([0xC4]) + fv.JWT + b"\x94.",
    ("\u3000" + fv.AWS.decode() + "\u3000").encode("utf-16-le"), b'KEY = "\\x41KIAABCDEFGHIJKLMNOP"\n',
])
def test_secret_shapes_are_found_in_every_view(blob):
    err = _stage(secrets.scan_bytes, blob, "notes.txt")
    assert err.stage == "secrets" and err.detail == "credential-shaped content"
    for marker in (b"AKIA", b"eyJ"):
        assert marker.decode() not in err.reason


def test_the_stream_scanner_finds_a_token_across_a_window_boundary_and_passes_clean_input():
    pad = b"a" * (secrets.WINDOW - 10)
    data = pad + fv.JWT + b"\n" + b"b" * 5000
    with pytest.raises(BundleCheckError):
        secrets.scan_stream(io.BytesIO(data), "big.txt", chunk=4096)
    secrets.scan_stream(io.BytesIO(b"x" * (3 * secrets.WINDOW)), "clean.txt", chunk=65536)
    secrets.scan_stream(io.BytesIO(b""), "empty.txt")


def test_forge_aliases_still_import_and_raise_the_forge_type():
    from core.forge_bundle.validate import BundleRejected, _check_container, _scan_bytes, _scan_text  # noqa: F401
    with pytest.raises(BundleRejected) as exc:
        _scan_text("token " + fv.JWT.decode(), "tool x")
    assert exc.value.stage == "secrets"
    assert isinstance(exc.value, BundleCheckError)


def test_forge_validate_imports_the_shared_package():
    import core.forge_bundle.validate as v
    assert v._names is names and v._ratio is ratio and v._secrets is secrets
