/**
 * Admin Panel Data Models — Stream 1–3 + Unified Dashboard (ADR-2050, Phase 2)
 */

/**
 * Stream 1: Workflow Optimizer Admin Data
 */
export interface ConfidenceDataPoint {
  feedback_count: number;
  confidence_score: number; // 0–1 (P(correct agent))
  timestamp: string;
  trend: 'up' | 'down' | 'stable';
}

export interface RoutingDistribution {
  agent: 'haiku' | 'sonnet' | 'opus';
  percentage: number;
  task_count: number;
}

export interface LearnedWeight {
  task_type: string;
  haiku_probability: number;
  sonnet_probability: number;
  opus_probability: number;
}

export interface WorkflowOptimizerAdminData {
  confidence_timeline: ConfidenceDataPoint[];
  routing_distribution: RoutingDistribution[];
  learned_weights: LearnedWeight[];
  current_confidence: number; // Latest confidence score
  version: number; // Config version
  available_versions: Array<{ version: number; timestamp: string }>;
}

/**
 * Stream 2: Security Orchestrator Admin Data
 */
export interface ThreatEvent {
  incident_id: string;
  detected_at: string;
  threat_type: string; // SQL injection, XSS, CSRF, brute force, etc.
  confidence_score: number;
  policy_applied: string;
  false_positive: boolean;
  feedback?: string;
  severity: 'low' | 'medium' | 'high' | 'critical';
}

export interface FalsePositiveEntry {
  incident_id: string;
  threat_type: string;
  detected_at: string;
  justification: string;
  reversed_at: string;
}

export interface PolicyAuditEntry {
  action: string; // hardening applied, reverted, tuned
  threat_type: string;
  timestamp: string;
  change_delta: string; // What changed
  admin_reason: string;
}

export interface SecurityOrchestratorAdminData {
  threat_timeline: ThreatEvent[];
  false_positives: FalsePositiveEntry[];
  audit_trail: PolicyAuditEntry[];
  threat_counts: Record<string, number>; // per threat type
  true_positive_rate: number; // %
  false_positive_rate: number; // %
  p95_latency_ms: number;
  canary_rollout_percent: number; // 5%, 25%, 50%, 100%
}

/**
 * Stream 3: Flow Guard Admin Data
 */
export interface DataClassMetrics {
  data_class: string;
  safe_percentage: number; // P(safe)
  denied_count: number;
  allowed_count: number;
  confidence: number;
}

export interface PolicyMatrixCell {
  data_class: string;
  engine: string;
  safe_score: number; // 0–1
  color: 'green' | 'yellow' | 'red'; // confidence-based
  sample_flows: Array<{ flow_id: string; destination: string }>;
}

export interface ThresholdSetting {
  data_class: string;
  current_threshold: number; // 0–1
  recommended_threshold: number;
  confidence: number;
}

export interface OverrideRequest {
  request_id: string;
  data_class: string;
  engine: string;
  destination: string;
  justification: string;
  requested_at: string;
  status: 'pending' | 'approved' | 'denied';
  approved_by?: string;
  approved_at?: string;
  ttl_hours: number;
}

export interface FlowGuardAdminData {
  policy_matrix: PolicyMatrixCell[];
  classification_accuracy: DataClassMetrics[];
  thresholds: ThresholdSetting[];
  override_requests: OverrideRequest[];
  current_policy_version: number;
  available_policy_versions: Array<{ version: number; timestamp: string }>;
}

/**
 * Unified Dashboard Data
 */
export interface FeedbackVolumeDataPoint {
  date: string;
  workflow_optimizer: number;
  security_orchestrator: number;
  flow_guard: number;
}

export interface ConfidenceTrendDataPoint {
  date: string;
  workflow_optimizer: number; // 0–1
  security_orchestrator: number;
  flow_guard: number;
}

export interface LearningStatus {
  total_feedback_received: number;
  days_learning: number;
  total_skills: number;
  avg_confidence: number;
}

export interface UnifiedDashboardData {
  feedback_volume: FeedbackVolumeDataPoint[];
  confidence_trends: ConfidenceTrendDataPoint[];
  learning_status: LearningStatus;
  last_update: string;
  date_range: {
    start: string;
    end: string;
  };
}

/**
 * Admin Panel Response Wrappers
 */
export interface AdminPanelResponse<T> {
  data: T;
  timestamp: string;
  cache_key: string;
  stale_after_seconds: number;
}
