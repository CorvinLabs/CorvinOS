/**
 * Test suite for Autonomous Skill Forge StatusDisplay Component
 * Focus on core rendering and display logic
 */

import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import StatusDisplay, { CanaryState } from '@/components/forge/StatusDisplay';

const healthyCanary: CanaryState = {
  skill_id: 'os.delegation_router',
  version: '1.2.4',
  status: 'canary',
  confidence: 0.85,
  latency_p95_ms: 145,
  error_rate: 2.5,
  traffic_percent: 10,
  time_remaining_sec: 2700,
  validation_passed: true,
};

const degradedCanary: CanaryState = {
  ...healthyCanary,
  error_rate: 7.5,
  latency_p95_ms: 1800,
  validation_passed: false,
};

const pausedCanary: CanaryState = {
  ...healthyCanary,
  status: 'paused',
};

describe('StatusDisplay Component', () => {
  it('renders null state message', () => {
    render(<StatusDisplay canaryState={null} />);
    expect(screen.getByText('No active canary')).toBeInTheDocument();
  });

  it('displays skill ID and version', () => {
    render(<StatusDisplay canaryState={healthyCanary} />);
    expect(screen.getByText('os.delegation_router')).toBeInTheDocument();
    expect(screen.getByText('1.2.4')).toBeInTheDocument();
  });

  it('shows active status badge', () => {
    render(<StatusDisplay canaryState={healthyCanary} />);
    expect(screen.getByText('Active')).toBeInTheDocument();
  });

  it('shows paused status badge', () => {
    render(<StatusDisplay canaryState={pausedCanary} />);
    expect(screen.getByText('paused')).toBeInTheDocument();
  });

  it('displays health indicators for healthy canary', () => {
    render(<StatusDisplay canaryState={healthyCanary} />);
    expect(screen.getByText('All validations passed')).toBeInTheDocument();
  });

  it('displays health indicators for degraded canary', () => {
    render(<StatusDisplay canaryState={degradedCanary} />);
    expect(screen.getByText('Validation issues detected')).toBeInTheDocument();
  });

  it('displays confidence as percentage', () => {
    render(<StatusDisplay canaryState={healthyCanary} />);
    expect(screen.getByText('85%')).toBeInTheDocument();
  });

  it('displays traffic as percentage', () => {
    render(<StatusDisplay canaryState={healthyCanary} />);
    expect(screen.getByText('10%')).toBeInTheDocument();
  });

  it('displays time remaining', () => {
    render(<StatusDisplay canaryState={healthyCanary} />);
    // 2700 seconds = 45 minutes
    expect(screen.getByText('45m')).toBeInTheDocument();
  });

  it('displays error rate', () => {
    render(<StatusDisplay canaryState={healthyCanary} />);
    expect(screen.getByText('2.50%')).toBeInTheDocument();
  });

  it('displays P95 latency', () => {
    render(<StatusDisplay canaryState={healthyCanary} />);
    expect(screen.getByText('145ms')).toBeInTheDocument();
  });

  it('shows loading state', () => {
    render(<StatusDisplay canaryState={null} loading={true} />);
    expect(screen.getByText('Loading canary state...')).toBeInTheDocument();
  });

  it('formats large latency values', () => {
    const slowCanary = { ...healthyCanary, latency_p95_ms: 2500 };
    render(<StatusDisplay canaryState={slowCanary} />);
    expect(screen.getByText('2500ms')).toBeInTheDocument();
  });

  it('formats high error rates', () => {
    const errorCanary = { ...healthyCanary, error_rate: 15.75 };
    render(<StatusDisplay canaryState={errorCanary} />);
    expect(screen.getByText('15.75%')).toBeInTheDocument();
  });

  it('handles zero confidence', () => {
    const zeroConfidenceCanary = { ...healthyCanary, confidence: 0 };
    render(<StatusDisplay canaryState={zeroConfidenceCanary} />);
    expect(screen.getByText('0%')).toBeInTheDocument();
  });

  it('handles 100% confidence', () => {
    const fullConfidenceCanary = { ...healthyCanary, confidence: 1.0 };
    render(<StatusDisplay canaryState={fullConfidenceCanary} />);
    expect(screen.getByText('100%')).toBeInTheDocument();
  });

  it('formats time under 1 minute as seconds', () => {
    const shortTimeCanary = { ...healthyCanary, time_remaining_sec: 30 };
    render(<StatusDisplay canaryState={shortTimeCanary} />);
    expect(screen.getByText('30s')).toBeInTheDocument();
  });

  it('formats time under 1 hour as minutes', () => {
    const midTimeCanary = { ...healthyCanary, time_remaining_sec: 1800 };
    render(<StatusDisplay canaryState={midTimeCanary} />);
    expect(screen.getByText('30m')).toBeInTheDocument();
  });

  it('formats time over 1 hour as hours', () => {
    const longTimeCanary = { ...healthyCanary, time_remaining_sec: 7200 };
    render(<StatusDisplay canaryState={longTimeCanary} />);
    expect(screen.getByText('2h')).toBeInTheDocument();
  });
});
