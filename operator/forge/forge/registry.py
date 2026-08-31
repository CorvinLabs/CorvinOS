"""Persistent tool registry.

Workspace layout (default root: ``.forge/``):

    .forge/
    ├── registry.json          manifest of all forged tools
    ├── tools/<name>.{py,sh}   implementations
    ├── skills/<name>/SKILL.md  promoted tools (durable across sessions)
    ├── audit.jsonl            create / delete / promote events
    └── memory.md              free-form notes (lessons, patterns)
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    runtime: str  # "python" | "bash"
    impl_path: str
    scope: str = "session"  # session | project | user
    created_at: float = field(default_factory=time.time)
    sha256: str = ""
    call_count: int = 0
    promoted: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ToolSpec":
        return cls(**d)


class Registry:
    MANIFEST_NAME = "registry.json"
    TOOLS_DIR = "tools"
    SKILLS_DIR = "skills"
    AUDIT_NAME = "audit.jsonl"
    MEMORY_NAME = "memory.md"

    def __init__(self, root: Path, *, hash_chain: bool = True):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / self.TOOLS_DIR).mkdir(exist_ok=True)
        (self.root / self.SKILLS_DIR).mkdir(exist_ok=True)
        self.manifest_path = self.root / self.MANIFEST_NAME
        self.lock_path = self.root / ".lock"
        self.hash_chain = hash_chain
        if not self.manifest_path.exists():
            self._atomic_write_text(self.manifest_path, "{}\n")
        memory = self.root / self.MEMORY_NAME
        if not memory.exists():
            self._atomic_write_text(
                memory,
                "# forge memory\n\n"
                "Free-form notes — lessons learned, patterns, gotchas. "
                "Append-only via `forge.py note`.\n\n",
            )

    # -- locking + atomic IO ------------------------------------------------

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        """Process-wide lock around manifest read-modify-write cycles."""
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @staticmethod
    def _atomic_write_text(path: Path, text: str) -> None:
        directory = path.parent
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(directory))
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    def _load(self) -> dict[str, dict]:
        try:
            text = self.manifest_path.read_text() or "{}"
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"registry manifest corrupted at {self.manifest_path}: {e}"
            ) from e

    def _save(self, data: dict[str, dict]) -> None:
        self._atomic_write_text(
            self.manifest_path, json.dumps(data, indent=2) + "\n"
        )

    def list(self) -> list[ToolSpec]:
        return [ToolSpec.from_dict(v) for v in self._load().values()]

    def get(self, name: str) -> ToolSpec | None:
        d = self._load().get(name)
        return ToolSpec.from_dict(d) if d else None

    def create(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        impl: str,
        runtime: str = "python",
        scope: str = "session",
        overwrite: bool = False,
        meta: dict[str, Any] | None = None,
    ) -> ToolSpec:
        # Allow alphanumerics plus _ and . (the dot enables AWP-style
        # namespacing like "csv.count" / "stats.median"). Reject path
        # traversal sequences and edge tokens that could collide with
        # filesystem semantics.
        if not name or len(name) > 128:
            raise ValueError(f"tool name must be 1..128 chars: {name!r}")
        if "/" in name or ".." in name \
                or name.startswith(".") or name.endswith("."):
            raise ValueError(f"tool name has illegal sequence: {name!r}")
        if not all(c.isalnum() or c in "._" for c in name):
            raise ValueError(
                f"tool name must be alphanumeric + . + _ : {name!r}"
            )
        if runtime not in {"python", "bash"}:
            raise ValueError(f"unsupported runtime: {runtime!r}")

        # Layer-16 v3 — validate meta.secrets at create time. The keys go on
        # the spec verbatim so the runner can resolve them later; values
        # never touch the spec. Fail-closed: a malformed list aborts the
        # create rather than silently dropping the entry.
        if meta and isinstance(meta, dict) and "secrets" in meta:
            from .secret_vault import SecretRefError, validate_secret_refs
            try:
                meta = dict(meta)
                meta["secrets"] = validate_secret_refs(meta.get("secrets"))
            except SecretRefError as exc:
                raise ValueError(f"meta.secrets invalid: {exc}") from exc

        with self._locked():
            data = self._load()
            if name in data and not overwrite:
                raise FileExistsError(
                    f"tool {name!r} already exists (use overwrite=True)"
                )
            # Layer-11 dialectic gate (forge_creation site). Heat is high
            # only when the new tool name collides with an existing one
            # OR an existing tool shares the same namespace prefix. The
            # decide() returns thesis ("create") for the common case where
            # heat is below threshold, so this adds zero overhead to fresh
            # names. Best-effort: silent on import / decide failures.
            ns = name.split(".", 1)[0]
            similar = [k for k in data
                       if k != name and k.split(".", 1)[0] == ns]
            collision = name in data
            try:
                self._dialectic_create(
                    name=name, description=description,
                    similar=similar, collision=collision,
                )
            except Exception:  # noqa: BLE001
                pass
            ext = ".py" if runtime == "python" else ".sh"
            impl_path = self.root / self.TOOLS_DIR / f"{name}{ext}"
            self._atomic_write_text(impl_path, impl)
            impl_path.chmod(0o600)

            sha = hashlib.sha256(impl.encode()).hexdigest()[:16]
            spec = ToolSpec(
                name=name,
                description=description,
                input_schema=input_schema,
                runtime=runtime,
                impl_path=str(impl_path),
                scope=scope,
                sha256=sha,
                meta=meta or {},
            )
            data[name] = asdict(spec)
            self._save(data)
            self._audit("create", spec)
            return spec

    def _dialectic_create(self, *, name: str, description: str,
                          similar: list[str], collision: bool) -> None:
        """Best-effort dialectic decision-point for forge tool creation.
        Higher heat when name collides or namespace already populated."""
        try:
            import sys as _sys
            from pathlib import Path as _Path
            here = _Path(__file__).resolve().parent
            shared = (here.parent.parent / "voice"
                      / "bridges" / "shared")
            if str(shared) not in _sys.path:
                _sys.path.insert(0, str(shared))
            import dialectic as _dialectic  # type: ignore
        except Exception:
            return
        consequence = 0.6 if (collision or similar) else 0.2
        uncertainty = 0.5 if similar else 0.1
        scope_n = 3 if collision else 1
        _dialectic.decide(
            site="forge_creation",
            thesis={"action": "create", "name": name},
            antithesis={"reason": "name-collision-or-similar",
                        "similar": similar[:5], "collision": collision},
            consequence=consequence,
            uncertainty=uncertainty,
            scope=scope_n,
        )

    def delete(self, name: str) -> None:
        with self._locked():
            data = self._load()
            spec = data.pop(name, None)
            if spec:
                try:
                    impl = Path(spec["impl_path"]).resolve()
                    tools_dir = (self.root / self.TOOLS_DIR).resolve()
                    if impl.is_relative_to(tools_dir):
                        impl.unlink()
                except FileNotFoundError:
                    pass
                self._save(data)
                self._audit("delete", ToolSpec.from_dict(spec))

    def bump_call(self, name: str) -> None:
        with self._locked():
            data = self._load()
            if name in data:
                data[name]["call_count"] = data[name].get("call_count", 0) + 1
                self._save(data)

    def promote(self, name: str) -> Path:
        """Materialize a forged tool as a real Skill folder.

        Mirrors Claude Code's skill layout: ``skills/<name>/SKILL.md`` plus
        the implementation alongside. Idempotent.
        """
        with self._locked():
            spec = self.get(name)
            if not spec:
                raise KeyError(name)
            skill_dir = self.root / self.SKILLS_DIR / name
            skill_dir.mkdir(parents=True, exist_ok=True)

            impl_src = Path(spec.impl_path)
            impl_dst = skill_dir / impl_src.name
            self._atomic_write_text(impl_dst, impl_src.read_text())

            skill_md = skill_dir / "SKILL.md"
            self._atomic_write_text(
                skill_md, _render_skill_md(spec, impl_dst.name)
            )

            data = self._load()
            data[name]["promoted"] = True
            data[name]["scope"] = "user"
            self._save(data)
            self._audit("promote", spec)
            return skill_dir

    def note(self, text: str) -> None:
        memory = self.root / self.MEMORY_NAME
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with self._locked(), memory.open("a") as fh:
            fh.write(f"\n## {ts}\n\n{text.rstrip()}\n")
            fh.flush()
            os.fsync(fh.fileno())

    def stats(self) -> dict[str, Any]:
        tools = self.list()
        events: list[dict] = []
        audit = self.root / self.AUDIT_NAME
        if audit.exists():
            for line in audit.read_text().splitlines():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        action_counts: dict[str, int] = {}
        for e in events:
            action_counts[e["action"]] = action_counts.get(e["action"], 0) + 1
        return {
            "tool_count": len(tools),
            "promoted_count": sum(1 for t in tools if t.promoted),
            "total_calls": sum(t.call_count for t in tools),
            "audit_events": len(events),
            "action_counts": action_counts,
            "tools": [
                {"name": t.name, "calls": t.call_count, "promoted": t.promoted}
                for t in sorted(tools, key=lambda x: -x.call_count)
            ],
        }

    _AUDIT_ACTION_MAP: dict[str, str] = {
        "create": "created",
        "delete": "deleted",
        "promote": "promoted",
    }

    def _audit(self, action: str, spec: ToolSpec) -> None:
        from .security_events import write_event
        persona = os.environ.get("FORGE_PERSONA", "")
        # Layer 9 — caller-persona attribution. The bridge adapter exports
        # CORVIN_CALLER_PERSONA per turn so the audit chain shows which
        # persona created / promoted / deleted a forged tool. Empty value
        # means the call ran without an attached persona (CLI use, legacy).
        caller_persona = os.environ.get("CORVIN_CALLER_PERSONA") or ""
        details: dict[str, Any] = {"sha": spec.sha256}
        if persona:
            details["persona"] = persona
        if caller_persona:
            details["caller_persona"] = caller_persona
        # Layer-16 v3 — record declared secret refs (names only, never values).
        if isinstance(spec.meta, dict):
            secret_refs = spec.meta.get("secrets")
            if isinstance(secret_refs, list) and secret_refs:
                details["secrets_declared"] = list(secret_refs)
        event_type = f"tool.{self._AUDIT_ACTION_MAP.get(action, action)}"
        write_event(
            self.root / self.AUDIT_NAME,
            event_type,
            tool=spec.name,
            details=details,
            hash_chain=self.hash_chain,
        )


def _render_skill_md(spec: ToolSpec, impl_filename: str) -> str:
    schema = json.dumps(spec.input_schema, indent=2)
    return (
        f"---\n"
        f"name: {spec.name}\n"
        f"description: {spec.description}\n"
        f"runtime: {spec.runtime}\n"
        f"sha256: {spec.sha256}\n"
        f"promoted_from: forge\n"
        f"---\n\n"
        f"# {spec.name}\n\n"
        f"{spec.description}\n\n"
        f"## Input schema\n\n"
        f"```json\n{schema}\n```\n\n"
        f"## Implementation\n\n"
        f"See `{impl_filename}` next to this file. The runtime reads a JSON "
        f"payload on stdin and writes a JSON result to stdout.\n\n"
        f"## Calling from forge\n\n"
        f"```bash\n"
        f"python3 forge.py call --name {spec.name} --input '<json>'\n"
        f"```\n"
    )
