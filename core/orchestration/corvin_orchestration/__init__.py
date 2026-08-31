"""corvin-orchestration — ADR-0190 M4/M5/M6 (chat-native orchestration).

Consolidates three previously chat-unreachable subsystems behind one MCP
server so a persona opting into ``orchestration_enabled`` gets all of them
without tripling stdio-JSON-RPC boilerplate:

- ``workflow_run`` / ``workflow_resume`` / ``workflow_list_paused`` — AWP
  DAG-Workflows (``core/workflows/corvin_workflows``, the canonical
  "workflow" engine per ADR-0190), driven directly via ``DAGRunner`` /
  ``resume_workflow`` — NOT the console's separate hand-rolled executor.
- ``a2a_send`` / ``a2a_list_endpoints`` — instance-to-instance task
  delegation (``operator/bridges/shared/remote_trigger_sender.py``).
- ``acs_delegate`` — the Autonomous Compute Shell delegation_loop engine
  (``operator/bridges/shared/acs_engine_adapter.py::run_acs_workflow``).

Each of the three groups degrades independently and gracefully: if its
external package isn't importable (e.g. a base install without the
workflows plugin), its tools are simply omitted from ``tools/list``
rather than crashing the server.
"""

from __future__ import annotations
