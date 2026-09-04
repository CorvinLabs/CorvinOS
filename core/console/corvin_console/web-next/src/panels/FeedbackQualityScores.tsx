/**
 * Feedback Quality Scores Panel
 *
 * Measures operator feedback quality and impact:
 * - Operator reliability (% of decisions that don't revoke)
 * - Feedback signal strength (impact on learning)
 * - Consistency scores
 *
 * ADR-0317: Outcome Feedback
 */

import React, { useState, useMemo } from 'react';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, LineChart, Line } from 'recharts';
import {
  Target,
  TrendingUp,
  Users,
  Award,
} from 'lucide-react';

interface OperatorFeedback {
  operator_id: string;
  operator_name: string;
  approval_count: number;
  rejection_count: number;
  accuracy: number; // 0-1 (% of approvals that don't revoke)
  consistency_score: number; // 0-1 (how consistent are decisions)
  learning_impact: number; // 0-1 (how much does system learn from this operator)
  average_decision_time_ms: number;
  skill_reliability: Record<string, number>; // skill -> accuracy on that skill
}

interface FeedbackQualityScoresProps {
  operators?: OperatorFeedback[];
  selectedOperator?: string;
  onSelectOperator?: (id: string) => void;
}

const FeedbackQualityScoresPanel: React.FC<FeedbackQualityScoresProps> = ({
  operators = [],
  selectedOperator = '',
  onSelectOperator = () => {},
}) => {
  const [selectedOp, setSelectedOp] = useState(selectedOperator || (operators.length > 0 ? operators[0].operator_id : ''));

  const selected = useMemo(
    () => operators.find((op) => op.operator_id === selectedOp) || operators[0],
    [selectedOp, operators]
  );

  const teamStats = useMemo(() => {
    if (operators.length === 0) return null;

    const avgAccuracy = operators.reduce((sum, op) => sum + op.accuracy, 0) / operators.length;
    const avgConsistency = operators.reduce((sum, op) => sum + op.consistency_score, 0) / operators.length;
    const avgLearningImpact = operators.reduce((sum, op) => sum + op.learning_impact, 0) / operators.length;
    const topOperator = operators.reduce((max, op) => (op.accuracy > max.accuracy ? op : max));

    return {
      avgAccuracy,
      avgConsistency,
      avgLearningImpact,
      topOperator,
    };
  }, [operators]);

  const getRatingColor = (score: number) => {
    if (score >= 0.95) return 'text-green-700';
    if (score >= 0.9) return 'text-green-600';
    if (score >= 0.8) return 'text-blue-600';
    if (score >= 0.7) return 'text-yellow-600';
    return 'text-red-600';
  };

  if (operators.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex gap-2 items-center">
            <Award className="h-5 w-5" />
            Feedback Quality Scores
          </CardTitle>
          <CardDescription>Operator reliability and decision impact</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="text-center py-8 text-gray-500 text-sm">
            No operator feedback data yet
          </div>
        </CardContent>
      </Card>
    );
  }

  const chartData = operators.map((op) => ({
    name: op.operator_name,
    accuracy: op.accuracy * 100,
    consistency: op.consistency_score * 100,
    impact: op.learning_impact * 100,
  }));

  const selectedSkills = selected
    ? Object.entries(selected.skill_reliability).map(([skill, accuracy]) => ({
        skill,
        accuracy: accuracy * 100,
      }))
    : [];

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex gap-2 items-center">
            <Users className="h-5 w-5" />
            Team Feedback Quality Overview
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="grid grid-cols-3 gap-4">
            <div className="bg-green-50 p-4 rounded border">
              <p className="text-xs text-gray-600">Team Accuracy</p>
              <p className={`text-2xl font-bold ${getRatingColor(teamStats?.avgAccuracy || 0)}`}>
                {((teamStats?.avgAccuracy || 0) * 100).toFixed(1)}%
              </p>
              <p className="text-xs text-gray-500 mt-1">Target: >97%</p>
            </div>
            <div className="bg-blue-50 p-4 rounded border">
              <p className="text-xs text-gray-600">Avg Consistency</p>
              <p className={`text-2xl font-bold ${getRatingColor(teamStats?.avgConsistency || 0)}`}>
                {((teamStats?.avgConsistency || 0) * 100).toFixed(1)}%
              </p>
              <p className="text-xs text-gray-500 mt-1">Decision uniformity</p>
            </div>
            <div className="bg-purple-50 p-4 rounded border">
              <p className="text-xs text-gray-600">Learning Impact</p>
              <p className={`text-2xl font-bold ${getRatingColor(teamStats?.avgLearningImpact || 0)}`}>
                {((teamStats?.avgLearningImpact || 0) * 100).toFixed(1)}%
              </p>
              <p className="text-xs text-gray-500 mt-1">Feedback quality</p>
            </div>
          </div>

          {teamStats?.topOperator && (
            <div className="bg-yellow-50 border border-yellow-200 p-4 rounded">
              <div className="flex gap-2 items-start">
                <Award className="h-5 w-5 text-yellow-700 flex-shrink-0 mt-0.5" />
                <div>
                  <p className="font-semibold text-yellow-900 text-sm">Top Operator</p>
                  <p className="text-sm text-yellow-800">
                    {teamStats.topOperator.operator_name} ({(teamStats.topOperator.accuracy * 100).toFixed(1)}% accuracy)
                  </p>
                </div>
              </div>
            </div>
          )}

          <div className="bg-gray-50 p-4 rounded border">
            <p className="font-semibold text-sm mb-3">Accuracy by Operator</p>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={chartData}>
                <XAxis dataKey="name" angle={-45} textAnchor="end" height={80} />
                <YAxis domain={[0, 100]} />
                <Tooltip formatter={(v) => `${v.toFixed(1)}%`} />
                <Legend />
                <Bar dataKey="accuracy" fill="#10b981" />
                <Bar dataKey="consistency" fill="#3b82f6" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </CardContent>
      </Card>

      {selected && (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg flex gap-2 items-center">
              <Target className="h-5 w-5" />
              {selected.operator_name}
            </CardTitle>
            <CardDescription>Individual feedback quality metrics</CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            <div className="grid grid-cols-4 gap-3">
              <div className="bg-blue-50 p-3 rounded border">
                <p className="text-xs text-gray-600">Approvals</p>
                <p className="text-xl font-bold">{selected.approval_count}</p>
              </div>
              <div className="bg-red-50 p-3 rounded border">
                <p className="text-xs text-gray-600">Rejections</p>
                <p className="text-xl font-bold">{selected.rejection_count}</p>
              </div>
              <div className="bg-green-50 p-3 rounded border">
                <p className="text-xs text-gray-600">Accuracy</p>
                <p className={`text-xl font-bold ${getRatingColor(selected.accuracy)}`}>
                  {(selected.accuracy * 100).toFixed(1)}%
                </p>
              </div>
              <div className="bg-purple-50 p-3 rounded border">
                <p className="text-xs text-gray-600">Decision Time</p>
                <p className="text-xl font-bold">{(selected.average_decision_time_ms / 1000).toFixed(1)}s</p>
              </div>
            </div>

            <div>
              <p className="font-semibold text-sm mb-2">Accuracy by Skill</p>
              {selectedSkills.length > 0 ? (
                <ResponsiveContainer width="100%" height={250}>
                  <BarChart data={selectedSkills}>
                    <XAxis dataKey="skill" angle={-45} textAnchor="end" height={60} interval={0} tick={{ fontSize: 12 }} />
                    <YAxis domain={[0, 100]} />
                    <Tooltip formatter={(v) => `${v.toFixed(1)}%`} />
                    <Bar dataKey="accuracy" fill="#3b82f6" />
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <p className="text-sm text-gray-500">No skill-specific data yet</p>
              )}
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="bg-gray-50 p-4 rounded border">
                <p className="text-xs text-gray-600 font-semibold">Consistency Score</p>
                <p className={`text-2xl font-bold ${getRatingColor(selected.consistency_score)} mt-2`}>
                  {(selected.consistency_score * 100).toFixed(0)}%
                </p>
                <p className="text-xs text-gray-600 mt-1">How consistent are your decisions?</p>
              </div>
              <div className="bg-gray-50 p-4 rounded border">
                <p className="text-xs text-gray-600 font-semibold">Learning Impact</p>
                <p className={`text-2xl font-bold ${getRatingColor(selected.learning_impact)} mt-2`}>
                  {(selected.learning_impact * 100).toFixed(0)}%
                </p>
                <p className="text-xs text-gray-600 mt-1">How much system learns</p>
              </div>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
};

export default FeedbackQualityScoresPanel;
