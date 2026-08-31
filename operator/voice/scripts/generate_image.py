#!/usr/bin/env python3
"""generate_image.py — wrap OpenAI image generation for the bridges.

Bridge personas can shell out to this script when the user asks for an
image. The result lands in the persona's `outputs/` folder, which the
adapter automatically attaches to the reply on whichever messenger.

Usage:
    python3 generate_image.py "a cinematic photo of a fox in a forest" \
        --out ~/cowork/assistant/outputs/fox.png \
        [--size 1024x1024] [--quality standard|hd] [--model dall-e-3|gpt-image-1]

Reads OPENAI_API_KEY from the environment (the bridge already has it
loaded via service.env).
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="Image prompt (English works best for DALL-E).")
    parser.add_argument("--out", required=True, help="Output PNG path.")
    parser.add_argument("--size", default="1024x1024",
                        help="Image size: 1024x1024, 1024x1792, 1792x1024.")
    parser.add_argument("--quality", default="standard", choices=("standard", "hd"))
    parser.add_argument("--model", default="dall-e-3",
                        help="OpenAI image model. Defaults to dall-e-3.")
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set.", file=sys.stderr)
        return 2

    try:
        from openai import OpenAI
    except ImportError:
        print("ERROR: openai python package missing. pip install openai", file=sys.stderr)
        return 2

    client = OpenAI(api_key=api_key)
    out_path = Path(os.path.expanduser(args.out)).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    try:
        resp = client.images.generate(
            model=args.model,
            prompt=args.prompt,
            size=args.size,
            quality=args.quality,
            n=1,
            response_format="b64_json",
        )
    except Exception as e:
        print(f"ERROR: image generation failed: {e}", file=sys.stderr)
        return 1

    b64 = resp.data[0].b64_json
    if not b64:
        print("ERROR: empty image response.", file=sys.stderr)
        return 1
    out_path.write_bytes(base64.b64decode(b64))
    dt = time.time() - t0
    print(f"OK: wrote {out_path} ({out_path.stat().st_size} bytes, {dt:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
