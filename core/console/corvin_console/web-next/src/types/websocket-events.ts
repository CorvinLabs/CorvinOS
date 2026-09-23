/**
 * WebSocket Event Schema (Phase 3a, ADR-2050)
 *
 * Type definitions for real-time skill feedback updates via /v1/console/learning/stream
 *
 * Wire format:
 *   {
 *     "type": "confidence_updated" | "config_changed" | "error" | "subscribed" | "pong",
 *     "stream_id": "workflow-optimizer" | "security-orchestrator" | "flow-guard" | "metrics",
 *     "data": {...event-specific payload},
 *     "timestamp": "2026-09-25T12:34:56Z"
 *   }
 */

// ============================================================================
// Channel IDs
// ============================================================================

export type SkillStreamId =
  | "workflow-optimizer"
  | "security-orchestrator"
  | "flow-guard"
  | "metrics";

export const SKILL_STREAM_NAMES: Record<SkillStreamId, string> = {
  "workflow-optimizer": "Workflow Optimizer",
  "security-orchestrator": "Security Orchestrator",
  "flow-guard": "Flow Guard",
  "metrics": "Unified Metrics",
};

// ============================================================================
// Event Type Definitions
// ============================================================================

export interface ConfidenceUpdatedData {
  new_confidence: number;  // 0.0–1.0
  version: number;        // Config version (increments on each optimization)
  timestamp: string;      // ISO 8601 timestamp
}

export interface ConfigChangedData {
  config_version: number;      // New config version
  delta: Record<string, unknown>; // What changed (weights, thresholds, etc.)
  timestamp: string;          // ISO 8601 timestamp
}

export interface ErrorData {
  reason: string;                  // Human-readable error message
  recovery_time_seconds?: number;  // Estimated time to recovery (or null if unknown)
  timestamp: string;              // ISO 8601 timestamp
}

export interface SubscribedData {
  channel: SkillStreamId;
  timestamp: string;
}

export interface PongData {
  timestamp: string;
}

// ============================================================================
// Base Event Type (Discriminated Union)
// ============================================================================

export type WebSocketEvent =
  | { type: "confidence_updated"; stream_id: SkillStreamId; data: ConfidenceUpdatedData; timestamp: string }
  | { type: "config_changed"; stream_id: SkillStreamId; data: ConfigChangedData; timestamp: string }
  | { type: "error"; stream_id: SkillStreamId; data: ErrorData; timestamp: string }
  | { type: "subscribed"; channel: SkillStreamId; timestamp: string }
  | { type: "pong"; timestamp: string };

// ============================================================================
// Client → Server Messages
// ============================================================================

export interface SubscribeMessage {
  action: "subscribe";
  channel: SkillStreamId;
}

export interface UnsubscribeMessage {
  action: "unsubscribe";
  channel: SkillStreamId;
}

export interface PingMessage {
  action: "ping";
}

export type WebSocketClientMessage = SubscribeMessage | UnsubscribeMessage | PingMessage;

// ============================================================================
// Hook State
// ============================================================================

export interface SkillStreamState {
  isConnected: boolean;
  subscribedChannels: Set<SkillStreamId>;
  lastUpdate: WebSocketEvent | null;
  error: string | null;
  isReconnecting: boolean;
  connectionAttempts: number;
}

// ============================================================================
// Event Handlers
// ============================================================================

export type OnUpdateCallback = (event: WebSocketEvent) => void;
export type OnErrorCallback = (error: string) => void;
export type OnConnectCallback = () => void;
export type OnDisconnectCallback = () => void;

// ============================================================================
// Helpers
// ============================================================================

/**
 * Type guard: check if event is ConfidenceUpdatedData
 */
export function isConfidenceUpdate(event: WebSocketEvent): event is {
  type: "confidence_updated";
  stream_id: SkillStreamId;
  data: ConfidenceUpdatedData;
  timestamp: string;
} {
  return event.type === "confidence_updated";
}

/**
 * Type guard: check if event is ConfigChangedData
 */
export function isConfigUpdate(event: WebSocketEvent): event is {
  type: "config_changed";
  stream_id: SkillStreamId;
  data: ConfigChangedData;
  timestamp: string;
} {
  return event.type === "config_changed";
}

/**
 * Type guard: check if event is ErrorData
 */
export function isErrorEvent(event: WebSocketEvent): event is {
  type: "error";
  stream_id: SkillStreamId;
  data: ErrorData;
  timestamp: string;
} {
  return event.type === "error";
}

/**
 * Type guard: check if event is a skill update (not control message)
 */
export function isSkillUpdate(event: WebSocketEvent): event is
  | { type: "confidence_updated"; stream_id: SkillStreamId; data: ConfidenceUpdatedData; timestamp: string }
  | { type: "config_changed"; stream_id: SkillStreamId; data: ConfigChangedData; timestamp: string }
  | { type: "error"; stream_id: SkillStreamId; data: ErrorData; timestamp: string } {
  return ["confidence_updated", "config_changed", "error"].includes(event.type);
}
