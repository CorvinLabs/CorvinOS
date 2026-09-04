/**
 * Concept Drift Alert Panel
 *
 * Detects when Skill confidence is diverging from expected distribution (K-L divergence).
 * Shows:
 * - Drift score over time
 * - Affected Skill metrics
 * - Recovery options (reset learning, retrain, manual tune)
 *
 * ADR-0315: Confidence Intervals
 */

import React, { useState, useEffect } from 'react';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { AlertTriangle, TrendingDown, RefreshCw, Zap } from 'lucide-react';

interface ConceptDriftAlert {
  skill_id: string;
  drift_score: number; // 0-1, where 0.5+ indicates drift
  kl_divergence: number;
  affected_metrics: string[];
  detected_at: string;
  confidence_before: number;
  confidence_after: number;
  recovery_status: 'none' | 'in_progress' | 'complete';
}

interface ConceptDriftAlertPanelProps {
  alerts?: ConceptDriftAlert[];
  onReset?: (skillId: string) => void;
}

const ConceptDriftAlertPanel: React.FC<ConceptDriftAlertPanelProps> = ({
  alerts = [],
  onReset = () => {},
}) => {
  const [selectedAlert, setSelectedAlert] = useState<ConceptDriftAlert | null>(
    alerts.length > 0 ? alerts[0] : null
  );
  const [recoveryInProgress, setRecoveryInProgress] = useState(false);

  const handleResetLearning = async () => {
    if (!selectedAlert) return;

    setRecoveryInProgress(true);
    try {
      // Call reset endpoint
      await fetch(`/v1/learning/skills/${selectedAlert.skill_id}/reset`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });

      // Callback
      onReset(selectedAlert.skill_id);
    } finally {
      setRecoveryInProgress(false);
    }
  };

  const getDriftSeverity = (score: number) => {
    if (score >= 0.7) return { level: 'CRITICAL', color: 'text-red-700', bg: 'bg-red-50' };
    if (score >= 0.5) return { level: 'HIGH', color: 'text-orange-700', bg: 'bg-orange-50' };
    if (score >= 0.3) return { level: 'MEDIUM', color: 'text-yellow-700', bg: 'bg-yellow-50' };
    return { level: 'LOW', color: 'text-blue-700', bg: 'bg-blue-50' };
  };

  if (alerts.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex gap-2 items-center">
            <Zap className="h-5 w-5" />
            Concept Drift Detection
          </CardTitle>
          <CardDescription>
            Monitors Skill confidence divergence (K-L divergence metric)
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="text-center py-8">
            <div className="text-gray-500 text-sm">No concept drift detected</div>
            <p className="text-xs text-gray-400 mt-2">
              All Skills are converging normally. Confidence is stable.
            </p>
          </div>
        </CardContent>
      </Card>
    );
  }

  const severity = selectedAlert ? getDriftSeverity(selectedAlert.drift_score) : null;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex gap-2 items-center">
            <AlertTriangle className="h-5 w-5 text-orange-600" />
            Concept Drift Detected
          </CardTitle>
          <CardDescription>
            {alerts.length} Skill{alerts.length !== 1 ? 's' : ''} showing confidence divergence
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            {alerts.map((alert) => (
              <button
                key={alert.skill_id}
                onClick={() => setSelectedAlert(alert)}
                className={`w-full text-left p-3 rounded border-2 transition ${
                  selectedAlert?.skill_id === alert.skill_id
                    ? 'border-blue-500 bg-blue-50'
                    : 'border-gray-200 hover:border-gray-300'
                }`}
              >
                <div className="flex justify-between items-start">
                  <div>
                    <p className="font-semibold text-sm">{alert.skill_id}</p>
                    <p className="text-xs text-gray-600">
                      KL Divergence: {alert.kl_divergence.toFixed(3)}
                    </p>
                  </div>
                  <div className={`text-xs font-bold ${getDriftSeverity(alert.drift_score).color}`}>
                    {getDriftSeverity(alert.drift_score).level}
                  </div>
                </div>
              </button>
            ))}
          </div>
        </CardContent>
      </Card>

      {selectedAlert && severity && (
        <Card className={severity.bg}>
          <CardHeader>
            <CardTitle className="text-lg">{selectedAlert.skill_id}</CardTitle>
            <CardDescription>
              Drift Score: {(selectedAlert.drift_score * 100).toFixed(1)}%
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="text-xs text-gray-600">Confidence Before</p>
                <p className="text-2xl font-bold">{(selectedAlert.confidence_before * 100).toFixed(0)}%</p>
              </div>
              <div>
                <p className="text-xs text-gray-600">Confidence After</p>
                <p className="text-2xl font-bold text-red-600">
                  {(selectedAlert.confidence_after * 100).toFixed(0)}%
                </p>
              </div>
            </div>

            <div>
              <p className="text-sm font-semibold mb-2">Affected Metrics</p>
              <div className="flex flex-wrap gap-2">
                {selectedAlert.affected_metrics.map((metric) => (
                  <div key={metric} className="bg-white px-2 py-1 rounded text-xs border">
                    {metric}
                  </div>
                ))}
              </div>
            </div>

            <div className="bg-white p-4 rounded border">
              <p className="text-sm font-semibold mb-2">Recovery Options</p>
              <div className="space-y-2">
                <Button
                  onClick={handleResetLearning}
                  disabled={recoveryInProgress}
                  className="w-full gap-2"
                >
                  <RefreshCw className="h-4 w-4" />
                  {recoveryInProgress ? 'Resetting...' : 'Reset Learning'}
                </Button>
                <p className="text-xs text-gray-600">
                  Clears historical confidence data and retrains from recent feedback.
                </p>
              </div>
            </div>

            <div className="bg-blue-50 border border-blue-200 p-4 rounded">
              <p className="text-xs font-semibold text-blue-900 mb-1">What is Concept Drift?</p>
              <p className="text-xs text-blue-800">
                Concept drift occurs when the underlying data distribution changes (e.g., new
                environment, changed workload). Your Skill's learned patterns no longer apply,
                confidence diverges from reality.
              </p>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
};

export default ConceptDriftAlertPanel;
