/**
 * Feedback Validators — Client-side validation matching backend (ADR-2050)
 * Fail-closed: reject suspicious input, never allow
 */

/**
 * PII patterns (must match backend validators)
 * Fail-closed: when in doubt, reject
 */
const PII_PATTERNS = [
  {
    pattern: /\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b/,
    name: 'email',
  },
  {
    pattern:
      /\b(?:\+?1[-.\s]?)?\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})\b/,
    name: 'phone',
  },
  {
    pattern: /\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b/,
    name: 'credit_card',
  },
  {
    pattern: /\b\d{3}-\d{2}-\d{4}\b/,
    name: 'ssn',
  },
  {
    pattern: /\b(?:sk_live_|sk_test_|pk_live_|pk_test_)[A-Za-z0-9]{20,}\b/,
    name: 'api_key',
  },
];

/**
 * Detect PII in text (fail-closed)
 */
export function detectPII(text: string | undefined | null): { hasPII: boolean; type?: string } {
  if (!text || typeof text !== 'string' || text.length === 0) {
    return { hasPII: false };
  }

  for (const { pattern, name } of PII_PATTERNS) {
    if (pattern.test(text)) {
      return { hasPII: true, type: name };
    }
  }

  return { hasPII: false };
}

/**
 * Validate skill ID (alphanumeric + underscore/dot)
 */
export function validateSkillId(skillId: string): { valid: boolean; error?: string } {
  if (!skillId || skillId.length < 3 || skillId.length > 100) {
    return { valid: false, error: 'Skill ID must be 3–100 characters' };
  }

  if (!/^[a-zA-Z0-9_.-]+$/.test(skillId)) {
    return { valid: false, error: 'Skill ID must be alphanumeric with underscore/dot' };
  }

  return { valid: true };
}

/**
 * Validate reason text (max 500 chars, no PII)
 */
export function validateReason(reason: string | undefined | null): {
  valid: boolean;
  error?: string;
} {
  if (!reason) {
    return { valid: true }; // optional field
  }

  if (reason.length > 500) {
    return { valid: false, error: 'Reason must be ≤500 characters' };
  }

  const pii = detectPII(reason);
  if (pii.hasPII) {
    return { valid: false, error: `Feedback contains ${pii.type}, not allowed` };
  }

  return { valid: true };
}

/**
 * Validate metric name (alphanumeric + underscore)
 */
export function validateMetricName(name: string): { valid: boolean; error?: string } {
  if (!name || name.length < 3 || name.length > 100) {
    return { valid: false, error: 'Metric name must be 3–100 characters' };
  }

  if (!/^[a-zA-Z0-9_]+$/.test(name)) {
    return { valid: false, error: 'Metric name must be alphanumeric + underscore' };
  }

  return { valid: true };
}

/**
 * Validate confidence score (0–100%)
 */
export function validateConfidenceScore(score: number | string): {
  valid: boolean;
  error?: string;
} {
  const num = typeof score === 'string' ? parseFloat(score) : score;

  if (isNaN(num)) {
    return { valid: false, error: 'Confidence must be a number' };
  }

  if (num < 0 || num > 100) {
    return { valid: false, error: 'Confidence must be 0–100%' };
  }

  return { valid: true };
}

/**
 * Validate metric value (numeric, reasonable bounds per metric type)
 */
export function validateMetricValue(
  name: string,
  value: number | string
): { valid: boolean; error?: string } {
  const num = typeof value === 'string' ? parseFloat(value) : value;

  if (isNaN(num)) {
    return { valid: false, error: 'Metric value must be a number' };
  }

  // Per-metric validation
  if (name === 'accuracy_percent' || name === 'error_rate') {
    if (num < 0 || num > 100) {
      return { valid: false, error: `${name} must be 0–100%` };
    }
  }

  if (name === 'latency_ms') {
    if (num < 0) {
      return { valid: false, error: 'Latency cannot be negative' };
    }
    if (num > 3600000) {
      // 1 hour
      return { valid: false, error: 'Latency seems unreasonably high (>1h)' };
    }
  }

  if (name === 'cost_usd') {
    if (num < 0) {
      return { valid: false, error: 'Cost cannot be negative' };
    }
    if (num > 100000) {
      // $100k per observation seems unreasonable
      return { valid: false, error: 'Cost seems unreasonably high (>$100k)' };
    }
  }

  if (name === 'throughput') {
    if (num < 0) {
      return { valid: false, error: 'Throughput cannot be negative' };
    }
  }

  return { valid: true };
}
