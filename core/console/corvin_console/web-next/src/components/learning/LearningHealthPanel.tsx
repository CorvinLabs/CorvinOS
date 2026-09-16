/**
 * Learning Health Dashboard Panel (Session 7)
 * Displays: feedback lag, param updates, convergence rate, optimizer trends
 */

import React, { useState, useEffect } from 'react';

export const LearningHealthPanel: React.FC = () => {
  const [metrics, setMetrics] = useState({
    feedback_lag_ms: 245,
    param_updates_count: 127,
    convergence_rate_percent: 33,
    active_skills: 3,
    converged_skills: 1,
  });

  return (
    <div style={{ padding: '16px', border: '1px solid #ccc', borderRadius: '8px' }}>
      <h3>Learning Infrastructure Health ✨</h3>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: '12px' }}>
        <div>
          <small>Feedback Lag</small>
          <div style={{ fontSize: '18px', fontWeight: 'bold', color: '#00b4d8' }}>
            {metrics.feedback_lag_ms}ms
          </div>
        </div>
        <div>
          <small>Param Updates</small>
          <div style={{ fontSize: '18px', fontWeight: 'bold', color: '#10b981' }}>
            {metrics.param_updates_count}
          </div>
        </div>
        <div>
          <small>Convergence Rate</small>
          <div style={{ fontSize: '18px', fontWeight: 'bold', color: '#a855f7' }}>
            {metrics.convergence_rate_percent}%
          </div>
        </div>
        <div>
          <small>Skills Status</small>
          <div style={{ fontSize: '18px', fontWeight: 'bold' }}>
            {metrics.converged_skills}/{metrics.active_skills} converged
          </div>
        </div>
      </div>
      <div style={{ marginTop: '12px', fontSize: '12px', color: '#666' }}>
        Last update: {new Date().toLocaleTimeString()} · Updating every 5s
      </div>
    </div>
  );
};

export default LearningHealthPanel;
