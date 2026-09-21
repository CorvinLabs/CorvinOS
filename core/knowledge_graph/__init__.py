"""Knowledge Graph Module — ADR-0671 Integration.

Provides:
- MCP tools under ``core.knowledge_graph.mcp.tools`` (learning-loop queries)
- ``LearningLoopService`` under ``core.knowledge_graph.mcp.learning_loop_service``
- Tenant-scoped storage under ``core.knowledge_graph.storage``

This package intentionally exports NOTHING at the top level. It used to
re-export a ``.mcp_tools`` module that was never written, so importing any
submodule (including the learning-loop service the console panel depends on)
raised ModuleNotFoundError. The console route catches that and degrades to
HTTP 503, which is why /app/learning-loops showed "Learning subsystem not
available" on every install. Import submodules directly.
"""

__version__ = "0.1.0"
__all__: list[str] = []
