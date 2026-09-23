#!/usr/bin/env python3
"""CorvinOS 3D scene generator — YAML learning concept → Blender scene → frames.

ADR-2033 (Blender automation & rendering strategy), Loop A task #1.

One file, two sides:

* **Host side** (any Python with PyYAML): ``build`` loads and validates a
  concept YAML, then runs Blender headless on this same file:

      python tools/blender_3d_scene_generator.py build learning_concepts/audit_chain.yaml \
          [--out DIR] [--render] [--preview] [--frames 1-2] [--resolution 25] [--samples N]

  It prints the JSON report Blender wrote and exits non-zero on any failure.
  A render that crashes is retried up to twice (ADR-2033 §6).

* **Blender side** (``blender -b --python this_file -- inner ...``): builds
  every concept scene from templates, configures Cycles (GPU when Blender
  finds one, CPU otherwise — recorded in the report, never silent), validates
  the scene BEFORE rendering, renders EXR frames, validates the frames AFTER
  rendering, and saves the ``.blend``.

Classes (ADR-2033 "Code Structure"): ``SceneTemplate``,
``CorvinOS3DSceneGenerator``, ``RenderOrchestrator``, ``QualityValidator``.

Output (default): ``<CORVIN_HOME>/tenants/<tid>/video_library/<concept>/``
    <concept>.blend, frames/<scene>/frame_####.exr, report.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:  # Blender side only
    import bmesh  # type: ignore
    import bpy  # type: ignore
    from mathutils import Vector  # type: ignore
except ImportError:  # host side
    bpy = bmesh = Vector = None  # type: ignore

TEMPLATES = ("linked_chain", "hash_transformation", "checkmark_sequence")
DEFAULT_FPS = 30
EXR_MAGIC = b"\x76\x2f\x31\x01"
MAX_RENDER_ATTEMPTS = 3  # first try + 2 retries (ADR-2033 §6)


class ConceptError(ValueError):
    """The concept YAML is not buildable — fix the YAML (ADR-2033 §6a)."""


# ── Concept (host + Blender, no bpy) ─────────────────────────────────────────

def validate_concept(raw: Any) -> dict[str, Any]:
    """Normalise and validate a learning concept. Raises ConceptError."""
    if not isinstance(raw, dict):
        raise ConceptError("concept must be a mapping")
    name = raw.get("name") or raw.get("concept")
    if not isinstance(name, str) or not name.strip():
        raise ConceptError("concept needs a non-empty 'name'")
    slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in name.strip().lower())
    scenes = raw.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ConceptError("concept needs a non-empty 'scenes' list")
    fps = int(raw.get("fps", DEFAULT_FPS))
    if not 1 <= fps <= 120:
        raise ConceptError("fps must be 1..120")
    out_scenes = []
    seen = set()
    for i, s in enumerate(scenes):
        where = f"scenes[{i}]"
        if not isinstance(s, dict):
            raise ConceptError(f"{where} must be a mapping")
        sname = str(s.get("name") or f"scene_{i + 1}")
        if sname in seen:
            raise ConceptError(f"{where}: duplicate scene name {sname!r}")
        seen.add(sname)
        tpl = s.get("template")
        if tpl not in TEMPLATES:
            raise ConceptError(f"{where}: template must be one of {TEMPLATES}, got {tpl!r}")
        params = s.get("template_params") or {}
        if not isinstance(params, dict):
            raise ConceptError(f"{where}.template_params must be a mapping")
        if tpl == "linked_chain":
            n = int(params.get("num_items", 4))
            if not 1 <= n <= 50:
                raise ConceptError(f"{where}: num_items must be 1..50")
            params = {"num_items": n, "spacing": float(params.get("spacing", 3.0))}
        elif tpl == "checkmark_sequence":
            n = int(params.get("num_marks", 5))
            if not 1 <= n <= 50:
                raise ConceptError(f"{where}: num_marks must be 1..50")
            params = {"num_marks": n, "interval_frames": int(params.get("interval_frames", 30))}
        else:
            params = {"distance": float(params.get("distance", 6.0)),
                      "pulse_period_s": float(params.get("pulse_period_s", 1.0))}
        duration = float(s.get("duration_s", 5.0))
        if not 0.1 <= duration <= 600:
            raise ConceptError(f"{where}: duration_s must be 0.1..600")
        out_scenes.append({
            "name": sname, "template": tpl, "template_params": params,
            "duration_s": duration,
            "camera_angle": float(s.get("camera_angle", 90.0)),
            "narration": s.get("narration") or None,
        })
    render = raw.get("render_settings") or {}
    return {
        "name": name.strip(), "slug": slug, "fps": fps, "scenes": out_scenes,
        "render_settings": {
            "device": str(render.get("device", "AUTO")).upper(),
            "samples": int(render.get("samples", 256)),
            "preview_samples": int(render.get("preview_samples", 64)),
            "use_denoiser": bool(render.get("use_denoiser", True)),
            "adaptive_sampling": bool(render.get("adaptive_sampling", True)),
            "resolution": list(render.get("resolution", [1920, 1080])),
        },
    }


def load_concept(path: Path) -> dict[str, Any]:
    import yaml  # noqa: PLC0415

    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConceptError(f"cannot read {path}: {exc}") from exc
    return validate_concept(raw)


# ── Blender side ─────────────────────────────────────────────────────────────

def _material(name: str, rgba: tuple, emission: float = 0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = rgba
    bsdf.inputs["Roughness"].default_value = 0.4
    if emission:
        bsdf.inputs["Emission Color"].default_value = rgba
        bsdf.inputs["Emission Strength"].default_value = emission
    return mat


def _link(scene, obj):
    scene.collection.objects.link(obj)
    return obj


def _mesh_object(scene, name: str, kind: str, mat, *, location=(0, 0, 0), rotation=(0, 0, 0),
                 size: float = 1.0, radius: float = 1.0, radius2: float | None = None,
                 depth: float = 1.0):
    """A primitive built with bmesh and linked into *scene*.

    Not bpy.ops: in background mode there is no window, so operators add to
    whatever scene the context happens to hold — not the one being built.
    """
    bm = bmesh.new()
    if kind == "cube":
        bmesh.ops.create_cube(bm, size=size)
    elif kind == "sphere":
        bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=radius)
    elif kind in ("cylinder", "cone"):
        bmesh.ops.create_cone(bm, cap_ends=True, segments=24, radius1=radius,
                              radius2=radius if kind == "cylinder" else (radius2 or 0.0), depth=depth)
    else:
        raise ValueError(kind)
    for f in bm.faces:
        f.smooth = kind == "sphere"
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    obj = _link(scene, bpy.data.objects.new(name, me))
    obj.location = location
    obj.rotation_euler = rotation
    obj.data.materials.append(mat)
    return obj


class SceneTemplate:
    """Reusable 3D scene components (ADR-2033 §2). Each returns its objects."""

    @staticmethod
    def linked_chain(scene, num_items: int = 4, spacing: float = 3.0) -> list:
        cube_mat = _material("chain_cube", (0.78, 0.59, 0.29, 1.0))
        link_mat = _material("chain_link", (0.85, 0.87, 0.92, 1.0), emission=0.6)
        offset = (num_items - 1) * spacing / 2.0
        cubes = []
        for i in range(num_items):
            cube = _mesh_object(scene, f"event_cube_{i + 1}", "cube", cube_mat,
                                size=1.2, location=(i * spacing - offset, 0.0, 0.0))
            cube["corvin_role"] = "event_cube"
            cubes.append(cube)
        for i in range(num_items - 1):
            a, b = cubes[i].location, cubes[i + 1].location
            SceneTemplate._arrow(scene, a, b, link_mat, f"link_arrow_{i + 1}")
        return cubes

    @staticmethod
    def _arrow(scene, a, b, mat, name: str):
        a, b = Vector(a), Vector(b)
        direction = b - a
        length = direction.length - 1.4  # stop at the cube faces
        mid = a + direction * 0.5
        rot = direction.to_track_quat("Z", "Y").to_euler()
        shaft = _mesh_object(scene, f"{name}_shaft", "cylinder", mat,
                             radius=0.09, depth=max(0.1, length - 0.3), location=mid, rotation=rot)
        head = _mesh_object(scene, f"{name}_head", "cone", mat, radius=0.24, radius2=0.0, depth=0.35,
                            location=mid + direction.normalized() * (length / 2 - 0.15), rotation=rot)
        shaft["corvin_role"] = head["corvin_role"] = "link_arrow"
        return shaft, head

    @staticmethod
    def hash_transformation(scene, distance: float = 6.0, pulse_period_s: float = 1.0) -> list:
        io_mat = _material("hash_io", (0.24, 0.47, 0.78, 1.0))
        hash_mat = _material("hash_sphere", (0.95, 0.72, 0.25, 1.0), emission=2.0)
        inp = _mesh_object(scene, "hash_input", "cube", io_mat, size=1.0, location=(-distance / 2, 0, 0))
        out = _mesh_object(scene, "hash_output", "cube", io_mat, size=1.0, location=(distance / 2, 0, 0))
        sphere = _mesh_object(scene, "hash_sphere", "sphere", hash_mat, radius=0.8, location=(0, 0, 0))
        sphere["corvin_role"] = "hash_sphere"
        # Pulsate: scale 1.0 → 1.25 → 1.0 every period across the scene.
        period = max(2, int(round(pulse_period_s * scene.render.fps)))
        f = scene.frame_start
        while f <= scene.frame_end + period:
            for off, s in ((0, 1.0), (period // 2, 1.25)):
                sphere.scale = (s, s, s)
                sphere.keyframe_insert(data_path="scale", frame=f + off)
            f += period
        return [inp, sphere, out]

    @staticmethod
    def checkmark_sequence(scene, num_marks: int = 5, interval_frames: int = 30) -> list:
        mat = _material("checkmark", (0.25, 0.75, 0.4, 1.0), emission=0.5)
        marks = []
        top = (num_marks - 1) * 1.5 / 2
        for i in range(num_marks):
            curve = bpy.data.curves.new(f"checkmark_{i + 1}", type="CURVE")
            curve.dimensions = "3D"
            curve.bevel_depth = 0.08
            spline = curve.splines.new("POLY")
            spline.points.add(2)
            for p, (x, z) in zip(spline.points, ((-0.5, 0.1), (-0.15, -0.3), (0.55, 0.45))):
                p.co = (x, 0.0, z, 1.0)
            obj = _link(scene, bpy.data.objects.new(f"checkmark_{i + 1}", curve))
            obj.location = (0.0, 0.0, top - i * 1.5)
            obj.data.materials.append(mat)
            obj["corvin_role"] = "checkmark"
            # Staggered appearance: scale 0 until its frame, then 1.
            appear = scene.frame_start + i * interval_frames
            obj.scale = (0, 0, 0)
            obj.keyframe_insert(data_path="scale", frame=max(scene.frame_start, appear - 1))
            obj.scale = (1, 1, 1)
            obj.keyframe_insert(data_path="scale", frame=appear)
            marks.append(obj)
        return marks


def _scene_center(objs) -> "Vector":
    # location, not matrix_world: a freshly created object's world matrix is
    # only filled in by the next depsgraph update (template objects have no parent).
    pts = [Vector(o.location) for o in objs]
    return sum(pts, Vector((0, 0, 0))) / max(1, len(pts))


class CorvinOS3DSceneGenerator:
    """Concept → one Blender scene per concept scene (ADR-2033 §1, layer 2)."""

    def __init__(self, concept: dict[str, Any]):
        self.concept = concept

    def build(self) -> list:
        # Start from an empty file: no default cube/camera/light leaking in.
        bpy.ops.wm.read_factory_settings(use_empty=True)
        built = []
        for spec in self.concept["scenes"]:
            scene = bpy.data.scenes.new(spec["name"])
            scene.render.fps = self.concept["fps"]
            scene.frame_start = 1
            scene.frame_end = max(1, int(round(spec["duration_s"] * self.concept["fps"])))
            objs = getattr(SceneTemplate, spec["template"])(scene, **spec["template_params"])
            self._world(scene)
            self._light(scene)
            self._camera(scene, objs, spec["camera_angle"])
            scene["corvin_template"] = spec["template"]
            built.append(scene)
        # Remove the scene read_factory_settings left behind.
        for sc in list(bpy.data.scenes):
            if sc not in built and len(bpy.data.scenes) > 1:
                bpy.data.scenes.remove(sc)
        return built

    @staticmethod
    def _world(scene):
        world = bpy.data.worlds.new(f"{scene.name}_world")
        world.use_nodes = True
        world.node_tree.nodes["Background"].inputs[0].default_value = (0.02, 0.025, 0.04, 1.0)
        scene.world = world

    @staticmethod
    def _light(scene):
        data = bpy.data.lights.new(f"{scene.name}_sun", type="SUN")
        data.energy = 3.0
        sun = _link(scene, bpy.data.objects.new(f"{scene.name}_sun", data))
        sun.rotation_euler = (math.radians(50), 0, math.radians(30))

    @staticmethod
    def _camera(scene, objs, angle_deg: float):
        """Camera orbits the content by *angle_deg* over the scene (animate_camera_rotation)."""
        center = _scene_center(objs)
        extent = max((Vector(o.location) - center).length for o in objs) + 2.0
        pivot = _link(scene, bpy.data.objects.new(f"{scene.name}_camera_pivot", None))
        pivot.location = center
        cam_data = bpy.data.cameras.new(f"{scene.name}_camera")
        cam_data.lens = 35
        cam = _link(scene, bpy.data.objects.new(f"{scene.name}_camera", cam_data))
        cam.parent = pivot
        # Far enough that the content's full extent (any axis) stays in frame
        # for every angle of the orbit, with a margin.
        cam.location = (0.0, -extent * 2.9, extent * 0.6)
        track = cam.constraints.new("TRACK_TO")
        track.target = pivot
        track.track_axis = "TRACK_NEGATIVE_Z"
        track.up_axis = "UP_Y"
        pivot.rotation_euler = (0, 0, 0)
        pivot.keyframe_insert(data_path="rotation_euler", frame=scene.frame_start)
        pivot.rotation_euler = (0, 0, math.radians(angle_deg))
        pivot.keyframe_insert(data_path="rotation_euler", frame=scene.frame_end)
        scene.camera = cam
        scene["corvin_camera_angle"] = angle_deg


class RenderOrchestrator:
    """Cycles configuration + rendering (ADR-2033 §3/§4). GPU if present, else CPU."""

    @staticmethod
    def detect_device(requested: str = "AUTO") -> dict[str, Any]:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        if requested != "CPU":
            order = [requested] if requested not in ("AUTO", "GPU") else ["OPTIX", "CUDA", "HIP", "ONEAPI", "METAL"]
            for backend in order:
                try:
                    prefs.compute_device_type = backend
                    prefs.get_devices()
                except (TypeError, ValueError):
                    continue
                gpus = [d for d in prefs.devices if d.type == backend]
                if gpus:
                    for d in prefs.devices:
                        d.use = d.type == backend
                    return {"device": "GPU", "backend": backend, "devices": [d.name for d in gpus]}
        return {"device": "CPU", "backend": None, "devices": [],
                "fallback": requested not in ("CPU",)}

    @staticmethod
    def configure(scene, settings: dict[str, Any], device: dict[str, Any], *, preview: bool,
                  frames_dir: Path, resolution_pct: int, samples: int | None) -> None:
        scene.render.engine = "CYCLES"
        scene.cycles.device = device["device"]
        scene.cycles.samples = samples or (settings["preview_samples"] if preview else settings["samples"])
        scene.cycles.use_adaptive_sampling = settings["adaptive_sampling"]
        # Distro Blender builds may ship WITHOUT any denoiser (Ubuntu's 4.0.2
        # has none: the enum is empty). Use what exists; record what was used.
        available = [i.identifier for i in scene.cycles.bl_rna.properties["denoiser"].enum_items]
        wanted = "OPTIX" if device.get("backend") == "OPTIX" else "OPENIMAGEDENOISE"
        denoiser = wanted if wanted in available else (available[0] if available else None)
        scene.cycles.use_denoising = bool(settings["use_denoiser"] and denoiser)
        if scene.cycles.use_denoising:
            scene.cycles.denoiser = denoiser
        device["denoiser"] = denoiser if scene.cycles.use_denoising else None
        if settings["use_denoiser"] and not denoiser:
            device["denoiser_note"] = "requested, but this Blender build has no denoiser"
        w, h = settings["resolution"]
        scene.render.resolution_x, scene.render.resolution_y = int(w), int(h)
        scene.render.resolution_percentage = resolution_pct
        scene.render.image_settings.file_format = "OPEN_EXR"
        scene.render.image_settings.color_depth = "16"
        scene.render.image_settings.exr_codec = "ZIP"
        scene.render.filepath = str(frames_dir / "frame_")
        scene.render.use_file_extension = True

    @staticmethod
    def render(scene, frames: tuple[int, int] | None) -> list[Path]:
        if frames:
            scene.frame_start, scene.frame_end = frames
        bpy.ops.render.render(animation=True, scene=scene.name)
        base = bpy.path.abspath(scene.render.filepath)
        return [Path(f"{base}{f:04d}.exr") for f in range(scene.frame_start, scene.frame_end + 1)]


class QualityValidator:
    """Pre-render and post-render checks (ADR-2033 §5). Return problem lists."""

    @staticmethod
    def pre_render(scene) -> list[str]:
        scene.view_layers[0].update()  # world matrices of freshly built objects
        problems = []
        meshes = [o for o in scene.objects if o.type in ("MESH", "CURVE")]
        if not meshes:
            problems.append("scene has no renderable objects")
        for o in meshes:
            if not o.data.materials or not any(o.data.materials):
                problems.append(f"{o.name} has no material")
        cam = scene.camera
        if cam is None:
            problems.append("scene has no camera")
        elif cam.data.lens <= 0:
            problems.append("camera focal length must be > 0")
        if not any(o.type == "LIGHT" for o in scene.objects) and scene.world is None:
            problems.append("scene has no light and no world")
        if meshes:
            ext = max(o.matrix_world.translation.length for o in meshes)
            if ext > 1000:
                problems.append(f"scene bounds unreasonable ({ext:.0f} m)")
        if scene.frame_end < scene.frame_start:
            problems.append("empty frame range")
        return problems

    @staticmethod
    def post_render(frames: list[Path], expected_size: tuple[int, int]) -> list[str]:
        problems = []
        for f in frames:
            if not f.is_file():
                problems.append(f"missing frame {f.name}")
                continue
            with f.open("rb") as fh:
                if fh.read(4) != EXR_MAGIC:
                    problems.append(f"{f.name} is not an OpenEXR file")
                    continue
            img = bpy.data.images.load(str(f), check_existing=False)
            try:
                if tuple(img.size) != tuple(expected_size):
                    problems.append(f"{f.name} is {tuple(img.size)}, expected {expected_size}")
                px = img.pixels[:]
                if not px or max(px) <= 0.0:
                    problems.append(f"{f.name} has no pixel data (black/empty)")
            finally:
                bpy.data.images.remove(img)
        return problems


def _inner(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="blender_3d_scene_generator (inner)")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--frames", default="")
    ap.add_argument("--resolution", type=int, default=100)
    ap.add_argument("--samples", type=int, default=0)
    ap.add_argument("--scene", default="", help="render only this scene (all are still built)")
    a = ap.parse_args(argv)
    concept = json.loads(Path(a.spec).read_text())
    out = Path(a.out)
    report: dict[str, Any] = {"concept": concept["name"], "blender": bpy.app.version_string,
                              "ok": False, "scenes": []}
    t0 = time.time()
    try:
        scenes = CorvinOS3DSceneGenerator(concept).build()
        device = RenderOrchestrator.detect_device(concept["render_settings"]["device"])
        report["device"] = device
        frames = tuple(int(x) for x in a.frames.split("-")) if a.frames else None
        all_ok = True
        for scene in scenes:
            frames_dir = out / "frames" / scene.name
            RenderOrchestrator.configure(scene, concept["render_settings"], device, preview=a.preview,
                                         frames_dir=frames_dir, resolution_pct=a.resolution,
                                         samples=a.samples or None)
            roles: dict[str, int] = {}
            for o in scene.objects:
                role = o.get("corvin_role")
                if role:
                    roles[role] = roles.get(role, 0) + 1
            pivot = next((o for o in scene.objects if o.name.endswith("_camera_pivot")), None)
            entry: dict[str, Any] = {
                "name": scene.name, "template": scene.get("corvin_template"),
                "frame_start": scene.frame_start, "frame_end": scene.frame_end, "fps": scene.render.fps,
                "objects": roles,
                "camera_rotation_deg": round(math.degrees(
                    pivot.animation_data.action.fcurves.find("rotation_euler", index=2).evaluate(scene.frame_end)), 3)
                if pivot and pivot.animation_data else None,
                "pre_render_problems": QualityValidator.pre_render(scene),
            }
            if entry["pre_render_problems"]:
                all_ok = False
            elif a.render and (not a.scene or a.scene == scene.name):
                t = time.time()
                paths = RenderOrchestrator.render(scene, frames)
                size = (scene.render.resolution_x * scene.render.resolution_percentage // 100,
                        scene.render.resolution_y * scene.render.resolution_percentage // 100)
                entry["frames"] = [str(p) for p in paths]
                entry["render_s"] = round(time.time() - t, 2)
                entry["post_render_problems"] = QualityValidator.post_render(paths, size)
                all_ok = all_ok and not entry["post_render_problems"]
            report["scenes"].append(entry)
        blend = out / f"{concept['slug']}.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(blend))
        report["blend_file"] = str(blend)
        report["ok"] = all_ok
    except Exception as exc:  # noqa: BLE001 — reported, never swallowed
        import traceback  # noqa: PLC0415

        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()[-4000:]
    report["duration_s"] = round(time.time() - t0, 2)
    Path(a.report).write_text(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


# ── Host side ────────────────────────────────────────────────────────────────

def default_out_dir(slug: str) -> Path:
    home = Path(os.environ.get("CORVIN_HOME", Path.home() / ".corvin"))
    tid = os.environ.get("CORVIN_TENANT_ID", "_default")
    return home / "tenants" / tid / "video_library" / slug


def build(concept_path: Path, *, out: Path | None = None, render: bool = False,
          preview: bool = False, frames: str = "", resolution: int = 100,
          samples: int = 0, blender: str | None = None, timeout_s: int = 6 * 3600,
          scene: str = "", threads: int = 0, nice: int = 0) -> dict[str, Any]:
    concept = load_concept(concept_path)
    if scene and scene not in {s["name"] for s in concept["scenes"]}:
        raise ConceptError(f"no scene named {scene!r} in the concept")
    blender = blender or shutil.which("blender")
    if not blender:
        raise RuntimeError("blender not found on PATH")
    out = Path(out) if out else default_out_dir(concept["slug"])
    out.mkdir(parents=True, exist_ok=True)
    spec = out / "concept.json"
    spec.write_text(json.dumps(concept, indent=2))
    report_path = out / "report.json"
    cmd = [blender, "--background", "--factory-startup"]
    if threads:
        cmd += ["--threads", str(threads)]  # leave cores for the console
    cmd += ["--python", str(Path(__file__).resolve()),
           "--", "inner", "--spec", str(spec), "--out", str(out), "--report", str(report_path),
           "--resolution", str(resolution)]
    if render:
        cmd.append("--render")
    if preview:
        cmd.append("--preview")
    if frames:
        cmd += ["--frames", frames]
    if samples:
        cmd += ["--samples", str(samples)]
    if scene:
        cmd += ["--scene", scene]
    if nice:
        cmd = ["nice", "-n", str(nice), *cmd]
    attempts = MAX_RENDER_ATTEMPTS if render else 1
    report: dict[str, Any] = {}
    for attempt in range(1, attempts + 1):
        report_path.unlink(missing_ok=True)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        (out / f"blender_attempt_{attempt}.log").write_text(proc.stdout[-200_000:] + proc.stderr[-50_000:])
        report = json.loads(report_path.read_text()) if report_path.is_file() else {
            "ok": False, "error": f"blender exited {proc.returncode} without a report"}
        report["attempts"] = attempt
        if report.get("ok"):
            break
        # A YAML/scene problem will not fix itself — only retry crashes (§6).
        if any(s.get("pre_render_problems") for s in report.get("scenes", [])):
            break
    return report


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if bpy is not None:  # Blender side: args after "--"
        inner = argv[argv.index("--") + 1:] if "--" in argv else sys.argv[sys.argv.index("--") + 1:]
        if inner[:1] == ["inner"]:
            return _inner(inner[1:])
    ap = argparse.ArgumentParser(description="Build (and optionally render) a CorvinOS 3D learning concept.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("concept", type=Path)
    b.add_argument("--out", type=Path)
    b.add_argument("--render", action="store_true")
    b.add_argument("--preview", action="store_true", help="preview samples (ADR-2033 §4.3)")
    b.add_argument("--frames", default="", help="e.g. 1-2 (default: every frame)")
    b.add_argument("--resolution", type=int, default=100, help="resolution percentage")
    b.add_argument("--samples", type=int, default=0)
    b.add_argument("--scene", default="", help="render only this scene")
    b.add_argument("--threads", type=int, default=0, help="Blender render threads (0 = all cores)")
    v = sub.add_parser("validate")
    v.add_argument("concept", type=Path)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "validate":
            print(json.dumps(load_concept(a.concept), indent=2))
            return 0
        report = build(a.concept, out=a.out, render=a.render, preview=a.preview,
                       frames=a.frames, resolution=a.resolution, samples=a.samples,
                       scene=a.scene, threads=a.threads)
    except ConceptError as exc:
        print(f"concept error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    code = main()
    if bpy is not None:
        # Inside Blender, --python does not stop Blender from continuing; exit explicitly.
        sys.exit(code)
    raise SystemExit(code)
