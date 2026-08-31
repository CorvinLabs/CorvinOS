"""MCP Plugin Manager — installer (ADR-0096 M1/M2/M4).

Supported sources:
  npm:package[@version]        — records npx runtime (M1)
  local:./path                 — reads mcp-tool.yaml or auto-detects (M1)
  github:owner/repo[@tag|@sha] — downloads tarball, pins SHA256 (M2)
  pip:package[@version]        — records uvx runtime (M2)
  docker:image[:tag]           — pulls image, pins digest (M4)
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tarfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import catalog as _cat

# Scoped npm packages: @scope/name[@version]   Unscoped: name[@version]
_NPM_RE = re.compile(r"^(@[^@\s/]+/[^@\s]+|[^@\s/]+)(?:@(.+))?$")
_GITHUB_RE = re.compile(
    r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:@(.+))?$"
)
_GITHUB_API = "https://api.github.com/repos/{owner}/{repo}/tarball/{ref}"
# Block branch-only installs without explicit opt-in (supply-chain protection).
_LOOKS_LIKE_VERSION = re.compile(r"^v?\d|^[0-9a-f]{7,40}$")


def _safe_id(source: str) -> str:
    pkg = source.split(":", 1)[-1]
    # Scoped npm packages: @scope/name[@version] → scope-name
    m = re.match(r"@([^/]+/[^@]+)(?:@.*)?$", pkg)
    if m:
        pkg = m.group(1)
    else:
        pkg = pkg.split("@")[0]
    pkg = pkg.replace("/", "-").lower()
    pkg = re.sub(r"[^a-z0-9._-]", "-", pkg)
    return pkg[:64]


def parse_npm_source(spec: str) -> tuple[str, str]:
    """Return (package, version) from an npm spec. version may be empty."""
    m = _NPM_RE.match(spec)
    if not m:
        raise ValueError(f"Invalid npm spec: {spec!r}")
    return m.group(1), (m.group(2) or "")


def install(
    source_str: str,
    tid: str = "_default",
    *,
    allow_unpin: bool = False,
) -> dict[str, Any]:
    """Install a tool from *source_str* and add it to the catalog.

    Returns the catalog entry. Raises ValueError on bad source or missing pin.
    Pass allow_unpin=True to permit branch-head GitHub installs (not recommended).
    """
    if source_str.startswith("npm:"):
        return _install_npm(source_str[4:], tid)
    if source_str.startswith("local:"):
        return _install_local(source_str[6:], tid)
    if source_str.startswith("github:"):
        return _install_github(source_str[7:], tid, allow_unpin=allow_unpin)
    if source_str.startswith("pip:"):
        return _install_pip(source_str[4:], tid)
    if source_str.startswith("docker:"):
        return _install_docker(source_str[7:], tid)
    raise ValueError(
        f"Unsupported source type: {source_str!r}. "
        "Supported: npm:<pkg>[@ver], local:<path>, "
        "github:<owner>/<repo>[@tag|@sha], pip:<pkg>[@ver], docker:<image>[:<tag>]"
    )


def uninstall(tool_id: str, tid: str = "_default") -> bool:
    """Remove tool from catalog + any on-disk artifacts. Returns True if found."""
    entry = _cat.get_tool(tid, tool_id)
    if entry is None:
        return False
    # Clean up downloaded GitHub artifacts
    installs_dir = _cat.catalog_dir(tid) / "installs"
    for path in (
        installs_dir / tool_id,
        installs_dir / f"{tool_id}.tar.gz",
    ):
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
    return _cat.remove_tool(tid, tool_id)


# ── npm ──────────────────────────────────────────────────────────────────────


def _install_npm(spec: str, tid: str) -> dict[str, Any]:
    package, version = parse_npm_source(spec)
    tool_id = _safe_id(f"npm:{package}")
    versioned = f"{package}@{version}" if version else package
    entry: dict[str, Any] = {
        "id": tool_id,
        "source": f"npm:{versioned}",
        "installed_at": datetime.now(tz=timezone.utc).isoformat(),
        "runtime": {"command": "npx", "args": ["-y", versioned]},
        "secrets": [],
        "compliance": {"locality": "unknown", "network_egress": "unknown"},
    }
    _cat.add_tool(tid, entry)
    return entry


# ── pip (M2) ─────────────────────────────────────────────────────────────────


def _install_pip(spec: str, tid: str) -> dict[str, Any]:
    """Record a pip/uvx runtime. Uses `uvx` for isolated execution."""
    m = _NPM_RE.match(spec)
    if not m:
        raise ValueError(f"Invalid pip spec: {spec!r}")
    package = m.group(1)
    version = m.group(2) or ""
    tool_id = _safe_id(f"pip:{package}")
    versioned = f"{package}=={version}" if version else package
    # uvx creates an isolated venv and runs the package in one step —
    # same pattern as npx for npm. Falls back to `python3 -m` if uvx is absent.
    cmd, args = ("uvx", [versioned]) if shutil.which("uvx") else (
        "python3", ["-m", package.replace("-", "_")]
    )
    entry: dict[str, Any] = {
        "id": tool_id,
        "source": f"pip:{versioned}",
        "installed_at": datetime.now(tz=timezone.utc).isoformat(),
        "runtime": {"command": cmd, "args": args},
        "secrets": [],
        "compliance": {"locality": "local", "network_egress": "none"},
    }
    _cat.add_tool(tid, entry)
    return entry


# ── github (M2) ───────────────────────────────────────────────────────────────


def _install_github(
    spec: str,
    tid: str,
    *,
    allow_unpin: bool = False,
) -> dict[str, Any]:
    """Download GitHub tarball, pin SHA256, extract, detect runtime."""
    m = _GITHUB_RE.match(spec)
    if not m:
        raise ValueError(
            f"Invalid github spec: {spec!r}. "
            "Use github:<owner>/<repo>[@tag|@sha]"
        )
    owner, repo, ref = m.group(1), m.group(2), m.group(3) or ""

    if not ref:
        raise ValueError(
            f"GitHub install requires a tag or SHA: github:{owner}/{repo}@<tag>. "
            "Branch-head installs are blocked (supply-chain protection). "
            "Use --allow-unpin to override."
        )
    if not allow_unpin and not _LOOKS_LIKE_VERSION.match(ref):
        raise ValueError(
            f"Ref {ref!r} does not look like a version tag or commit SHA. "
            "Use --allow-unpin to install from a branch (not recommended)."
        )

    tool_id = _safe_id(f"github:{owner}/{repo}")
    installs_dir = _cat.catalog_dir(tid) / "installs"
    installs_dir.mkdir(parents=True, exist_ok=True)

    tarball_path = installs_dir / f"{tool_id}.tar.gz"
    extract_dir = installs_dir / tool_id

    url = _GITHUB_API.format(owner=owner, repo=repo, ref=ref or "HEAD")
    sha256 = _download_and_verify(url, tarball_path)

    # Extract (clean any previous extract first)
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir()
    top_dir_name = _extract_tarball(tarball_path, extract_dir)
    tool_root = extract_dir / top_dir_name if top_dir_name else extract_dir

    # Detect runtime from extracted content
    runtime = _detect_runtime(tool_root, tool_root)

    entry: dict[str, Any] = {
        "id": tool_id,
        "source": f"github:{owner}/{repo}@{ref}",
        "installed_at": datetime.now(tz=timezone.utc).isoformat(),
        "sha256": sha256,
        "install_path": str(tool_root),
        "runtime": runtime,
        "secrets": [],
        "compliance": {"locality": "local", "network_egress": "none"},
    }
    _cat.add_tool(tid, entry)
    return entry


def _download_and_verify(url: str, dest: Path) -> str:
    """Download *url* to *dest* and return its SHA256 hex digest."""
    sha = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": "corvin-mcp/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        with open(dest, "wb") as fh:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                fh.write(chunk)
                sha.update(chunk)
    return sha.hexdigest()


def _extract_tarball(tarball: Path, dest: Path) -> str:
    """Extract *tarball* to *dest*. Returns the top-level directory name (may be empty)."""
    with tarfile.open(tarball, "r:gz") as tf:
        for member in tf.getmembers():
            # Reject absolute paths and directory traversal in the member name
            if member.name.startswith("/") or ".." in member.name.split("/"):
                raise ValueError(f"Unsafe path in tarball: {member.name!r}")
            # Reject symlinks/hardlinks whose target escapes the extraction root.
            # member.linkname is the symlink or hardlink target; a relative
            # target like "../../etc/passwd" would resolve outside dest.
            if (member.issym() or member.islnk()) and member.linkname:
                link = member.linkname
                if link.startswith("/") or ".." in link.replace("\\", "/").split("/"):
                    raise ValueError(
                        f"Unsafe symlink/hardlink target in tarball: {link!r}"
                        f" (member: {member.name!r})"
                    )
        tf.extractall(dest)
        names = tf.getnames()
    if names:
        return names[0].split("/")[0]
    return ""


def _detect_runtime(tool_root: Path, base_path: Path) -> dict[str, Any]:
    """Detect runtime from extracted directory content."""
    manifest = tool_root / "mcp-tool.yaml"
    if manifest.is_file():
        try:
            import yaml  # type: ignore[import-not-found]
            with open(manifest, encoding="utf-8") as fh:
                raw = yaml.safe_load(fh)
            if isinstance(raw, dict):
                rt = raw.get("runtime", {})
                if rt.get("command"):
                    return dict(rt)
        except Exception:
            pass

    if (tool_root / "package.json").is_file():
        entry_point = tool_root / "index.js"
        if not entry_point.is_file():
            # Try src/index.js or dist/index.js
            for candidate in (tool_root / "src" / "index.js",
                               tool_root / "dist" / "index.js"):
                if candidate.is_file():
                    entry_point = candidate
                    break
        return {"command": "node", "args": [str(entry_point)]}

    if (tool_root / "pyproject.toml").is_file() or (tool_root / "setup.py").is_file():
        # Try to find a main.py or __main__.py
        for candidate in ("main.py", "__main__.py", "server.py"):
            if (tool_root / candidate).is_file():
                return {"command": "python3", "args": [str(tool_root / candidate)]}
        # Fall back to module execution
        return {"command": "python3", "args": ["-m", tool_root.name.replace("-", "_")]}

    raise ValueError(
        f"Cannot auto-detect runtime in {tool_root}. "
        "Add an mcp-tool.yaml manifest to the repository."
    )


# ── local ────────────────────────────────────────────────────────────────────


def _install_local(path_str: str, tid: str) -> dict[str, Any]:
    path = Path(path_str).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"Local path is not a directory: {path}")

    manifest_path = path / "mcp-tool.yaml"
    if manifest_path.is_file():
        entry = _read_manifest_yaml(manifest_path, path, tid)
    else:
        entry = _entry_from_local_dir(path, tid)

    _cat.add_tool(tid, entry)
    return entry


def _read_manifest_yaml(manifest_path: Path, base_path: Path, tid: str) -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        raise RuntimeError(
            "PyYAML is required to read mcp-tool.yaml manifests. "
            "Install it via: pip install pyyaml"
        ) from None

    with open(manifest_path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    if not isinstance(raw, dict):
        raise ValueError(f"Invalid mcp-tool.yaml at {manifest_path}")

    tool_id = raw.get("id") or _safe_id(f"local:{base_path.name}")
    runtime = raw.get("runtime", {})
    if not runtime.get("command"):
        raise ValueError(f"mcp-tool.yaml at {manifest_path} missing runtime.command")

    cmd = runtime["command"]
    if not os.path.isabs(cmd) and not _is_system_command(cmd):
        runtime = dict(runtime)
        runtime["command"] = str(base_path / cmd)

    return {
        "id": tool_id,
        "source": f"local:{base_path}",
        "installed_at": datetime.now(tz=timezone.utc).isoformat(),
        "runtime": runtime,
        "secrets": raw.get("secrets", []),
        "compliance": raw.get("compliance", {"locality": "local", "network_egress": "none"}),
    }


def _entry_from_local_dir(path: Path, tid: str) -> dict[str, Any]:
    tool_id = _safe_id(f"local:{path.name}")
    if (path / "package.json").is_file():
        runtime: dict[str, Any] = {"command": "node", "args": [str(path / "index.js")]}
    elif (path / "pyproject.toml").is_file() or (path / "setup.py").is_file():
        runtime = {"command": "python3", "args": [str(path / "main.py")]}
    else:
        raise ValueError(
            f"Cannot auto-detect runtime for local tool at {path}. "
            "Add an mcp-tool.yaml manifest or use a recognised structure "
            "(package.json for Node, pyproject.toml/setup.py for Python)."
        )
    return {
        "id": tool_id,
        "source": f"local:{path}",
        "installed_at": datetime.now(tz=timezone.utc).isoformat(),
        "runtime": runtime,
        "secrets": [],
        "compliance": {"locality": "local", "network_egress": "none"},
    }


def _is_system_command(cmd: str) -> bool:
    return not (cmd.startswith("./") or cmd.startswith("../") or "/" in cmd)


# ── docker (M4) ──────────────────────────────────────────────────────────────


def _docker_bin() -> str:
    """Return the path to the docker binary or raise ValueError."""
    docker = shutil.which("docker")
    if docker is None:
        raise ValueError(
            "docker is not available on PATH. "
            "Install Docker and ensure the daemon is running before using docker: sources."
        )
    return docker


def _docker_pull(image_ref: str) -> None:
    """Run `docker pull <image_ref>`. Raises ValueError on failure."""
    docker = _docker_bin()
    result = subprocess.run(
        [docker, "pull", image_ref],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise ValueError(
            f"docker pull {image_ref!r} failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )


def _docker_digest(image_ref: str) -> str:
    """Return the repo digest of *image_ref* (e.g. image@sha256:...).

    Raises ValueError when docker is unavailable or the image has no digest.
    """
    docker = _docker_bin()
    result = subprocess.run(
        [docker, "inspect", "--format={{index .RepoDigests 0}}", image_ref],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise ValueError(
            f"docker inspect {image_ref!r} failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    digest = result.stdout.strip()
    if not digest or digest == "<no value>":
        raise ValueError(
            f"No repo digest found for {image_ref!r}. "
            "The image may have been built locally without being pushed to a registry."
        )
    return digest


def _install_docker(spec: str, tid: str) -> dict[str, Any]:
    """Pull a Docker image, pin its digest, record runtime.

    spec is everything after the 'docker:' prefix, e.g. 'ghcr.io/foo/bar:1.2.3'.
    """
    # Validate the image spec: no whitespace, no shell metacharacters.
    if not spec or re.search(r'[\s;|&$`\\]', spec):
        raise ValueError(f"Invalid docker image spec: {spec!r}")

    image_ref = spec  # e.g. "ghcr.io/foo/bar:1.2.3" or "foo/bar:latest"
    tool_id = _safe_id(f"docker:{image_ref}")

    # Pull first so inspect finds the image.
    _docker_pull(image_ref)
    digest = _docker_digest(image_ref)

    # Runtime: run as an MCP server over stdio.
    entry: dict[str, Any] = {
        "id": tool_id,
        "source": f"docker:{image_ref}",
        "installed_at": datetime.now(tz=timezone.utc).isoformat(),
        "docker_digest": digest,
        "runtime": {
            "command": "docker",
            "args": ["run", "--rm", "-i", "--", image_ref],
        },
        "secrets": [],
        # Docker daemon is local; image pull requires network.
        "compliance": {"locality": "local", "network_egress": "required"},
    }
    _cat.add_tool(tid, entry)
    return entry


def verify_docker_digest(entry: dict[str, Any]) -> bool:
    """Verify that the running image still matches the pinned digest.

    Called at spawn time when the tool source is docker:.
    Returns True iff the live digest matches.  Returns False (never raises)
    when docker is unavailable — callers decide whether to block the spawn.
    """
    try:
        pinned = entry.get("docker_digest") or ""
        if not pinned:
            return False  # No digest pinned → cannot verify → block
        source = entry.get("source") or ""
        if not source.startswith("docker:"):
            return False
        image_ref = source[len("docker:"):]
        live_digest = _docker_digest(image_ref)
        return live_digest == pinned
    except Exception:  # noqa: BLE001
        return False


# ── update helper (M4) ───────────────────────────────────────────────────────


def update(tool_id: str, tid: str = "_default") -> dict[str, Any]:
    """Re-pin a tool to its latest available version.

    Source-type behaviour:
      npm:   re-install <package>@latest → new catalog entry
      pip:   re-install <package>@latest → new catalog entry
      docker: docker pull → new digest pinned in catalog entry
      github: raises ValueError (requires explicit new tag)
      local:  no-op, returns existing entry unchanged

    Returns the (updated) catalog entry.
    Raises ValueError when the tool is not installed or update is not
    supported for the source type.
    """
    entry = _cat.get_tool(tid, tool_id)
    if entry is None:
        raise ValueError(f"Tool {tool_id!r} is not installed for tenant {tid!r}")

    source = entry.get("source") or ""

    if source.startswith("npm:"):
        # Extract base package name (strip @version suffix if present).
        pkg_spec = source[len("npm:"):]
        # Scoped: @scope/name[@ver] → @scope/name
        # Unscoped: name[@ver] → name
        m = re.match(r"(@[^@\s/]+/[^@\s]+|[^@\s/]+)(?:@.*)?$", pkg_spec)
        if not m:
            raise ValueError(f"Cannot parse npm package from source {source!r}")
        pkg = m.group(1)
        return install(f"npm:{pkg}@latest", tid)

    if source.startswith("pip:"):
        pkg_spec = source[len("pip:"):]
        # pip uses == for versioning: name==ver → name
        pkg = re.sub(r"==.*$", "", pkg_spec).strip()
        return install(f"pip:{pkg}", tid)

    if source.startswith("docker:"):
        image_ref = source[len("docker:"):]
        _docker_pull(image_ref)
        new_digest = _docker_digest(image_ref)
        old_digest = entry.get("docker_digest") or ""
        entry = dict(entry)
        entry["docker_digest"] = new_digest
        entry["installed_at"] = datetime.now(tz=timezone.utc).isoformat()
        _cat.add_tool(tid, entry)
        if new_digest != old_digest:
            return entry  # digest changed
        return entry  # up to date

    if source.startswith("github:"):
        raise ValueError(
            f"Cannot auto-update GitHub source {source!r}. "
            "GitHub installs are pinned to a specific tag or SHA. "
            "To update, run: corvin-mcp install github:<owner>/<repo>@<new-tag>"
        )

    if source.startswith("local:"):
        # Local tools are not managed — nothing to update.
        return entry

    raise ValueError(f"Unsupported source type for update: {source!r}")
