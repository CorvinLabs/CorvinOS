"""ADR-2033 Blender automation — tools/blender_3d_scene_generator.py.

The build/render tests go through the real CLI (a subprocess) and the real
Blender binary: the concept YAML is validated on the host, Blender builds the
scenes headless, renders EXR frames and writes report.json. Skipped only when
no Blender is installed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "blender_3d_scene_generator.py"
CONCEPT = REPO / "learning_concepts" / "audit_chain.yaml"
sys.path.insert(0, str(TOOL.parent))
import blender_3d_scene_generator as gen  # noqa: E402

needs_blender = pytest.mark.skipif(shutil.which("blender") is None, reason="blender not installed")


def _cli(*args: str, timeout: int = 600) -> tuple[int, dict | None, str]:
    p = subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True, timeout=timeout)
    try:
        return p.returncode, json.loads(p.stdout), p.stderr
    except json.JSONDecodeError:
        return p.returncode, None, p.stderr + p.stdout


# ── concept validation (host side, no Blender) ───────────────────────────────

def test_shipped_concept_is_valid():
    c = gen.load_concept(CONCEPT)
    assert [s["template"] for s in c["scenes"]] == ["linked_chain", "hash_transformation", "checkmark_sequence"]
    assert c["scenes"][0]["template_params"] == {"num_items": 4, "spacing": 3.0}


@pytest.mark.parametrize("raw, msg", [
    ({"scenes": [{"template": "linked_chain"}]}, "name"),
    ({"name": "x", "scenes": []}, "scenes"),
    ({"name": "x", "scenes": [{"template": "teapot"}]}, "template"),
    ({"name": "x", "scenes": [{"template": "linked_chain", "template_params": {"num_items": 0}}]}, "num_items"),
    ({"name": "x", "scenes": [{"name": "a", "template": "linked_chain"}, {"name": "a", "template": "linked_chain"}]}, "duplicate"),
])
def test_invalid_concepts_are_rejected(raw, msg):
    with pytest.raises(gen.ConceptError, match=msg):
        gen.validate_concept(raw)


def test_bad_yaml_fails_before_blender_runs(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\nscenes:\n  - template: teapot\n")
    code, _, err = _cli("build", str(bad), "--out", str(tmp_path / "out"))
    assert code == 2 and "template" in err
    assert not (tmp_path / "out").exists()


# ── build + render through Blender ───────────────────────────────────────────

@pytest.fixture(scope="module")
def built(tmp_path_factory):
    if shutil.which("blender") is None:
        pytest.skip("blender not installed")
    out = tmp_path_factory.mktemp("render")
    code, report, err = _cli("build", str(CONCEPT), "--out", str(out), "--render",
                             "--frames", "1-2", "--resolution", "5", "--samples", "2")
    assert report is not None, err
    return code, report, out


@needs_blender
def test_linked_chain_creates_4_cubes(built):
    _code, report, _ = built
    chain = next(s for s in report["scenes"] if s["name"] == "chain")
    assert chain["objects"]["event_cube"] == 4
    assert chain["objects"]["link_arrow"] == 6  # 3 arrows = shaft + head each


@needs_blender
def test_camera_rotation_90_degrees(built):
    _code, report, _ = built
    by = {s["name"]: s for s in report["scenes"]}
    assert by["chain"]["camera_rotation_deg"] == pytest.approx(90.0)
    assert by["hash"]["camera_rotation_deg"] == pytest.approx(45.0)


@needs_blender
def test_scene_timing_follows_the_concept(built):
    _code, report, _ = built
    by = {s["name"]: s for s in report["scenes"]}
    assert (by["chain"]["frame_end"], by["hash"]["frame_end"]) == (180, 150)  # 6 s, 5 s @ 30 fps


@needs_blender
def test_render_output_is_valid_exr(built):
    code, report, out = built
    assert code == 0 and report["ok"], report.get("error")
    for s in report["scenes"]:
        assert s["pre_render_problems"] == [] and s["post_render_problems"] == []
        assert len(s["frames"]) == 2
        for f in s["frames"]:
            assert Path(f).read_bytes()[:4] == gen.EXR_MAGIC
    assert (out / "audit_chain.blend").is_file()


@needs_blender
def test_device_choice_is_recorded_never_silent(built):
    _code, report, _ = built
    d = report["device"]
    assert d["device"] in ("GPU", "CPU")
    if d["device"] == "CPU":
        assert d["fallback"] is True  # AUTO asked for a GPU; CPU is an explicit fallback
    assert "denoiser" in d
