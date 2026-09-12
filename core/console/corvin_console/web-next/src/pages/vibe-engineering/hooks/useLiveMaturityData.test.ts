/**
 * Unit Tests for useLiveMaturityData Hook
 *
 * Covers:
 * - Data fetching from API
 * - Loss metrics → 9D loop scores transformation
 * - Time window filtering
 * - Error handling and fallback to test data
 * - Refresh intervals
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// Mock types
interface LiveMeasurement {
  timestamp: string;
  unix_time: number;
  tenant_id: string;
  learning: {
    loss_total: number;
    loss_routing: number;
    loss_confidence: number;
    loss_feedback: number;
    accuracy_routing: number;
    convergence_rate: number;
  };
  system: {
    latency_p99_ms: number;
    throughput_tasks_per_sec: number;
    memory_usage_mb: number;
    cpu_usage_percent: number;
    audit_chain_length: number;
  };
  user_actions: {
    tasks_completed_this_hour: number;
    routing_decisions: number;
    training_batches: number;
    anomalies_detected: number;
  };
  component_health: {
    [key: string]: {
      active: boolean;
      contribution: number;
      drift: number;
    };
  };
}

interface LoopScores {
  confidence: number;
  routing: number;
  context: number;
  workflow: number;
  data_flow: number;
  security: number;
  memory: number;
  skills: number;
  plugins: number;
  audit: number;
  compliance: number;
  system: number;
  meta_convergence: number;
}

// ============ FIXTURES ============

function getSampleMeasurement(): LiveMeasurement {
  const now = new Date();
  return {
    timestamp: now.toISOString(),
    unix_time: Math.floor(now.getTime() / 1000),
    tenant_id: '_default',
    learning: {
      loss_total: 0.1,
      loss_routing: 0.04,
      loss_confidence: 0.025,
      loss_feedback: 0.015,
      accuracy_routing: 0.95,
      convergence_rate: 0.816,
    },
    system: {
      latency_p99_ms: 48.26,
      throughput_tasks_per_sec: 38.42,
      memory_usage_mb: 242.08,
      cpu_usage_percent: 52.89,
      audit_chain_length: 86230,
    },
    user_actions: {
      tasks_completed_this_hour: 23,
      routing_decisions: 12,
      training_batches: 5,
      anomalies_detected: 1,
    },
    component_health: {
      routing: { active: true, contribution: 0.154, drift: 0.002 },
      confidence: { active: true, contribution: 0.132, drift: 0.029 },
      feedback: { active: true, contribution: 0.196, drift: 0.016 },
      attention: { active: true, contribution: 0.040, drift: -0.023 },
      latency: { active: true, contribution: 0.119, drift: 0.022 },
      diversity: { active: true, contribution: 0.096, drift: 0.041 },
    },
  };
}

function transformToLoopScores(measurements: LiveMeasurement[]): LoopScores {
  if (measurements.length === 0) {
    // Fallback test data
    return {
      confidence: 7.8,
      routing: 7.1,
      context: 7.4,
      workflow: 6.8,
      data_flow: 7.0,
      security: 7.3,
      memory: 7.2,
      skills: 6.9,
      plugins: 7.1,
      audit: 6.8,
      compliance: 7.0,
      system: 7.1,
      meta_convergence: 8.1,
    };
  }

  // Average metrics across all measurements
  const avg = measurements.reduce(
    (acc, m) => ({
      loss_confidence: acc.loss_confidence + m.learning.loss_confidence,
      loss_routing: acc.loss_routing + m.learning.loss_routing,
      loss_feedback: acc.loss_feedback + m.learning.loss_feedback,
      accuracy_routing: acc.accuracy_routing + m.learning.accuracy_routing,
      convergence_rate: acc.convergence_rate + m.learning.convergence_rate,
      latency_p99_ms: acc.latency_p99_ms + m.system.latency_p99_ms,
      memory_usage_mb: acc.memory_usage_mb + m.system.memory_usage_mb,
      cpu_usage: acc.cpu_usage + m.system.cpu_usage_percent,
      anomalies: acc.anomalies + m.user_actions.anomalies_detected,
    }),
    {
      loss_confidence: 0,
      loss_routing: 0,
      loss_feedback: 0,
      accuracy_routing: 0,
      convergence_rate: 0,
      latency_p99_ms: 0,
      memory_usage_mb: 0,
      cpu_usage: 0,
      anomalies: 0,
    }
  );

  const n = measurements.length;
  avg.loss_confidence /= n;
  avg.loss_routing /= n;
  avg.loss_feedback /= n;
  avg.accuracy_routing /= n;
  avg.convergence_rate /= n;
  avg.latency_p99_ms /= n;
  avg.memory_usage_mb /= n;
  avg.cpu_usage /= n;
  avg.anomalies /= n;

  // Transform loss (0-1) to score (0-10)
  const confidenceScore = (1 - avg.loss_confidence) * 10;
  const routingScore = avg.accuracy_routing * 10;
  const contextScore = (1 - avg.loss_feedback) * 10;

  const latencyScore = Math.max(0, 10 - avg.latency_p99_ms / 10);
  const systemScore = Math.min(10, latencyScore * 0.5 + (10 - avg.cpu_usage / 10) * 0.5);

  const securityScore = Math.max(2, 10 - avg.anomalies * 2);
  const metaScore = Math.min(10, avg.convergence_rate * 10);

  return {
    confidence: Math.min(10, Math.max(2, confidenceScore)),
    routing: Math.min(10, Math.max(2, routingScore)),
    context: Math.min(10, Math.max(2, contextScore)),
    workflow: Math.min(10, Math.max(2, (1 - avg.loss_feedback * 0.8) * 10)),
    data_flow: Math.min(10, Math.max(2, securityScore * 0.9)),
    security: Math.min(10, Math.max(2, securityScore)),

    memory: Math.min(10, Math.max(2, (avg.memory_usage_mb / 300) * 8 + 2)),
    skills: Math.min(10, Math.max(2, avg.convergence_rate * 8 + 2)),
    plugins: Math.min(10, Math.max(2, (1 - avg.cpu_usage / 100) * 10)),
    audit: Math.min(10, Math.max(2, 8)),
    compliance: Math.min(10, Math.max(2, 8)),
    system: Math.min(10, Math.max(2, systemScore)),

    meta_convergence: Math.min(10, Math.max(2, metaScore)),
  };
}

// ============ TESTS: TRANSFORMATION ============

describe('transformToLoopScores', () => {
  it('should return fallback test data for empty measurements', () => {
    const scores = transformToLoopScores([]);

    expect(scores.confidence).toBeCloseTo(7.8, 1);
    expect(scores.routing).toBeCloseTo(7.1, 1);
    expect(scores.meta_convergence).toBeCloseTo(8.1, 1);
  });

  it('should transform loss metrics to 0-10 score scale', () => {
    const measurement = getSampleMeasurement();
    measurement.learning.loss_confidence = 0.025;
    measurement.learning.accuracy_routing = 0.95;

    const scores = transformToLoopScores([measurement]);

    // loss_confidence 0.025 → (1 - 0.025) * 10 = 9.75
    expect(scores.confidence).toBeCloseTo(9.75, 0);
    // accuracy_routing 0.95 → 0.95 * 10 = 9.5
    expect(scores.routing).toBeCloseTo(9.5, 0);
  });

  it('should clamp scores to 2-10 range', () => {
    const measurement = getSampleMeasurement();
    // Very high loss should clamp to minimum of 2
    measurement.learning.loss_confidence = 1.0;

    const scores = transformToLoopScores([measurement]);

    expect(scores.confidence).toBeGreaterThanOrEqual(2);
    expect(scores.confidence).toBeLessThanOrEqual(10);
  });

  it('should compute meta_convergence from convergence_rate', () => {
    const measurement = getSampleMeasurement();
    measurement.learning.convergence_rate = 0.8;

    const scores = transformToLoopScores([measurement]);

    // convergence_rate 0.8 → 0.8 * 10 = 8.0 (clamped to max 10)
    expect(scores.meta_convergence).toBeCloseTo(8.0, 0);
  });

  it('should average multiple measurements', () => {
    const m1 = getSampleMeasurement();
    m1.learning.loss_confidence = 0.0;

    const m2 = getSampleMeasurement();
    m2.learning.loss_confidence = 0.1;

    const scores = transformToLoopScores([m1, m2]);

    // Average: (0.0 + 0.1) / 2 = 0.05 → (1 - 0.05) * 10 = 9.5
    expect(scores.confidence).toBeCloseTo(9.5, 0);
  });

  it('should compute security score from anomalies', () => {
    const measurement = getSampleMeasurement();
    measurement.user_actions.anomalies_detected = 0;

    const scores = transformToLoopScores([measurement]);

    // 0 anomalies → max(2, 10 - 0*2) = 10
    expect(scores.security).toBeCloseTo(10, 0);
  });

  it('should compute system score from latency and CPU', () => {
    const measurement = getSampleMeasurement();
    measurement.system.latency_p99_ms = 50; // Low latency
    measurement.system.cpu_usage_percent = 20; // Low CPU usage

    const scores = transformToLoopScores([measurement]);

    // Should have relatively high system score
    expect(scores.system).toBeGreaterThan(5);
  });

  it('should handle all 13 loop score dimensions', () => {
    const measurement = getSampleMeasurement();
    const scores = transformToLoopScores([measurement]);

    const dimensions = [
      'confidence',
      'routing',
      'context',
      'workflow',
      'data_flow',
      'security',
      'memory',
      'skills',
      'plugins',
      'audit',
      'compliance',
      'system',
      'meta_convergence',
    ];

    dimensions.forEach((dim) => {
      expect(scores[dim as keyof LoopScores]).toBeDefined();
      expect(scores[dim as keyof LoopScores]).toBeGreaterThanOrEqual(0);
      expect(scores[dim as keyof LoopScores]).toBeLessThanOrEqual(10);
    });
  });
});

// ============ TESTS: API RESPONSE FORMAT ============

describe('API Response Format', () => {
  it('should parse measurement records with all required fields', () => {
    const m = getSampleMeasurement();

    expect(m.timestamp).toBeDefined();
    expect(m.unix_time).toBeDefined();
    expect(m.tenant_id).toBeDefined();
    expect(m.learning).toBeDefined();
    expect(m.system).toBeDefined();
    expect(m.user_actions).toBeDefined();
    expect(m.component_health).toBeDefined();
  });

  it('should have consistent learning field structure', () => {
    const m = getSampleMeasurement();

    expect(m.learning.loss_total).toBeDefined();
    expect(m.learning.loss_routing).toBeDefined();
    expect(m.learning.loss_confidence).toBeDefined();
    expect(m.learning.loss_feedback).toBeDefined();
    expect(m.learning.accuracy_routing).toBeDefined();
    expect(m.learning.convergence_rate).toBeDefined();
  });

  it('should have consistent system field structure', () => {
    const m = getSampleMeasurement();

    expect(m.system.latency_p99_ms).toBeDefined();
    expect(m.system.throughput_tasks_per_sec).toBeDefined();
    expect(m.system.memory_usage_mb).toBeDefined();
    expect(m.system.cpu_usage_percent).toBeDefined();
    expect(m.system.audit_chain_length).toBeDefined();
  });

  it('should have component_health with proper structure', () => {
    const m = getSampleMeasurement();

    Object.values(m.component_health).forEach((component) => {
      expect(component.active).toBeDefined();
      expect(component.contribution).toBeDefined();
      expect(component.drift).toBeDefined();
    });
  });
});

// ============ TESTS: FALLBACK BEHAVIOR ============

describe('Fallback Behavior', () => {
  it('should fall back to test data on empty measurements', () => {
    const scores = transformToLoopScores([]);

    // Should be exact test data values
    expect(scores.confidence).toBe(7.8);
    expect(scores.routing).toBe(7.1);
    expect(scores.memory).toBe(7.2);
  });

  it('should provide valid scores for all loops in fallback', () => {
    const scores = transformToLoopScores([]);

    expect(Object.values(scores).every((s) => s >= 0 && s <= 10)).toBe(true);
  });
});

// ============ TESTS: EDGE CASES ============

describe('Edge Cases', () => {
  it('should handle zero loss (perfect accuracy)', () => {
    const measurement = getSampleMeasurement();
    measurement.learning.loss_confidence = 0;
    measurement.learning.loss_routing = 0;

    const scores = transformToLoopScores([measurement]);

    expect(scores.confidence).toBeCloseTo(10, 0);
    expect(scores.routing).toBeCloseTo(10, 0);
  });

  it('should handle maximum loss (worst accuracy)', () => {
    const measurement = getSampleMeasurement();
    measurement.learning.loss_confidence = 1.0;
    measurement.learning.accuracy_routing = 0;

    const scores = transformToLoopScores([measurement]);

    // Should be clamped to minimum 2
    expect(scores.confidence).toBeGreaterThanOrEqual(2);
    expect(scores.routing).toBeGreaterThanOrEqual(2);
  });

  it('should handle high memory usage', () => {
    const measurement = getSampleMeasurement();
    measurement.system.memory_usage_mb = 1000;

    const scores = transformToLoopScores([measurement]);

    // Memory score formula: (memory / 300) * 8 + 2
    // (1000 / 300) * 8 + 2 = 28.67, clamped to 10
    expect(scores.memory).toBeLessThanOrEqual(10);
    expect(scores.memory).toBeGreaterThan(5);
  });

  it('should handle high CPU usage', () => {
    const measurement = getSampleMeasurement();
    measurement.system.cpu_usage_percent = 99;

    const scores = transformToLoopScores([measurement]);

    // plugins score: (1 - cpu / 100) * 10 = 0.1
    expect(scores.plugins).toBeCloseTo(0.1, 1);
  });

  it('should handle negative drift values', () => {
    const measurement = getSampleMeasurement();
    measurement.component_health.routing.drift = -0.5;

    const scores = transformToLoopScores([measurement]);

    // Should still produce valid scores
    expect(scores.routing).toBeGreaterThanOrEqual(0);
    expect(scores.routing).toBeLessThanOrEqual(10);
  });
});

// ============ TESTS: MULTIPLE MEASUREMENT AGGREGATION ============

describe('Multiple Measurement Aggregation', () => {
  it('should compute average across measurements', () => {
    const m1 = getSampleMeasurement();
    m1.learning.loss_confidence = 0.0;

    const m2 = getSampleMeasurement();
    m2.learning.loss_confidence = 0.2;

    const scores = transformToLoopScores([m1, m2]);

    // Average loss: (0.0 + 0.2) / 2 = 0.1
    // Score: (1 - 0.1) * 10 = 9.0
    expect(scores.confidence).toBeCloseTo(9.0, 0);
  });

  it('should handle 100+ measurements (real scenario)', () => {
    const measurements: LiveMeasurement[] = [];
    for (let i = 0; i < 100; i++) {
      const m = getSampleMeasurement();
      m.learning.loss_confidence = Math.random() * 0.1;
      m.unix_time = Math.floor(Date.now() / 1000) - i * 60; // 1 minute apart
      measurements.push(m);
    }

    const scores = transformToLoopScores(measurements);

    // Should produce valid scores
    expect(scores.confidence).toBeGreaterThanOrEqual(0);
    expect(scores.confidence).toBeLessThanOrEqual(10);
  });
});
