/**
 * Feedback Types — Unified feedback schema (ADR-2050)
 * Used by all 4 feedback form components
 */

export type FeedbackType =
  | "outcome_feedback"
  | "preference_feedback"
  | "confidence_score"
  | "metric_observed";

export type OutcomeChoice = "yes" | "no" | "other";
export type PreferenceChoice = "llm" | "deterministic" | "neither";
export type MetricName =
  | "latency_ms"
  | "accuracy_percent"
  | "cost_usd"
  | "error_rate"
  | "throughput";

// Request types matching backend validators
export interface OutcomeFeedbackRequest {
  feedback_type: "outcome_feedback";
  skill_id: string;
  outcome: OutcomeChoice;
  task_id?: string;
  reason?: string;
}

export interface PreferenceFeedbackRequest {
  feedback_type: "preference_feedback";
  skill_id: string;
  preference: PreferenceChoice;
  policy_class?: string;
  reason?: string;
}

export interface ConfidenceFeedbackRequest {
  feedback_type: "confidence_score";
  skill_id: string;
  confidence_score: number;
  task_id?: string;
  reason?: string;
}

export interface MetricFeedbackRequest {
  feedback_type: "metric_observed";
  skill_id: string;
  metric_name: MetricName;
  metric_value: number;
  dimension?: string;
}

// Response type
export interface FeedbackResponse {
  feedback_id: string;
  feedback_type: string;
  skill_id: string;
  timestamp: string;
  status: string;
  message: string;
}

// Error response
export interface FeedbackErrorResponse {
  error: string;
  detail: string;
  status_code: number;
}
