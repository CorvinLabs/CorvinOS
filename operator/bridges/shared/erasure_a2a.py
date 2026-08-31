"""Layer 38 — ADR-0077 C-3: A2A Erasure Handler (L36 integration).

Implements :class:`ErasureHandler` Protocol for A2A-related data, as
required by GDPR Art. 17 and the L36 erasure orchestrator (ADR-0045).

Scope of erasure
----------------
A2A-related data that can be attributed to a ``subject_id`` lives in:

  * **Worker session pins** (ADR-0049) — JSON files under
    ``<tenant_home>/global/sessions/*/worker_sessions/``.  Each record
    carries a ``scope_label`` and a ``persona`` field.  We purge records
    whose ``scope_label`` starts with ``<subject_id>`` or ``<subject_id>:``.

  * **Nonce store** — nonces are not attributable to a subject; skipped.
  * **Origin/endpoint configs** — not personal data; skipped.

The handler returns:

  * ``APPLIED`` + ``count=N`` — if ≥1 record was deleted.
  * ``SKIPPED`` — if no records matched the subject_id.

ADR-0077 Must NOT do: delete entire session directories (only individual
matched records). Must NOT put subject_id or matched file paths in audit
``details``. Must NOT ``import anthropic`` (CI AST lint enforces).
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

# Import via lazy path so this module works standalone in tests.
try:
    from erasure_orchestrator import (  # type: ignore[import-not-found]
        ErasureLayerResult, LayerStatus,
    )
except ImportError:
    import sys
    _shared = Path(__file__).resolve().parent
    if str(_shared) not in sys.path:
        sys.path.insert(0, str(_shared))
    from erasure_orchestrator import (  # type: ignore[import-not-found]
        ErasureLayerResult, LayerStatus,
    )


@dataclass
class A2AErasureHandler:
    """Layer 36 erasure handler for A2A worker-session data.

    Registered via ``orchestrator.register_handler(A2AErasureHandler(...))``.
    """

    layer_id: str = "L38-a2a"
    tenant_home: Path | None = None

    def purge(self, subject_id: str, request_id: str) -> ErasureLayerResult:
        """Delete A2A worker-session records matching ``subject_id``.

        Matches on ``scope_label`` prefix: ``<subject_id>`` or
        ``<subject_id>:``. The colon form allows ``channel:chat_key``
        style identifiers (consistent with L36 convention).
        """
        start = time.time()
        roots = self._session_roots()
        count = 0
        for ws_dir in roots:
            count += self._purge_in(ws_dir, subject_id)
        duration_ms = int((time.time() - start) * 1000)

        if count > 0:
            return ErasureLayerResult(
                layer_id=self.layer_id,
                status=LayerStatus.APPLIED,
                count=count,
                reason="",
                duration_ms=duration_ms,
            )
        return ErasureLayerResult(
            layer_id=self.layer_id,
            status=LayerStatus.SKIPPED,
            count=0,
            reason="no_a2a_session_records_found",
            duration_ms=duration_ms,
        )

    # ── Internals ─────────────────────────────────────────────────────

    def _session_roots(self) -> list[Path]:
        """Return all worker_sessions/ dirs under the tenant's sessions/."""
        home = self.tenant_home or self._default_tenant_home()
        sessions_base = home / "global" / "sessions"
        roots: list[Path] = []
        if not sessions_base.exists():
            return roots
        for entry in sessions_base.iterdir():
            ws = entry / "worker_sessions"
            if ws.is_dir():
                roots.append(ws)
        return roots

    @staticmethod
    def _default_tenant_home() -> Path:
        return Path(os.environ.get("CORVIN_HOME", Path.home() / ".corvin"))

    @staticmethod
    def _purge_in(ws_dir: Path, subject_id: str) -> int:
        """Delete session records matching subject_id in one worker_sessions/ dir."""
        count = 0
        prefix_bare = subject_id
        prefix_colon = subject_id + ":"
        for f in list(ws_dir.iterdir()):
            if not f.is_file() or f.suffix not in (".json", ""):
                continue
            try:
                data = json.loads(f.read_text("utf-8"))
            except Exception:  # noqa: BLE001
                continue
            scope_label = str(data.get("scope_label", ""))
            if scope_label == prefix_bare or scope_label.startswith(prefix_colon):
                try:
                    f.unlink()
                    count += 1
                except OSError:
                    pass
        return count


__all__ = ["A2AErasureHandler"]
