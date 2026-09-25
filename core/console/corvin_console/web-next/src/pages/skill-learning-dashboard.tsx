import React, { useState, useEffect } from "react";

interface LearningMetrics {
  skill_id: string;
  version: string;
  total_executions: number;
  correct_outcomes: number;
  accuracy: number;
  avg_latency_ms: number;
  error_rate: number;
  avg_cost_usd: number;
  confidence_score: number;
  last_updated: string;
}

interface FeedbackItem {
  execution_id: string;
  outcome_correct: boolean;
  rating: number;
  notes: string;
  timestamp: string;
  latency_ms: number;
}

interface OptimizationProposal {
  proposal_id: string;
  skill_id: string;
  parameter_name: string;
  old_value: string;
  new_value: string;
  rationale: string;
  expected_improvement_pct: number;
  confidence: number;
  created_at: string;
  status: "pending" | "approved" | "rejected" | "applied";
}

export const SkillLearningDashboard: React.FC<{ skillId: string }> = ({
  skillId,
}) => {
  const [metrics, setMetrics] = useState<LearningMetrics | null>(null);
  const [feedback, setFeedback] = useState<FeedbackItem[]>([]);
  const [proposals, setProposals] = useState<OptimizationProposal[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const metricsRes = await fetch(
          `/v1/console/skills/${skillId}/learning`
        );
        const metricsData = await metricsRes.json();
        setMetrics(metricsData);

        const feedbackRes = await fetch(
          `/v1/console/skills/${skillId}/feedback/history?limit=20`
        );
        const feedbackData = await feedbackRes.json();
        setFeedback(feedbackData.recent);

        const proposalsRes = await fetch(
          `/v1/console/skills/${skillId}/optimization/proposals`
        );
        const proposalsData = await proposalsRes.json();
        setProposals(proposalsData.proposals);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, [skillId]);

  if (loading) return <div>Loading...</div>;
  if (!metrics) return <div>No data</div>;

  return (
    <div className="learning-dashboard">
      <h2>{metrics.skill_id} — Learning Dashboard</h2>

      {/* Metrics Cards */}
      <div className="metrics-grid">
        <div className="metric-card">
          <div className="metric-label">Accuracy</div>
          <div className="metric-value">{(metrics.accuracy * 100).toFixed(1)}%</div>
          <div className="metric-detail">
            {metrics.correct_outcomes} / {metrics.total_executions} correct
          </div>
        </div>

        <div className="metric-card">
          <div className="metric-label">Confidence Score</div>
          <div className="metric-value">{(metrics.confidence_score * 100).toFixed(0)}%</div>
          <div className="metric-detail">Skill reliability</div>
        </div>

        <div className="metric-card">
          <div className="metric-label">Avg Latency</div>
          <div className="metric-value">{metrics.avg_latency_ms.toFixed(1)}ms</div>
          <div className="metric-detail">Per execution</div>
        </div>

        <div className="metric-card">
          <div className="metric-label">Error Rate</div>
          <div className="metric-value">{(metrics.error_rate * 100).toFixed(1)}%</div>
          <div className="metric-detail">Failed executions</div>
        </div>
      </div>

      {/* Confidence Chart (Placeholder) */}
      <div className="chart-section">
        <h3>Confidence Trend</h3>
        <div className="chart-placeholder">
          [Chart: Confidence score over time — {metrics.confidence_score.toFixed(2)}]
        </div>
      </div>

      {/* Feedback Panel */}
      <div className="feedback-section">
        <h3>Recent Feedback ({feedback.length} items)</h3>
        <div className="feedback-list">
          {feedback.map((item) => (
            <div key={item.execution_id} className="feedback-item">
              <span className="icon">
                {item.outcome_correct ? "✅" : "❌"}
              </span>
              <span className="details">
                <strong>{item.notes}</strong> — {item.rating}★ (
                {item.latency_ms.toFixed(1)}ms)
              </span>
              <span className="time">{item.timestamp}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Optimization Proposals */}
      <div className="proposals-section">
        <h3>Optimization Proposals ({proposals.length})</h3>
        <div className="proposals-list">
          {proposals.map((p) => (
            <div key={p.proposal_id} className="proposal-card">
              <div className="proposal-header">
                <span className="param">{p.parameter_name}</span>
                <span className="confidence">{(p.confidence * 100).toFixed(0)}% confidence</span>
              </div>
              <div className="proposal-body">
                <p className="rationale">{p.rationale}</p>
                <div className="change">
                  {p.old_value} → {p.new_value} (expected +{p.expected_improvement_pct.toFixed(1)}%)
                </div>
              </div>
              <button className="approve-btn">Approve</button>
            </div>
          ))}
        </div>
      </div>

      <style>{`
        .learning-dashboard {
          padding: 20px;
          max-width: 1200px;
          margin: 0 auto;
        }

        .learning-dashboard h2 {
          font-size: 24px;
          font-weight: 600;
          margin-bottom: 20px;
        }

        .metrics-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
          gap: 16px;
          margin-bottom: 30px;
        }

        .metric-card {
          border: 1px solid #e0e0e0;
          border-radius: 8px;
          padding: 16px;
          background: #f9f9f9;
        }

        .metric-label {
          font-size: 12px;
          color: #666;
          text-transform: uppercase;
          margin-bottom: 8px;
        }

        .metric-value {
          font-size: 32px;
          font-weight: 700;
          color: #007bff;
          margin-bottom: 4px;
        }

        .metric-detail {
          font-size: 12px;
          color: #999;
        }

        .chart-section {
          border: 1px solid #e0e0e0;
          border-radius: 8px;
          padding: 20px;
          margin-bottom: 30px;
        }

        .chart-placeholder {
          background: #f5f5f5;
          padding: 40px;
          text-align: center;
          border-radius: 6px;
          color: #999;
          font-size: 14px;
        }

        .feedback-section,
        .proposals-section {
          border: 1px solid #e0e0e0;
          border-radius: 8px;
          padding: 20px;
          margin-bottom: 20px;
        }

        .feedback-section h3,
        .proposals-section h3 {
          margin: 0 0 16px 0;
          font-size: 16px;
          font-weight: 600;
        }

        .feedback-list {
          display: flex;
          flex-direction: column;
          gap: 12px;
        }

        .feedback-item {
          display: flex;
          align-items: center;
          gap: 12px;
          padding: 12px;
          background: #f9f9f9;
          border-radius: 6px;
          font-size: 13px;
        }

        .feedback-item .icon {
          font-size: 16px;
          width: 24px;
          text-align: center;
        }

        .feedback-item .details {
          flex: 1;
        }

        .feedback-item .time {
          color: #999;
          font-size: 11px;
        }

        .proposals-list {
          display: flex;
          flex-direction: column;
          gap: 12px;
        }

        .proposal-card {
          border: 1px solid #ddd;
          border-radius: 6px;
          padding: 14px;
          background: #ffffff;
        }

        .proposal-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 8px;
        }

        .param {
          font-weight: 600;
          color: #333;
        }

        .confidence {
          font-size: 12px;
          color: #666;
        }

        .rationale {
          margin: 8px 0;
          font-size: 13px;
          color: #555;
        }

        .change {
          font-size: 12px;
          color: #0056b3;
          font-family: monospace;
          margin-bottom: 10px;
        }

        .approve-btn {
          padding: 6px 12px;
          background: #28a745;
          color: white;
          border: none;
          border-radius: 4px;
          font-size: 12px;
          cursor: pointer;
        }

        .approve-btn:hover {
          background: #218838;
        }
      `}</style>
    </div>
  );
};

export default SkillLearningDashboard;
