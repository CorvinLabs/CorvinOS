#!/usr/bin/env python3
"""Capture a REAL `claude` stream-json run and write a scrubbed, timed fixture.

Used to (re)generate ``fixtures/bgscope/*.jsonl`` — the contract between the
background-scope tracker and the claude CLI (ADR-2236, PLAN-0938 P0). Re-run it
after a CLI update and diff the result; the schema is not versioned by the CLI.

    capture_bgscope_fixture.py <name> "<prompt>" [--timeout 120] [--out DIR]

The run mirrors what the bridge does: prompt on stdin as a stream-json user
message, stdin CLOSED right after the first ``result`` event (the adapter's
``close_stdin()``), then read to EOF. Every kept line gets ``_t`` = seconds
since spawn so the fake CLI can replay realistic timing.

Scrubbing keeps only what the tracker and the adapter read: ids are replaced by
stable placeholders, filesystem paths and tool/skill catalogues are dropped.
Prompts in fixtures are synthetic; never capture a real user prompt.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

_DROP_SYSTEM = {"hook_started", "hook_response", "thinking_tokens"}
_DROP_TYPES = {"rate_limit_event"}
_PATH_RE = re.compile(r"/tmp/claude-\d+/[^\s\"']+|/tmp/bgprobe[^\s\"']*")


def _scrub_str(s: str) -> str:
    return _PATH_RE.sub("/tmp/claude/tasks/OUT.output", s)


def _scrub_obj(o):
    if isinstance(o, str):
        return _scrub_str(o)
    if isinstance(o, list):
        return [_scrub_obj(x) for x in o]
    if isinstance(o, dict):
        return {k: _scrub_obj(v) for k, v in o.items()}
    return o


def scrub(events: list[dict]) -> list[dict]:
    out: list[dict] = []
    n_uuid = 0
    for e in events:
        t, st = e.get("type"), e.get("subtype")
        if t in _DROP_TYPES or (t == "system" and st in _DROP_SYSTEM):
            continue
        keep: dict
        if t == "system" and st == "init":
            keep = {"type": "system", "subtype": "init", "model": e.get("model", "")}
        elif t == "assistant":
            msg = e.get("message") or {}
            keep = {"type": "assistant",
                    "message": {"role": "assistant", "content": msg.get("content") or []}}
        elif t == "user":
            msg = e.get("message") or {}
            content = msg.get("content")
            if isinstance(content, list):
                content = [
                    {**b, "content": _scrub_str(str(b.get("content", "")))[:300]}
                    if isinstance(b, dict) and b.get("type") == "tool_result" else b
                    for b in content
                ]
            keep = {"type": "user", "message": {"role": "user", "content": content}}
        elif t == "result":
            u = e.get("usage") or {}
            keep = {k: e[k] for k in ("type", "subtype", "is_error", "result",
                                      "terminal_reason", "stop_reason", "num_turns",
                                      "origin") if k in e}
            keep["usage"] = {
                "input_tokens": u.get("input_tokens", 0),
                "output_tokens": u.get("output_tokens", 0),
                "cache_read_input_tokens": u.get("cache_read_input_tokens", 0),
                "cache_creation_input_tokens": u.get("cache_creation_input_tokens", 0),
            }
        elif t == "system":
            keep = {k: v for k, v in e.items() if k not in ("uuid", "session_id")}
        else:
            keep = {k: v for k, v in e.items() if k not in ("uuid", "session_id")}
        keep = _scrub_obj(keep)
        n_uuid += 1
        keep["uuid"] = f"u{n_uuid:03d}"
        keep["session_id"] = "S"
        keep["_t"] = e["_t"]
        out.append(keep)
    return out


def capture(prompt: str, timeout: int) -> list[dict]:
    with __import__("tempfile").TemporaryDirectory(prefix="bgcap-") as cwd:
        p = subprocess.Popen(
            ["claude", "-p", "--input-format", "stream-json",
             "--output-format", "stream-json", "--verbose", "--model", "haiku",
             "--permission-mode", "bypassPermissions", "--max-turns", "6"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, cwd=cwd)
        threading.Timer(timeout, p.kill).start()
        p.stdin.write(json.dumps({"type": "user",
                                  "message": {"role": "user", "content": prompt}}) + "\n")
        p.stdin.flush()
        t0 = time.time()
        events: list[dict] = []
        closed = False
        for line in p.stdout:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            e["_t"] = round(time.time() - t0, 2)
            events.append(e)
            if e.get("type") == "result" and not closed:
                p.stdin.close()
                closed = True
        p.wait()
        events.append({"type": "_eof", "_t": round(time.time() - t0, 2),
                       "rc": p.returncode})
    return events


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("prompt")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--out", default=str(Path(__file__).parent / "fixtures" / "bgscope"))
    a = ap.parse_args()
    ev = scrub(capture(a.prompt, a.timeout))
    d = Path(a.out)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{a.name}.jsonl"
    path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in ev),
                    encoding="utf-8")
    kinds = [f"{e['type']}/{e.get('subtype', '')}" for e in ev]
    print(f"{path}: {len(ev)} events; results={kinds.count('result/success')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
