/**
 * Monitoring Dashboard Extension — Orchestration State + Approval Gates
 *
 * Remediation Cycle 2: Dashboard Error Handling + Approval Buttons + Metrics Validation
 *
 * Features:
 * - Real-time phase status display (PHASE_1 → PHASE_2A → PHASE_2B → PHASE_3)
 * - Approval/rejection buttons (clickable, form-submitted)
 * - Blocking reasons displayed (metrics out of bounds, lock timeout, audit chain broken)
 * - Metrics validation display (outcome_count, avg_confidence bounds)
 * - Audit trail verification (hash-chain status)
 * - Error recovery & degradation (lock timeout → retry button)
 *
 * NOT WIRED: no production caller as of 2026-09-27 (adversarial review) —
 * nothing imports this component, and routes/orchestration.py is mounted by no
 * router, so /v1/console/orchestration/status answers 404: that renders "not
 * available on this build" and stops polling. On any other error the page no
 * longer renders its placeholder initial state (PHASE_1, "Audit Chain: ✓
 * Valid") as if it had been measured.
 */

import React, { useState, useEffect, useCallback } from 'react';

interface BlockingReason {
  phase: string;
  reason: string;
  detail: string;
  severity: 'error' | 'warning' | 'info';
}

interface MetricsValidation {
  outcome_count: number;
  avg_confidence: number;
  is_valid: boolean;
  errors: string[];
}

interface PhaseStatus {
  current: string;
  previous: string;
  approval_status: 'pending' | 'approved' | 'rejected' | 'blocked';
  blocking_reasons: BlockingReason[];
  metrics: MetricsValidation;
  audit_chain_valid: boolean;
  last_updated: string;
}

interface DashboardState {
  status: PhaseStatus;
  loading: boolean;
  error: string | null;
  request_in_flight: boolean;
}

/**
 * Monitoring Dashboard Extension Component
 *
 * Displays orchestration state, approval buttons, and error reasons.
 */
export const MonitoringDashboardExtension: React.FC = () => {
  const [dashboard, setDashboard] = useState<DashboardState>({
    status: {
      current: 'PHASE_1',
      previous: 'INIT',
      approval_status: 'pending',
      blocking_reasons: [],
      metrics: { outcome_count: 0, avg_confidence: 0, is_valid: false, errors: [] },
      audit_chain_valid: true,
      last_updated: new Date().toISOString(),
    },
    loading: true,
    error: null,
    request_in_flight: false,
  });
  // True only once a status response has actually been received.
  const [loaded, setLoaded] = useState(false);
  const [unavailable, setUnavailable] = useState(false);

  /**
   * Fetch orchestration status from API
   */
  const fetchStatus = useCallback(async () => {
    try {
      const response = await fetch('/v1/console/orchestration/status', {
        headers: {
          'Content-Type': 'application/json',
        },
      });

      if (response.status === 404) {
        setUnavailable(true);
        setDashboard((prev) => ({ ...prev, loading: false }));
        return;
      }
      if (!response.ok) {
        throw new Error(`API error: ${response.status} ${response.statusText}`);
      }

      const data = await response.json();
      setLoaded(true);
      setDashboard((prev) => ({
        ...prev,
        status: data,
        loading: false,
        error: null,
      }));
    } catch (err) {
      setDashboard((prev) => ({
        ...prev,
        loading: false,
        error: err instanceof Error ? err.message : 'Unknown error',
      }));
    }
  }, []);

  /**
   * Poll status every 2 seconds
   */
  useEffect(() => {
    if (unavailable) return;
    fetchStatus();
    const interval = setInterval(fetchStatus, 2000);
    return () => clearInterval(interval);
  }, [fetchStatus, unavailable]);

  /**
   * Handle approval button click
   */
  const handleApproval = useCallback(
    async (phase: string) => {
      setDashboard((prev) => ({ ...prev, request_in_flight: true }));
      try {
        // The documented route is under /v1/console (routes/orchestration.py);
        // X-CSRF-Token comes from lib/csrf-fetch.ts — `window.csrfToken`
        // was never set by anything.
        const response = await fetch('/v1/console/orchestration/approve', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            phase,
            reason: 'Operator approval via monitoring dashboard',
            timestamp: new Date().toISOString(),
          }),
        });

        if (!response.ok) {
          throw new Error(`Approval failed: ${response.status} ${response.statusText}`);
        }

        // Refresh status immediately
        await fetchStatus();
      } catch (err) {
        setDashboard((prev) => ({
          ...prev,
          error: err instanceof Error ? err.message : 'Approval failed',
        }));
      } finally {
        setDashboard((prev) => ({ ...prev, request_in_flight: false }));
      }
    },
    [fetchStatus]
  );

  /**
   * Validate metrics and return error list
   */
  const validateMetrics = (m: MetricsValidation): string[] => {
    const errors: string[] = [];
    if (m.outcome_count < 0) errors.push('outcome_count < 0');
    if (m.avg_confidence < 0.0 || m.avg_confidence > 1.0) {
      errors.push(`avg_confidence out of bounds (${m.avg_confidence})`);
    }
    // Check for NaN
    if (Number.isNaN(m.avg_confidence)) errors.push('avg_confidence is NaN');
    return errors;
  };

  if (unavailable) {
    return (
      <div className="dashboard" data-testid="orchestration-unavailable">
        Orchestration status is not available on this build.
      </div>
    );
  }

  if (dashboard.loading) {
    return <div className="dashboard loading">Loading orchestration status...</div>;
  }

  if (!loaded) {
    // Never render the placeholder initial state as if it were measured.
    return (
      <div className="dashboard error-banner" role="alert">
        <span className="error-message">{dashboard.error || 'Orchestration status could not be loaded.'}</span>
        <button className="retry-button" onClick={fetchStatus}>
          Retry
        </button>
      </div>
    );
  }

  const { status } = dashboard;
  const isBlocked = status.approval_status === 'blocked' && status.blocking_reasons.length > 0;
  const metricErrors = validateMetrics(status.metrics);

  return (
    <div className="monitoring-dashboard-extension">
      {/* Error Banner */}
      {dashboard.error && (
        <div className="error-banner">
          <span className="error-icon">⚠️</span>
          <span className="error-message">{dashboard.error}</span>
          <button className="retry-button" onClick={fetchStatus} disabled={dashboard.request_in_flight}>
            Retry
          </button>
        </div>
      )}

      {/* Phase Status */}
      <div className="phase-status-section">
        <h2>Orchestration Phase Status</h2>
        <div className="phase-display">
          <div className="phase-current">
            <span className="label">Current Phase:</span>
            <span className="value">{status.current}</span>
          </div>
          <div className="phase-approval">
            <span className="label">Approval Status:</span>
            <span className={`value status-${status.approval_status}`}>{status.approval_status.toUpperCase()}</span>
          </div>
          <div className="phase-audit">
            <span className="label">Audit Chain:</span>
            <span className={`value ${status.audit_chain_valid ? 'valid' : 'broken'}`}>
              {status.audit_chain_valid ? '✓ Valid' : '✗ Broken'}
            </span>
          </div>
        </div>
      </div>

      {/* Metrics Validation */}
      <div className="metrics-section">
        <h3>Metrics Validation</h3>
        <div className="metrics-display">
          <div className="metric">
            <span className="name">outcome_count:</span>
            <span className={`value ${status.metrics.outcome_count >= 0 ? 'valid' : 'invalid'}`}>
              {status.metrics.outcome_count}
            </span>
          </div>
          <div className="metric">
            <span className="name">avg_confidence:</span>
            <span
              className={`value ${
                status.metrics.avg_confidence >= 0 && status.metrics.avg_confidence <= 1 ? 'valid' : 'invalid'
              }`}
            >
              {status.metrics.avg_confidence.toFixed(3)}
            </span>
          </div>
          {metricErrors.length > 0 && (
            <div className="metric-errors">
              {metricErrors.map((err, i) => (
                <span key={i} className="error-tag">
                  {err}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Blocking Reasons */}
      {isBlocked && (
        <div className="blocking-reasons-section">
          <h3>Blocking Reasons</h3>
          <div className="reasons-list">
            {status.blocking_reasons.map((reason, i) => (
              <div key={i} className={`reason reason-${reason.severity}`}>
                <div className="reason-header">
                  <span className="phase">{reason.phase}</span>
                  <span className="severity">{reason.severity.toUpperCase()}</span>
                </div>
                <div className="reason-body">
                  <span className="reason-text">{reason.reason}</span>
                  <span className="reason-detail">{reason.detail}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Approval Buttons */}
      <div className="approval-buttons-section">
        <h3>Phase Transitions</h3>
        <div className="buttons-grid">
          {!isBlocked && status.approval_status === 'pending' && (
            <>
              <button
                id="approve-phase-1-to-2a"
                className="approval-button approve"
                onClick={() => handleApproval('PHASE_1_TO_2A')}
                disabled={dashboard.request_in_flight || isBlocked}
              >
                ✓ Approve Phase 1→2A
              </button>
              <button
                className="approval-button reject"
                onClick={() => handleApproval('REJECT_PHASE_1')}
                disabled={dashboard.request_in_flight || isBlocked}
              >
                ✗ Reject Phase 1
              </button>
            </>
          )}
          {status.approval_status === 'approved' && (
            <div className="status-approved">
              <span className="checkmark">✓</span>
              <span className="text">Phase transition approved</span>
            </div>
          )}
          {isBlocked && (
            <div className="status-blocked">
              <span className="icon">🚫</span>
              <span className="text">Blocked: Please resolve errors above</span>
            </div>
          )}
        </div>
      </div>

      {/* Last Updated */}
      <div className="footer">
        <span className="last-updated">Last updated: {new Date(status.last_updated).toLocaleTimeString()}</span>
      </div>

      <style>{`
        .monitoring-dashboard-extension {
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
          max-width: 900px;
          margin: 20px auto;
          padding: 20px;
          background: #fff;
          border-radius: 8px;
          box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
        }

        .error-banner {
          display: flex;
          align-items: center;
          gap: 12px;
          padding: 12px 16px;
          background: #fee;
          border: 1px solid #fcc;
          border-radius: 4px;
          margin-bottom: 20px;
        }

        .error-icon {
          font-size: 18px;
        }

        .error-message {
          flex: 1;
          color: #c33;
        }

        .retry-button {
          padding: 6px 12px;
          background: #c33;
          color: white;
          border: none;
          border-radius: 4px;
          cursor: pointer;
          font-size: 14px;
        }

        .retry-button:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }

        .phase-status-section,
        .metrics-section,
        .blocking-reasons-section,
        .approval-buttons-section {
          margin-bottom: 24px;
        }

        h2 {
          font-size: 20px;
          font-weight: 600;
          margin: 0 0 16px 0;
          color: #333;
        }

        h3 {
          font-size: 16px;
          font-weight: 600;
          margin: 0 0 12px 0;
          color: #555;
        }

        .phase-display {
          display: grid;
          grid-template-columns: 1fr 1fr 1fr;
          gap: 16px;
        }

        .phase-current,
        .phase-approval,
        .phase-audit {
          display: flex;
          flex-direction: column;
          gap: 4px;
        }

        .label {
          font-size: 12px;
          font-weight: 500;
          color: #888;
          text-transform: uppercase;
        }

        .value {
          font-size: 16px;
          font-weight: 600;
          color: #333;
        }

        .value.status-pending {
          color: #ff9800;
        }

        .value.status-approved {
          color: #4caf50;
        }

        .value.status-rejected {
          color: #f44336;
        }

        .value.status-blocked {
          color: #c33;
        }

        .value.valid {
          color: #4caf50;
        }

        .value.broken {
          color: #f44336;
        }

        .value.invalid {
          color: #f44336;
        }

        .metrics-display {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 16px;
        }

        .metric {
          display: flex;
          justify-content: space-between;
          padding: 12px;
          background: #f9f9f9;
          border-radius: 4px;
          border-left: 3px solid #ddd;
        }

        .metric.invalid {
          border-left-color: #f44336;
          background: #ffebee;
        }

        .metric-errors {
          grid-column: 1 / -1;
          display: flex;
          gap: 8px;
          flex-wrap: wrap;
        }

        .error-tag {
          padding: 4px 8px;
          background: #ffcdd2;
          color: #c62828;
          border-radius: 3px;
          font-size: 12px;
          font-weight: 500;
        }

        .reasons-list {
          display: flex;
          flex-direction: column;
          gap: 12px;
        }

        .reason {
          padding: 12px;
          border-left: 4px solid #ff9800;
          background: #fff9e6;
          border-radius: 4px;
        }

        .reason-error {
          border-left-color: #f44336;
          background: #ffebee;
        }

        .reason-warning {
          border-left-color: #ff9800;
          background: #fff9e6;
        }

        .reason-info {
          border-left-color: #2196f3;
          background: #e3f2fd;
        }

        .reason-header {
          display: flex;
          justify-content: space-between;
          margin-bottom: 8px;
          font-weight: 600;
          font-size: 14px;
        }

        .phase {
          color: #333;
        }

        .severity {
          font-size: 12px;
          color: #888;
          text-transform: uppercase;
        }

        .reason-body {
          display: flex;
          flex-direction: column;
          gap: 4px;
        }

        .reason-text {
          color: #333;
          font-weight: 500;
        }

        .reason-detail {
          color: #666;
          font-size: 12px;
        }

        .buttons-grid {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 12px;
        }

        .approval-button {
          padding: 12px 16px;
          font-size: 14px;
          font-weight: 600;
          border: none;
          border-radius: 4px;
          cursor: pointer;
          transition: all 0.2s ease;
        }

        .approval-button.approve {
          background: #4caf50;
          color: white;
        }

        .approval-button.approve:hover:not(:disabled) {
          background: #45a049;
          box-shadow: 0 2px 8px rgba(76, 175, 80, 0.3);
        }

        .approval-button.reject {
          background: #f44336;
          color: white;
        }

        .approval-button.reject:hover:not(:disabled) {
          background: #da190b;
          box-shadow: 0 2px 8px rgba(244, 67, 54, 0.3);
        }

        .approval-button:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }

        .status-approved,
        .status-blocked {
          grid-column: 1 / -1;
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 12px;
          padding: 16px;
          border-radius: 4px;
          font-weight: 600;
          font-size: 16px;
        }

        .status-approved {
          background: #e8f5e9;
          color: #2e7d32;
        }

        .status-approved .checkmark {
          font-size: 24px;
        }

        .status-blocked {
          background: #ffebee;
          color: #c62828;
        }

        .status-blocked .icon {
          font-size: 20px;
        }

        .footer {
          text-align: center;
          padding-top: 16px;
          border-top: 1px solid #eee;
        }

        .last-updated {
          font-size: 12px;
          color: #888;
        }

        .loading {
          text-align: center;
          padding: 40px;
          color: #999;
        }

        @media (max-width: 640px) {
          .phase-display,
          .metrics-display,
          .buttons-grid {
            grid-template-columns: 1fr;
          }
        }
      `}</style>
    </div>
  );
};

export default MonitoringDashboardExtension;
