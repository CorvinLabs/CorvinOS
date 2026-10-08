"""Bundle lab for the Forge-system Playwright suite (stdlib only).

The browser side has no ZIP library, so the specs call this script to look
inside a downloaded bundle and to forge hostile ones. It deliberately does NOT
import ``core.forge_bundle``: a test that builds its attack input with the
code under test only proves the code agrees with itself.

    bundle_lab.py inspect BUNDLE.zip
        -> JSON: entries, envelope, and an independent integrity re-check
    bundle_lab.py build RECIPE.json OUT.zip
        -> writes a bundle from a recipe (see ``build``); the recipe's
           ``tamper`` list breaks it in one named way after hashing
    bundle_lab.py graft BASE.zip RECIPE.json OUT.zip
        -> copies BASE's artifacts (payload bytes untouched) into a new
           bundle, then applies the recipe's envelope overrides / tamper
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import stat
import sys
import zipfile

ENVELOPE = "forge-bundle.json"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def inspect(path: str) -> dict:
    with zipfile.ZipFile(path) as zf:
        entries = [{"name": i.filename, "size": i.file_size, "compress_size": i.compress_size}
                   for i in zf.infolist()]
        env = json.loads(zf.read(ENVELOPE))
        problems = []
        declared = set()
        for art in env.get("artifacts", []):
            for f in art.get("files", []):
                declared.add(f["path"])
                data = zf.read(f["path"])
                if _sha(data) != f["sha256"]:
                    problems.append(f"sha256 mismatch: {f['path']}")
                if len(data) != f["size"]:
                    problems.append(f"size mismatch: {f['path']}")
        undeclared = sorted({e["name"] for e in entries} - declared - {ENVELOPE})
        payloads = {}
        for name in declared:
            data = zf.read(name)
            if name.endswith(".json") or name.endswith(".py") or name.endswith(".sh"):
                payloads[name] = data.decode("utf-8", "replace")
            elif name.endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(data)) as inner:
                    payloads[name] = {"zip_entries": sorted(inner.namelist())}
        return {"entries": entries, "envelope": env, "problems": problems,
                "undeclared": undeclared, "payloads": payloads}


def _content(spec) -> bytes:
    """A file body in a recipe: a str, {"b64": ...}, {"repeat": ch, "n": N},
    {"zip": {name: body}} (a nested archive) or {"gzip": body}."""
    if isinstance(spec, str):
        return spec.encode("utf-8")
    if "b64" in spec:
        return base64.b64decode(spec["b64"])
    if "repeat" in spec:
        return (spec["repeat"] * int(spec["n"])).encode("latin-1")
    if "zip" in spec:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, body in spec["zip"].items():
                zf.writestr(name, _content(body))
        return buf.getvalue()
    if "gzip" in spec:
        return gzip.compress(_content(spec["gzip"]))
    if "json" in spec:
        return json.dumps(spec["json"]).encode("utf-8")
    raise ValueError(f"unknown content spec: {spec!r}")


def _write(out: str, envelope: dict, files: dict[str, bytes], tamper: list) -> None:
    """Hash every declared file, then apply the named tamper steps."""
    for art in envelope["artifacts"]:
        if "files" not in art or art["files"] is None:
            art["files"] = []
        if art.get("_auto_files", True):
            prefix = f"artifacts/{art['kind']}/{art['id']}@{art['version']}/"
            art["files"] = [{"path": p, "sha256": _sha(b), "size": len(b)}
                            for p, b in files.items() if p.startswith(prefix)]
        art.pop("_auto_files", None)
    extra: dict[str, bytes] = {}
    symlinks: set[str] = set()
    for t in tamper:
        op = t["op"]
        if op == "flip_byte":             # payload changes after hashing
            b = bytearray(files[t["path"]]); b[0] ^= 0xFF; files[t["path"]] = bytes(b)
        elif op == "drop_file":
            files.pop(t["path"])
        elif op == "extra_file":          # present but not declared
            extra[t["path"]] = _content(t.get("body", "x"))
        elif op == "raw_entry":           # declared or not, written with this exact name
            extra[t["path"]] = _content(t.get("body", "x"))
        elif op == "symlink":
            symlinks.add(t["path"])
        elif op == "envelope_patch":
            envelope.update(t["fields"])
        elif op == "drop_envelope":
            envelope = None  # type: ignore[assignment]
        else:
            raise ValueError(f"unknown tamper op {op!r}")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        if envelope is not None:
            zf.writestr(ENVELOPE, json.dumps(envelope, indent=2))
        for name, body in {**files, **extra}.items():
            if name in symlinks:
                info = zipfile.ZipInfo(name)
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                zf.writestr(info, body)
            else:
                zf.writestr(name, body)


def build(recipe_path: str, out: str) -> dict:
    r = json.load(open(recipe_path))
    envelope = {"format": "corvin.forge-bundle", "format_version": 1,
                "id": r["id"], "version": r["version"],
                "created_at": "2026-10-08T00:00:00Z", "artifacts": []}
    if "description" in r:
        envelope["description"] = r["description"]
    files: dict[str, bytes] = {}
    for a in r["artifacts"]:
        art = {"kind": a["kind"], "id": a["id"], "version": a["version"]}
        if a.get("requires"):
            art["requires"] = a["requires"]
        prefix = f"artifacts/{a['kind']}/{a['id']}@{a['version']}/"
        for rel, body in a.get("files", {}).items():
            files[prefix + rel] = _content(body)
        envelope["artifacts"].append(art)
    _write(out, envelope, files, r.get("tamper", []))
    return {"written": out}


def graft(base: str, recipe_path: str, out: str) -> dict:
    r = json.load(open(recipe_path))
    with zipfile.ZipFile(base) as zf:
        envelope = json.loads(zf.read(ENVELOPE))
        files = {i.filename: zf.read(i.filename) for i in zf.infolist() if i.filename != ENVELOPE}
    envelope.update(r.get("envelope", {}))
    for art in envelope["artifacts"]:
        art["_auto_files"] = False
    _write(out, envelope, files, r.get("tamper", []))
    return {"written": out}


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    fn = {"inspect": inspect, "build": build, "graft": graft}[cmd]
    print(json.dumps(fn(*args)))
