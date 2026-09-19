"""Knowledge Graph Module — ADR-0671 Integration.

Provides:
- KnowledgeGraphBuilder: Tenant-scoped graph indexing
- MCP Tools: kg.* query/export/trace tools for console + API
- ADR metadata extraction + relationship inference
- Audit trail integration (tenant-scoped, fail-closed)
"""

from .mcp_tools import (
    KGQueryTool,
    KGFindADRByPathTool,
    KGTraceDecisionChainTool,
    KGListMetricsTool,
    KGSearchFulltextTool,
    KGExportGraphTool,
    get_all_kg_tools,
)

__version__ = "0.1.0"
__all__ = [
    "KGQueryTool",
    "KGFindADRByPathTool",
    "KGTraceDecisionChainTool",
    "KGListMetricsTool",
    "KGSearchFulltextTool",
    "KGExportGraphTool",
    "get_all_kg_tools",
]
