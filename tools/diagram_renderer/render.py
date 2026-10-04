#!/usr/bin/env python3
"""CLI over the canonical diagram renderer (core/skills/video_producer/workers/
diagram/) for fast manual iteration on a spec outside the Maestro pipeline:

    .venv/bin/python3 tools/diagram_renderer/render.py build spec.yaml -o out_dir/

Writes out_dir/frame_01.png .. frame_NN.png (one per step). Same validation,
browser hardening (JavaScript off, network aborted) and content gate as
DiagramRendererWorker — both call diagram.raster.render_compiled.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root, for core.* imports
from core.skills.video_producer.workers.diagram.compiler import SpecError, compile_spec, normalize_spec  # noqa: E402
from core.skills.video_producer.workers.diagram.raster import DiagramRenderError, render_compiled  # noqa: E402


def render_spec(spec_path: Path, out_dir: Path) -> list:
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    canvas = normalize_spec(spec)["canvas"]
    compiled = compile_spec(spec)
    out_dir.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            return render_compiled(browser, compiled, canvas, out_dir)
        finally:
            browser.close()


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render a diagram spec (YAML) to PNG frame(s).")
    ap.add_argument("cmd", choices=["build"])
    ap.add_argument("spec", type=Path)
    ap.add_argument("-o", "--out", type=Path, required=True, dest="out_dir")
    args = ap.parse_args(argv)
    try:
        written = render_spec(args.spec, args.out_dir)
    except (SpecError, DiagramRenderError, yaml.YAMLError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    for p in written:
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
