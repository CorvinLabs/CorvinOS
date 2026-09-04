/**
 * Audit Graph Types (ADR-0564, Phase 5 Graph Engineering)
 *
 * Immutable, hash-chained audit events represented as a directed acyclic graph (DAG).
 * Each node is an audit event; each edge is a hash-chain link or causality link.
 */

// ─────────────────────────────────────────────────────────────────────────────
// Audit Event Types
// ─────────────────────────────────────────────────────────────────────────────

export type AuditEventType =
  | 'skill_executed'
  | 'learning_event'
  | 'decision'
  | 'context_snapshot'
  | 'error';

export type SkillExecutionStatus = 'success' | 'partial' | 'failed' | 'timeout';
export type LearningEventType = 'outcome' | 'preference' | 'confidence' | 'metric';
export type DecisionOutcome = 'correct' | 'partial' | 'incorrect' | 'in_progress';

// Base event (all events have these fields)
export interface AuditEvent {
  id: string;
  type: AuditEventType;
  timestamp: string; // ISO8601
  hash: string; // SHA256 of this event
  prev_hash: string; // SHA256 of prior event (chain link)
  lom_hash: string; // Line of Moral Responsibility (source code binding)
  tenant_id: string; // GDPR tenant scoping
  // What the backend ACTUALLY delivers per event (routes/vibe_engineering.py
  // ::get_audit_chain maps real chain records to {event_type, details,
  // severity} and only classifies `type`). The typed fields on the subtypes
  // below are the design-time ideal and are OPTIONAL: none of them is
  // populated today, and rendering them unguarded crashed the Inspector
  // ("Cannot read properties of undefined (reading 'toFixed')", 2026-09-04).
  event_type?: string; // raw chain event_type, e.g. "tier2_layer_injected"
  details?: Record<string, unknown>;
  severity?: string;
}

// Skill Execution Event
export interface SkillExecutedEvent extends AuditEvent {
  type: 'skill_executed';
  skill_id?: string;
  skill_version?: string;
  status?: SkillExecutionStatus;
  latency_ms?: number;
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
  error?: {
    type: string;
    message: string;
  };
}

// Learning Event
export interface LearningEventRecord extends AuditEvent {
  type: 'learning_event';
  skill_id?: string;
  event_type?: LearningEventType | string;
  signal?: unknown; // Type depends on event_type
  confidence_before?: number;
  confidence_after?: number;
  confidence_delta?: number;
}

// Decision Event
export interface DecisionEvent extends AuditEvent {
  type: 'decision';
  decision_name?: string;
  skill_id?: string;
  confidence?: number;
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
}

// Context Snapshot Event
export interface ContextSnapshotEvent extends AuditEvent {
  type: 'context_snapshot';
  context_id?: string;
  entropy_score?: number;
  tier_1_count?: number;
  tier_2_count?: number;
  tier_3_count?: number;
  merge_status?: 'success' | 'conflict' | 'failed';
}

// Error Event
export interface ErrorEvent extends AuditEvent {
  type: 'error';
  error_type?: string;
  error_message?: string;
  related_skill?: string;
  stack_trace?: string;
}

export type AnyAuditEvent =
  | SkillExecutedEvent
  | LearningEventRecord
  | DecisionEvent
  | ContextSnapshotEvent
  | ErrorEvent;

// ─────────────────────────────────────────────────────────────────────────────
// Graph Structure
// ─────────────────────────────────────────────────────────────────────────────

export type EdgeType = 'hash_chain' | 'causes' | 'feedback_for' | 'dependency';

export interface GraphNode {
  id: string;
  type: AuditEventType;
  timestamp: string;
  hash: string;
  lom_hash: string;
  // Rendering hints
  label: string; // Display name
  data: AnyAuditEvent; // Full event data
}

export interface GraphEdge {
  id: string;
  source: string; // Node ID
  target: string; // Node ID
  type: EdgeType;
  hash: string; // Edge integrity
  // Rendering hints
  label?: string;
  weight?: number; // For strength visualization
}

export interface AuditGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  metadata: {
    chainHeight: number; // Total events in chain
    nodeCount: number;
    edgeCount: number;
    timespan: {
      start: string;
      end: string;
    };
    snapshotFreshness_ms: number; // How old is this snapshot?
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Query Filters
// ─────────────────────────────────────────────────────────────────────────────

export interface AuditQueryFilter {
  since?: string; // ISO8601, e.g., "2026-09-03T21:29:00Z"
  until?: string;
  limit?: number; // Default: 100
  types?: AuditEventType[]; // Filter by event type
  skillIds?: string[]; // Filter by skill
  outcomes?: DecisionOutcome[]; // Filter by outcome
}

export interface AuditQueryResult {
  events: AnyAuditEvent[];
  graph: AuditGraph; // Rendered as graph
  nextCursor?: string; // For pagination
  hasMore: boolean;
  snapshotFreshness_ms: number;
}

// ─────────────────────────────────────────────────────────────────────────────
// Cytoscape.js Compatible Format
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Cytoscape.js uses a specific JSON format for nodes and edges.
 * This helper converts AuditGraph to Cytoscape format.
 */

export interface CytoscapeNode {
  data: {
    id: string;
    label: string;
    type: AuditEventType;
    hash: string;
    timestamp: string;
    // Store full event for inspector
    event: AnyAuditEvent;
  };
}

export interface CytoscapeEdge {
  data: {
    id: string;
    source: string;
    target: string;
    type: EdgeType;
    label?: string;
  };
}

export interface CytoscapeData {
  nodes: CytoscapeNode[];
  edges: CytoscapeEdge[];
}

/**
 * Convert AuditGraph to Cytoscape format
 */
export function auditGraphToCytoscape(graph: AuditGraph): CytoscapeData {
  return {
    nodes: graph.nodes.map((node) => ({
      data: {
        id: node.id,
        label: node.label,
        type: node.type,
        hash: node.hash,
        timestamp: node.timestamp,
        event: node.data,
      },
    })),
    edges: graph.edges.map((edge) => ({
      data: {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        type: edge.type,
        label: edge.label,
      },
    })),
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Type Guards
// ─────────────────────────────────────────────────────────────────────────────

export function isSkillExecutedEvent(event: AnyAuditEvent): event is SkillExecutedEvent {
  return event.type === 'skill_executed';
}

export function isLearningEvent(event: AnyAuditEvent): event is LearningEventRecord {
  return event.type === 'learning_event';
}

export function isDecisionEvent(event: AnyAuditEvent): event is DecisionEvent {
  return event.type === 'decision';
}

export function isContextSnapshotEvent(event: AnyAuditEvent): event is ContextSnapshotEvent {
  return event.type === 'context_snapshot';
}

export function isErrorEvent(event: AnyAuditEvent): event is ErrorEvent {
  return event.type === 'error';
}
