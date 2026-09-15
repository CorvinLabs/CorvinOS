/**
 * Quality Dashboard Types (ADR-0731, ADR-0732, ADR-0733)
 * Specification-as-Loss-Landscape visualization
 */

export interface ConvergenceHistoryPoint {
  iteration: number;
  quality_score: number;
  loss: number;
  dod_score: number;
  hallucin_score: number;
  timestamp?: string;
}

export interface SpecConstraint {
  name: string;
  type: "critical_invariant" | "domain_fact" | "success_criterion";
  description: string;
  weight: number;
  version: number;
  source: string;
}

export interface QualityMetrics {
  task_id: string;
  task_type: string;
  task_size: "micro" | "medium" | "macro";
  status: "converged" | "exhausted" | "in_progress";
  quality_score: number;
  dod_score: number;
  hallucin_score: number;
  iteration: number;
  spec_version: number;
  convergence_history: ConvergenceHistoryPoint[];
  spec_constraints: SpecConstraint[];
  audit_events: AuditEvent[];
  last_updated: string;
}

export interface AuditEvent {
  event_type: string;
  timestamp: string;
  details: Record<string, any>;
  spec_version?: number;
}

export interface ExportMetricsRequest {
  task_id: string;
  format: "csv" | "json";
  include_audit: boolean;
  include_spec_history: boolean;
}
