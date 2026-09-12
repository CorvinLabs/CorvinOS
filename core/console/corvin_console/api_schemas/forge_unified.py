"""Unified API Schema — Tools, Skills, OS-Skills management.

ADR-TBD: Unified Forge Control Plane (console-side schema for backend routes).

Three subsystems converge in the Console Forge panel:
  1. Tools — MCP tools (read-only discovery, enable/disable lifecycle)
  2. Skills — SkillForge-generated skills (versioning, rollback, learning state)
  3. OS-Skills — kernel-level skills (delegation_router, context_adapter, etc.)
     with tunable parameters (ADR-0532 Agentic Control Plane)

Audit trail (ADR-0537):
  - Every enable/disable/config change → audit event (forge_tool_enabled, etc.)
  - No endpoint without audit emission (handled by route, not schema)

Tenant isolation (ADR-0007):
  - tenant_id from SessionRecord, never env var
  - All list queries filtered by tenant
  - No cross-tenant leakage in responses

PII Scrubbing (GDPR Art. 32):
  - No user data in tool/skill/os-skill manifests
  - No prompts/transcripts in learning state
  - Schemas enforce fail-closed validation

Learning Integration (ADR-0314):
  - Skills carry confidence_score, outcome_feedback history
  - Skill rollback persists prior version → recovery + audit
  - OS-Skills expose learning_state (convergence metrics)

DAG + Dependencies (ADR-0535):
  - Forge graph endpoint returns nodes (tools/skills/os-skills) + edges (calls, depends_on)
  - Cross-subsystem dependencies visible (Skill A calls Tool B, OS-Skill C routes to Skill D)
  - Circular dependencies detected at query time (not at storage)

E2E Wiring Proof (ADR-0610):
  - Every tool must have ≥1 call site (found via tool_call_sites endpoint, TBD)
  - Every skill must have ≥1 execution (skill_executions endpoint, TBD)
  - Dashboard shows unreachable items (dead code warning)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# =============================================================================
# Shared Enums
# =============================================================================

class SkillVersionStatus(str, Enum):
    """Skill version lifecycle."""
    ACTIVE = "active"          # Currently in use
    ROLLBACK_AVAILABLE = "rollback_available"  # Can be restored to
    DEPRECATED = "deprecated"  # Old version, not recommended
    BROKEN = "broken"          # Failed tests or healthcheck


class ToolStatus(str, Enum):
    """Tool lifecycle state."""
    AVAILABLE = "available"
    ENABLED = "enabled"
    DISABLED = "disabled"
    ERROR = "error"


class OSSkillStatus(str, Enum):
    """OS-Skill execution status."""
    ACTIVE = "active"
    LEARNING = "learning"      # In feedback loop, optimizing config
    DEGRADED = "degraded"      # Falling back to baseline
    DISABLED = "disabled"


class DependencyType(str, Enum):
    """Type of dependency between components."""
    CALLS = "calls"            # A calls B
    DEPENDS_ON = "depends_on"  # A requires B at runtime
    REPLACES = "replaces"      # A supersedes B
    AUGMENTS = "augments"      # A wraps/extends B


class AuditEventType(str, Enum):
    """Audit event types for Forge subsystem."""
    TOOL_ENABLED = "forge.tool_enabled"
    TOOL_DISABLED = "forge.tool_disabled"
    TOOL_ERROR = "forge.tool_error"
    SKILL_EXECUTED = "forge.skill_executed"
    SKILL_ROLLBACK = "forge.skill_rollback"
    SKILL_PINNED = "forge.skill_pinned"
    SKILL_FEEDBACK = "forge.skill_feedback"
    OSSKILL_CONFIG_UPDATED = "forge.osskill_config_updated"
    OSSKILL_CONFIG_REVERTED = "forge.osskill_config_reverted"
    OSSKILL_LEARNING_STARTED = "forge.osskill_learning_started"
    OSSKILL_LEARNING_CONVERGED = "forge.osskill_learning_converged"


# =============================================================================
# Tools API (MCP Tool Management)
# =============================================================================

class ToolManifest(BaseModel):
    """MCP tool metadata (static, from tool definition)."""
    model_config = {"extra": "forbid"}

    tool_id: str = Field(..., description="Unique tool identifier (e.g. 'git_clone')")
    name: str = Field(..., description="Human-readable tool name")
    description: str = Field(..., description="What this tool does")
    category: str = Field(
        ...,
        description="Category (e.g. 'version-control', 'file-io', 'network')"
    )

    # MCP server metadata
    mcp_server_id: Optional[str] = Field(
        None, description="ID of the MCP server providing this tool"
    )
    mcp_server_url: Optional[str] = Field(
        None, description="URL/endpoint of MCP server (if remote)"
    )

    # Schema and contract
    input_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema for tool input parameters"
    )
    output_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema for tool output"
    )

    # Metadata
    version: str = Field(..., description="Tool version (semver)")
    author: str = Field(..., description="Tool author/origin")
    tags: list[str] = Field(default_factory=list, description="Search tags")

    # Safety & compliance
    requires_approval: bool = Field(
        False, description="Requires manual approval before each use"
    )
    sandbox_required: bool = Field(
        False, description="Must run in isolated sandbox"
    )
    audit_enabled: bool = Field(
        True, description="Tool invocations are audited"
    )


class ToolState(BaseModel):
    """Runtime state of a tool."""
    model_config = {"extra": "forbid"}

    tool_id: str
    status: ToolStatus
    enabled: bool

    # Runtime metrics
    call_count: int = Field(default=0, description="Lifetime invocations")
    error_count: int = Field(default=0, description="Failed invocations")
    last_called: Optional[datetime] = None
    avg_latency_ms: Optional[float] = None

    # Error tracking
    last_error: Optional[str] = Field(
        None, description="Last error message (PII-scrubbed)"
    )
    error_rate: Optional[float] = Field(
        None, description="P(tool fails) over last 100 calls"
    )

    # Dependencies
    depends_on_tools: list[str] = Field(
        default_factory=list, description="Tools this tool calls internally"
    )
    used_by_skills: list[str] = Field(
        default_factory=list, description="Skills that call this tool"
    )


class ToolResponse(BaseModel):
    """Complete tool info (manifest + state)."""
    manifest: ToolManifest
    state: ToolState

    # Audit info
    enabled_at: Optional[datetime] = None
    disabled_at: Optional[datetime] = None
    enabled_by: Optional[str] = Field(None, description="User who enabled tool")


class ToolListResponse(BaseModel):
    """List of tools with basic state."""
    tools: list[ToolResponse]
    total: int
    limit: int
    offset: int


class ToolEnableRequest(BaseModel):
    """Request to enable a tool."""
    reason: Optional[str] = Field(
        None, description="Why enabling this tool (for audit trail)"
    )


class ToolDisableRequest(BaseModel):
    """Request to disable a tool."""
    reason: Optional[str] = Field(
        None, description="Why disabling (for audit trail)"
    )
    force: bool = Field(
        False, description="Force disable even if used by active skills"
    )


# =============================================================================
# Skills API (SkillForge Skill Management)
# =============================================================================

class SkillMetadata(BaseModel):
    """Skill metadata (from manifest)."""
    model_config = {"extra": "forbid"}

    skill_id: str = Field(..., description="Unique skill identifier")
    name: str
    description: str

    # Versioning
    current_version: str = Field(..., description="Active version (semver)")
    available_versions: list[str] = Field(
        default_factory=list, description="Versions available for rollback"
    )

    # Source & authorship
    author: str = Field(..., description="Skill author")
    origin: str = Field(
        ...,
        description="Origin: 'builtin' | 'community' | 'personal'"
    )

    # Scope
    scope: str = Field(
        ...,
        description="Scope: 'assistant' | 'project' | 'workspace' | 'global'"
    )

    # Categories
    tags: list[str] = Field(default_factory=list)

    # Dependencies
    depends_on_skills: list[str] = Field(
        default_factory=list, description="Skills this skill composes with"
    )
    depends_on_tools: list[str] = Field(
        default_factory=list, description="Tools this skill invokes"
    )


class SkillLearningState(BaseModel):
    """Learning metrics for a skill (ADR-0314)."""
    confidence_score: float = Field(
        ..., ge=0.0, le=1.0,
        description="P(skill makes correct decision | input), updated by feedback loop"
    )

    execution_count: int = Field(default=0, description="Lifetime executions")
    success_count: int = Field(default=0, description="Successful outcomes")

    avg_latency_ms: Optional[float] = None
    p95_latency_ms: Optional[float] = None

    # Feedback history (last N)
    recent_feedback: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Last 5 feedback events: {timestamp, type, signal, confidence_delta}"
    )

    # Optimization state
    learning_active: bool = Field(
        False, description="Is the skill in an active optimization loop?"
    )
    convergence_estimate: Optional[float] = Field(
        None, description="Estimated % of convergence (0.0–1.0)"
    )

    last_feedback_at: Optional[datetime] = None
    last_config_update_at: Optional[datetime] = None


class SkillVersionInfo(BaseModel):
    """Info about a specific skill version."""
    version: str
    status: SkillVersionStatus
    created_at: datetime
    created_by: Optional[str] = None

    # Manifest snapshot
    description: Optional[str] = None
    tags: list[str] = Field(default_factory=list)

    # Test results
    test_count: int = 0
    test_pass_count: int = 0

    # Rollback info
    can_rollback_to: bool = Field(
        False, description="Can restore from this version"
    )
    pinned: bool = Field(False, description="Pinned to this version")


class SkillResponse(BaseModel):
    """Complete skill info."""
    metadata: SkillMetadata
    learning_state: SkillLearningState
    versions: list[SkillVersionInfo]


class SkillListResponse(BaseModel):
    """List of skills."""
    skills: list[SkillResponse]
    total: int
    limit: int
    offset: int


class SkillRollbackRequest(BaseModel):
    """Request to roll back to a prior version."""
    version: str = Field(..., description="Version to restore")
    reason: Optional[str] = Field(
        None, description="Why rolling back (for audit trail)"
    )


class SkillPinRequest(BaseModel):
    """Request to pin skill to a specific version."""
    version: str = Field(..., description="Version to pin to")
    reason: Optional[str] = None


class SkillFeedbackRequest(BaseModel):
    """Feedback on a skill execution (for learning loop)."""
    skill_id: str
    execution_id: str = Field(
        ..., description="ID of the execution being evaluated"
    )

    # Feedback type
    outcome: str = Field(
        ...,
        description="'correct' | 'incorrect' | 'partial' | 'unknown'"
    )
    confidence: Optional[float] = Field(
        None, ge=0.0, le=1.0,
        description="How confident is the user in this assessment?"
    )

    # Optional reasoning (PII-scrubbed before storage)
    notes: Optional[str] = Field(
        None,
        description="User notes on why this outcome (audit-logged, fail-closed scrubbing)"
    )

    # Alternative action preference
    preferred_action: Optional[str] = Field(
        None, description="What the skill should have done instead"
    )


# =============================================================================
# OS-Skills API (Kernel-Level Skills with Learning)
# =============================================================================

class OSSkillParameter(BaseModel):
    """Tunable parameter for an OS-Skill."""
    name: str = Field(..., description="Parameter name (e.g. 'confidence_threshold')")
    value: Any = Field(...)
    value_type: str = Field(
        ..., description="'float' | 'int' | 'bool' | 'string'"
    )

    # Bounds for numeric types
    min_value: Optional[float] = None
    max_value: Optional[float] = None

    # Valid options for enum-like params
    options: Optional[list[Any]] = None

    # Learn ability
    learnable: bool = Field(
        False,
        description="Can optimizer adjust this parameter from feedback?"
    )

    # Tuning history
    default_value: Any = Field(...)
    current_confidence: Optional[float] = Field(
        None, description="Confidence in current value (ADR-0314)"
    )
    tuning_history: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Last 5 optimization steps: {timestamp, old_value, new_value, delta_confidence}"
    )


class OSSkillConfig(BaseModel):
    """Configuration state of an OS-Skill."""
    skill_id: str

    # Current parameters
    parameters: dict[str, OSSkillParameter]

    # Version tracking
    config_version: str = Field(..., description="Config version (for rollback)")
    config_created_at: datetime
    config_applied_at: Optional[datetime] = None

    # Baseline config (for rollback)
    baseline_config: dict[str, Any] = Field(
        ..., description="Original config before any learning adjustments"
    )


class OSSkillMetrics(BaseModel):
    """Performance metrics for an OS-Skill."""
    execution_count: int
    avg_latency_ms: float
    error_count: int
    success_rate: float = Field(..., ge=0.0, le=1.0)

    # Learning metrics
    confidence_convergence_rate: Optional[float] = Field(
        None, description="% improvement in confidence per 100 executions"
    )
    last_config_update_at: Optional[datetime] = None
    time_since_last_update_s: Optional[float] = None


class OSSkillResponse(BaseModel):
    """Complete OS-Skill info."""
    skill_id: str = Field(
        ...,
        description="e.g. 'os.delegation_router', 'os.context_adapter'"
    )
    name: str
    description: str

    status: OSSkillStatus

    # Execution & learning
    config: OSSkillConfig
    metrics: OSSkillMetrics

    # Boot layer (ADR-0243)
    boot_layer: str = Field(
        ...,
        description="'compliance' | 'core' | 'bundled' | 'installed'"
    )

    # For meta-skills
    is_meta_skill: bool = Field(
        False, description="Meta-skill (audit-enforcing, never disableable)"
    )


class OSSkillListResponse(BaseModel):
    """List of OS-Skills."""
    skills: list[OSSkillResponse]
    total: int


class OSSkillConfigRequest(BaseModel):
    """Request to update OS-Skill parameters."""
    parameter_updates: dict[str, Any] = Field(
        ..., description="Mapping of param_name → new_value"
    )

    reason: Optional[str] = Field(
        None, description="Why changing these parameters (for audit)"
    )

    # Test before apply
    dry_run: bool = Field(
        False, description="Simulate the change without persisting"
    )


class OSSkillConfigResponse(BaseModel):
    """Response to config update request."""
    success: bool
    old_config: dict[str, Any]
    new_config: dict[str, Any]

    # If dry_run=true, warnings about what would change
    warnings: list[str] = Field(default_factory=list)

    # Audit info
    applied_at: datetime
    config_version: str


# =============================================================================
# Dependency Graph API
# =============================================================================

class GraphNode(BaseModel):
    """Node in the forge dependency graph."""
    node_id: str = Field(..., description="Unique node id")
    node_type: str = Field(
        ..., description="'tool' | 'skill' | 'os-skill'"
    )

    name: str
    status: str  # tool: ToolStatus | skill: version status | os-skill: OSSkillStatus

    # Quick stats
    execution_count: Optional[int] = None
    error_rate: Optional[float] = None
    confidence_score: Optional[float] = Field(
        None, description="Learning confidence (skills only)"
    )


class GraphEdge(BaseModel):
    """Dependency edge between nodes."""
    source_id: str
    target_id: str
    edge_type: DependencyType

    # Edge metadata
    call_count: int = Field(default=0, description="Times source called target")
    error_count: int = Field(default=0, description="Failed calls")


class GraphResponse(BaseModel):
    """Complete dependency graph."""
    nodes: list[GraphNode]
    edges: list[GraphEdge]

    # Diagnostics
    circular_dependencies: list[list[str]] = Field(
        default_factory=list,
        description="Cycles in the graph (list of node_id lists forming cycles)"
    )

    unreachable_nodes: list[str] = Field(
        default_factory=list,
        description="Nodes with no incoming edges (dead code warning)"
    )

    # Render hints
    layout_hint: str = Field(
        "dag", description="'dag' | 'force-directed' | 'hierarchical' for frontend viz"
    )


# =============================================================================
# Audit Trail API
# =============================================================================

class AuditEvent(BaseModel):
    """Audit event from forge subsystem."""
    event_id: str = Field(..., description="Unique event ID (ulid or uuid)")
    event_type: AuditEventType
    timestamp: datetime

    # What changed
    resource_type: str = Field(..., description="'tool' | 'skill' | 'os-skill'")
    resource_id: str

    # Audit details
    action: str = Field(
        ...,
        description="'enable' | 'disable' | 'execute' | 'config_update' | 'rollback'"
    )

    # Who (metadata-only, GDPR-compliant)
    user_id_fingerprint: Optional[str] = Field(
        None, description="Hashed user ID (PII-safe)"
    )

    # Impact
    before_state: Optional[dict[str, Any]] = Field(
        None, description="Prior state (e.g. old config)"
    )
    after_state: Optional[dict[str, Any]] = Field(
        None, description="New state"
    )

    # Reason (PII-scrubbed)
    reason: Optional[str] = None

    # Chain link (ADR-0232)
    prev_hash: Optional[str] = Field(None, description="Hash of previous event")
    hash: str = Field(..., description="SHA256 of this event")


class AuditQueryResponse(BaseModel):
    """Audit events query result."""
    events: list[AuditEvent]
    total: int
    limit: int
    offset: int

    # Chain integrity
    chain_verified: bool = Field(
        True, description="All hashes verified unbroken"
    )


# =============================================================================
# Search API
# =============================================================================

class SearchResult(BaseModel):
    """Single search result (unified across subsystems)."""
    id: str
    type: str = Field(..., description="'tool' | 'skill' | 'os-skill'")
    name: str
    description: str

    # Match info
    match_score: float = Field(
        ..., ge=0.0, le=1.0,
        description="BM25 relevance score"
    )
    matched_fields: list[str] = Field(
        ..., description="Which fields matched (e.g. ['name', 'description'])"
    )

    # Quick status
    status: str
    enabled: Optional[bool] = None


class SearchResponse(BaseModel):
    """Search results across all subsystems."""
    query: str
    results: list[SearchResult]
    total: int
    limit: int
    offset: int

    # Facets (for filtering)
    facets: dict[str, int] = Field(
        default_factory=dict,
        description="Count by type: {'tool': 5, 'skill': 3, 'os-skill': 2}"
    )


# =============================================================================
# Batch Operations
# =============================================================================

class BatchEnableToolsRequest(BaseModel):
    """Enable multiple tools at once."""
    tool_ids: list[str]
    reason: Optional[str] = None


class BatchDisableToolsRequest(BaseModel):
    """Disable multiple tools."""
    tool_ids: list[str]
    reason: Optional[str] = None
    force: bool = False


class BatchOperationResponse(BaseModel):
    """Result of a batch operation."""
    succeeded: list[str] = Field(default_factory=list)
    failed: list[dict[str, Any]] = Field(
        default_factory=list,
        description="[{id, error_message, http_status}]"
    )


# =============================================================================
# Health & Diagnostics
# =============================================================================

class ForgeHealthResponse(BaseModel):
    """Overall Forge subsystem health."""
    status: str = Field(..., description="'healthy' | 'degraded' | 'unhealthy'")
    timestamp: datetime

    # Component health
    tools_healthy: int
    tools_unhealthy: int

    skills_healthy: int
    skills_unhealthy: int

    osskills_healthy: int
    osskills_unhealthy: int

    # Audit trail
    audit_chain_verified: bool
    audit_lag_s: float = Field(..., description="Seconds since last audit write")

    # Warnings
    warnings: list[str] = Field(default_factory=list)


# =============================================================================
# Discovery & Capabilities
# =============================================================================

class ForgeCapabilitiesResponse(BaseModel):
    """Capabilities provided by Forge subsystem."""
    supports_tool_lifecycle: bool = True
    supports_skill_versioning: bool = True
    supports_skill_rollback: bool = True
    supports_osskill_learning: bool = True
    supports_dependency_graph: bool = True
    supports_audit_trail: bool = True
    supports_batch_operations: bool = True

    # Feature flags (future-extensible)
    experimental_features: list[str] = Field(default_factory=list)


class ForgeManifestResponse(BaseModel):
    """Complete manifest of all forge resources (for offline UI sync)."""
    tools_count: int
    skills_count: int
    osskills_count: int

    # Timestamp for cache validation
    generated_at: datetime

    # Nested for each subsystem (optional for offline use)
    tools_summary: Optional[list[dict[str, Any]]] = None  # [tool_id, name, status]
    skills_summary: Optional[list[dict[str, Any]]] = None  # [skill_id, name, version, status]
    osskills_summary: Optional[list[dict[str, Any]]] = None  # [skill_id, name, status]
