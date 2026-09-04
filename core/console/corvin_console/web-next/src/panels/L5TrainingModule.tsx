/**
 * L5 Interactive Training Module
 *
 * Guided 8-step tutorial for new L5 operators.
 * Topics: Overview, 5-gate system, decision making, monitoring, real example, troubleshooting.
 *
 * Includes:
 * - Step-by-step instructions
 * - Interactive quizzes
 * - Real-time examples
 * - Progress tracking
 * - Completion certificate
 */

import React, { useState } from 'react';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/components/ui/tabs';
import {
  AlertCircle,
  CheckCircle2,
  ChevronRight,
  Clock,
  Target,
  Zap,
} from 'lucide-react';

interface TrainingStep {
  id: number;
  title: string;
  description: string;
  content: React.ReactNode;
  quiz?: {
    question: string;
    options: Array<{ text: string; correct: boolean; explanation: string }>;
  };
  estimatedTime: number; // minutes
}

const L5TrainingModule: React.FC = () => {
  const [currentStep, setCurrentStep] = useState(0);
  const [completedSteps, setCompletedSteps] = useState<Set<number>>(new Set());
  const [quizAnswers, setQuizAnswers] = useState<Map<number, number>>(new Map());

  const steps: TrainingStep[] = [
    {
      id: 0,
      title: 'Welcome to L5',
      description: 'Get started with L5 in 45 minutes',
      estimatedTime: 5,
      content: (
        <div className="space-y-6">
          <div className="bg-blue-50 p-6 rounded-lg border border-blue-200">
            <h3 className="font-semibold text-lg mb-4">What is L5?</h3>
            <p className="text-gray-700 mb-4">
              L5 is CorvinOS's automated decision approval system. It learns which
              config changes are safe, auto-approves high-confidence ones, and routes
              uncertain changes to you for review.
            </p>
            <div className="grid grid-cols-2 gap-4">
              <div className="bg-white p-4 rounded border">
                <p className="font-semibold text-sm">Your Role</p>
                <p className="text-sm text-gray-600">Make 10-30 approval decisions/day</p>
              </div>
              <div className="bg-white p-4 rounded border">
                <p className="font-semibold text-sm">Time Commitment</p>
                <p className="text-sm text-gray-600">~10-20 min/day</p>
              </div>
              <div className="bg-white p-4 rounded border">
                <p className="font-semibold text-sm">System Learning</p>
                <p className="text-sm text-gray-600">Improves from your feedback</p>
              </div>
              <div className="bg-white p-4 rounded border">
                <p className="font-semibold text-sm">Accuracy Target</p>
                <p className="text-sm text-gray-600">>97% (non-revoked approvals)</p>
              </div>
            </div>
          </div>

          <div>
            <h3 className="font-semibold mb-2">Training Overview</h3>
            <ol className="space-y-2 text-sm">
              <li className="flex gap-2">
                <span className="font-semibold">1.</span>
                <span>Welcome (5 min)</span>
              </li>
              <li className="flex gap-2">
                <span className="font-semibold">2.</span>
                <span>5-Gate System (8 min)</span>
              </li>
              <li className="flex gap-2">
                <span className="font-semibold">3.</span>
                <span>Decision Framework (7 min)</span>
              </li>
              <li className="flex gap-2">
                <span className="font-semibold">4.</span>
                <span>Approval Workflow (6 min)</span>
              </li>
              <li className="flex gap-2">
                <span className="font-semibold">5.</span>
                <span>Monitoring Dashboard (5 min)</span>
              </li>
              <li className="flex gap-2">
                <span className="font-semibold">6.</span>
                <span>Real-World Example (8 min)</span>
              </li>
              <li className="flex gap-2">
                <span className="font-semibold">7.</span>
                <span>Troubleshooting (4 min)</span>
              </li>
              <li className="flex gap-2">
                <span className="font-semibold">8.</span>
                <span>Certification Quiz (5 min)</span>
              </li>
            </ol>
          </div>
        </div>
      ),
      quiz: {
        question: 'What is L5 primarily used for?',
        options: [
          {
            text: 'Automatically approving config changes',
            correct: false,
            explanation: 'Partially correct. L5 auto-approves high-confidence changes but routes uncertain ones to operators.',
          },
          {
            text: 'Learning which changes are safe, auto-approving safe ones, routing uncertain ones for operator review',
            correct: true,
            explanation: 'Correct! L5 is a learning system that handles routine decisions and routes interesting ones to you.',
          },
          {
            text: 'Making decisions faster than humans',
            correct: false,
            explanation: 'Not the goal. L5 aims to be safer and more accurate, not faster.',
          },
        ],
      },
    },
    {
      id: 1,
      title: 'The 5-Gate System',
      description: 'Understand how L5 gates work',
      estimatedTime: 8,
      content: (
        <div className="space-y-6">
          <div className="bg-gradient-to-r from-purple-50 to-blue-50 p-6 rounded-lg border border-purple-200">
            <h3 className="font-semibold text-lg mb-4">Five Approval Gates</h3>
            <div className="space-y-4">
              <div className="bg-white p-4 rounded border-l-4 border-purple-500">
                <p className="font-semibold">Gate k=1: Smooth (Auto-Approval)</p>
                <p className="text-sm text-gray-600">Confidence > 95% → Auto-approve instantly</p>
              </div>
              <div className="bg-white p-4 rounded border-l-4 border-blue-500">
                <p className="font-semibold">Gate k=2: Operator (Your Decision)</p>
                <p className="text-sm text-gray-600">70-95% confidence → You decide (5 min SLA)</p>
              </div>
              <div className="bg-white p-4 rounded border-l-4 border-green-500">
                <p className="font-semibold">Gate k=3: Quality (Validation)</p>
                <p className="text-sm text-gray-600">Checks syntax, types, ranges (<100ms)</p>
              </div>
              <div className="bg-white p-4 rounded border-l-4 border-orange-500">
                <p className="font-semibold">Gate k=4: Conflict (Cross-Skill)</p>
                <p className="text-sm text-gray-600">Detects incompatible changes (2 min SLA)</p>
              </div>
              <div className="bg-white p-4 rounded border-l-4 border-red-500">
                <p className="font-semibold">Gate k=5: Hold (Safety Monitoring)</p>
                <p className="text-sm text-gray-600">24-hour probation before permanent lock</p>
              </div>
            </div>
          </div>

          <div className="bg-gray-50 p-4 rounded border">
            <p className="text-sm font-semibold mb-2">Decision Flow:</p>
            <p className="text-sm text-gray-700">
              A change goes through ALL 5 gates. k=1 can auto-complete (skip k=2), but never skips k=3-k=5.
            </p>
          </div>
        </div>
      ),
      quiz: {
        question: 'What is Gate k=2 (Operator)?',
        options: [
          {
            text: 'Auto-approves high-confidence changes',
            correct: false,
            explanation: 'That is Gate k=1 (Smooth). k=2 is where you make decisions.',
          },
          {
            text: 'Where you manually approve/reject changes with 70-95% confidence',
            correct: true,
            explanation: 'Correct! k=2 is your decision point with a 5-minute SLA.',
          },
          {
            text: 'Validates the change is syntactically correct',
            correct: false,
            explanation: 'That is Gate k=3 (Quality). k=2 is operator review.',
          },
        ],
      },
    },
    {
      id: 2,
      title: 'Decision Framework',
      description: 'Learn how to evaluate approval requests',
      estimatedTime: 7,
      content: (
        <div className="space-y-6">
          <div className="bg-yellow-50 border border-yellow-200 p-4 rounded">
            <div className="flex gap-2">
              <Target className="h-5 w-5 text-yellow-600 flex-shrink-0 mt-0.5" />
              <div>
                <p className="font-semibold text-yellow-900">Decision Rule</p>
                <p className="text-sm text-yellow-800">
                  APPROVE if: confidence > 85% + good skill history + small change (<10%)
                </p>
              </div>
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="bg-gray-100">
                  <th className="border p-2 text-left">Confidence</th>
                  <th className="border p-2 text-left">Skill History</th>
                  <th className="border p-2 text-left">Decision</th>
                </tr>
              </thead>
              <tbody>
                <tr className="hover:bg-green-50">
                  <td className="border p-2 font-semibold">85%+</td>
                  <td className="border p-2">Good</td>
                  <td className="border p-2 font-semibold text-green-700">✅ APPROVE</td>
                </tr>
                <tr className="hover:bg-yellow-50">
                  <td className="border p-2 font-semibold">75-85%</td>
                  <td className="border p-2">Good</td>
                  <td className="border p-2 font-semibold text-yellow-700">⚠️ EVALUATE</td>
                </tr>
                <tr className="hover:bg-red-50">
                  <td className="border p-2 font-semibold">&lt;75%</td>
                  <td className="border p-2">Any</td>
                  <td className="border p-2 font-semibold text-red-700">❌ REJECT</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div className="bg-red-50 border border-red-200 p-4 rounded">
            <p className="font-semibold text-red-900 mb-2">🚫 Red Flags (Always Reject)</p>
            <ul className="text-sm text-red-800 space-y-1">
              <li>• Timeout changes > 50%</li>
              <li>• Connection limits 10x change</li>
              <li>• Values outside historical range</li>
              <li>• Cascading changes on same metric</li>
              <li>• Skill repeatedly rejected before</li>
            </ul>
          </div>

          <div className="bg-green-50 border border-green-200 p-4 rounded">
            <p className="font-semibold text-green-900 mb-2">✅ Green Lights (Safe to Approve)</p>
            <ul className="text-sm text-green-800 space-y-1">
              <li>• &lt;10% change from current</li>
              <li>• Confidence > 85%</li>
              <li>• Skill track record &lt;5% revoke</li>
              <li>• Similar decision approved before</li>
              <li>• No conflicting changes in queue</li>
            </ul>
          </div>
        </div>
      ),
      quiz: {
        question:
          'A skill proposes a 3% timeout increase with 88% confidence. Skill has 2% revoke rate. What do you do?',
        options: [
          {
            text: 'REJECT - too risky',
            correct: false,
            explanation: '88% confidence + small change + good skill history = APPROVE. Low revoke rate (2%) is excellent.',
          },
          {
            text: 'APPROVE - meets all criteria',
            correct: true,
            explanation:
              'Correct! Confidence > 85%, change < 10%, skill has excellent history. This is a safe APPROVE.',
          },
          {
            text: 'HOLD - need more information',
            correct: false,
            explanation: 'You have enough information to decide. HOLD is rarely used. APPROVE or REJECT clearly.',
          },
        ],
      },
    },
    {
      id: 3,
      title: 'Approval Workflow',
      description: 'See what happens after you approve',
      estimatedTime: 6,
      content: (
        <div className="space-y-6">
          <div className="bg-gradient-to-b from-blue-50 to-transparent p-6 rounded border border-blue-200">
            <h3 className="font-semibold mb-4">Timeline: After You Approve</h3>
            <div className="space-y-3">
              <div className="flex gap-4 items-start">
                <div className="bg-blue-500 text-white w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 text-sm font-bold">
                  2
                </div>
                <div>
                  <p className="font-semibold text-sm">You Approve (k=2)</p>
                  <p className="text-xs text-gray-600">Your decision is recorded</p>
                </div>
              </div>
              <div className="flex gap-4 items-start">
                <div className="bg-green-500 text-white w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 text-sm font-bold">
                  3
                </div>
                <div>
                  <p className="font-semibold text-sm">Quality Validation (k=3)</p>
                  <p className="text-xs text-gray-600">&lt;100ms - Syntax, types, ranges checked</p>
                </div>
              </div>
              <div className="flex gap-4 items-start">
                <div className="bg-orange-500 text-white w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 text-sm font-bold">
                  4
                </div>
                <div>
                  <p className="font-semibold text-sm">Conflict Detection (k=4)</p>
                  <p className="text-xs text-gray-600">&lt;2min - Checks for incompatible changes</p>
                </div>
              </div>
              <div className="flex gap-4 items-start">
                <div className="bg-red-500 text-white w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 text-sm font-bold">
                  5
                </div>
                <div>
                  <p className="font-semibold text-sm">Hold Period (k=5)</p>
                  <p className="text-xs text-gray-600">24-hour monitoring for issues</p>
                </div>
              </div>
              <div className="flex gap-4 items-start">
                <div className="bg-purple-500 text-white w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 text-sm font-bold">
                  ✓
                </div>
                <div>
                  <p className="font-semibold text-sm">Locked (Permanent)</p>
                  <p className="text-xs text-gray-600">After 24h if monitoring is clean</p>
                </div>
              </div>
            </div>
          </div>

          <div className="bg-purple-50 border border-purple-200 p-4 rounded">
            <p className="font-semibold text-purple-900 mb-2">Monitor During Hold (k=5)</p>
            <p className="text-sm text-purple-800 mb-3">
              The 24-hour hold period is your safety net. Watch for:
            </p>
            <ul className="text-sm text-purple-800 space-y-1">
              <li>• Latency spike (>10% vs. baseline)</li>
              <li>• Error rate increase (>5% new errors)</li>
              <li>• CPU/memory spike (>20% vs. baseline)</li>
              <li>• User complaints or support tickets</li>
            </ul>
            <p className="text-sm text-purple-800 mt-3 font-semibold">
              If issues appear: Click REVOKE to rollback instantly.
            </p>
          </div>
        </div>
      ),
      quiz: {
        question:
          'During hold period, latency jumps 15%. What do you do?',
        options: [
          {
            text: 'Nothing - it might be temporary',
            correct: false,
            explanation: '15% spike is significant and matches your monitoring criteria. You should investigate.',
          },
          {
            text: 'Wait and see what happens at 24h',
            correct: false,
            explanation: 'Hold period is 24 hours, but you can REVOKE earlier if issues emerge. Waiting risks cascade failures.',
          },
          {
            text: 'Investigate quickly - if still spiked after 5 min, REVOKE',
            correct: true,
            explanation: 'Correct! A 15% latency spike is significant. Revoke to rollback and avoid cascade failures.',
          },
        ],
      },
    },
    {
      id: 4,
      title: 'Monitoring Dashboard',
      description: 'Track your performance and system health',
      estimatedTime: 5,
      content: (
        <div className="space-y-6">
          <div className="bg-blue-50 border border-blue-200 p-4 rounded">
            <p className="font-semibold text-blue-900 mb-3">Key Metrics to Monitor</p>
            <div className="space-y-3">
              <div className="bg-white p-3 rounded border-l-4 border-blue-500">
                <p className="text-sm font-semibold">Operator Latency</p>
                <p className="text-xs text-gray-600">
                  How long you take to approve. Target: &lt;5 min. Alert: &gt;10 min.
                </p>
              </div>
              <div className="bg-white p-3 rounded border-l-4 border-green-500">
                <p className="text-sm font-semibold">Your Accuracy</p>
                <p className="text-xs text-gray-600">
                  % of approvals that don't revoke. Target: >97%.
                </p>
              </div>
              <div className="bg-white p-3 rounded border-l-4 border-orange-500">
                <p className="text-sm font-semibold">Rejection Rate</p>
                <p className="text-xs text-gray-600">
                  % you reject. Target: 10-20%. Too high = too cautious.
                </p>
              </div>
              <div className="bg-white p-3 rounded border-l-4 border-red-500">
                <p className="text-sm font-semibold">Revoke Rate</p>
                <p className="text-xs text-gray-600">
                  System-wide. Target: &lt;3%. Alert: >5%.
                </p>
              </div>
            </div>
          </div>

          <div className="bg-gray-50 p-4 rounded border">
            <p className="text-sm font-semibold mb-2">Dashboard Frequency</p>
            <ul className="text-sm text-gray-700 space-y-1">
              <li>• <strong>Ramp-up:</strong> Daily</li>
              <li>• <strong>Stable:</strong> Weekly spot-checks</li>
              <li>• <strong>Incident:</strong> Real-time monitoring</li>
            </ul>
          </div>
        </div>
      ),
      quiz: {
        question: 'Your accuracy is 95%. Is that good?',
        options: [
          {
            text: 'Yes, 95% is excellent',
            correct: false,
            explanation: 'Target is >97%. 95% means 5 reversions per 100 approvals, which is higher than acceptable.',
          },
          {
            text: 'No, target is >97%. Investigate why reversions are happening.',
            correct: true,
            explanation: 'Correct! 95% is below target. Review your recent approvals for patterns.',
          },
          {
            text: 'Depends on the revoke rate',
            correct: false,
            explanation:
              'Your accuracy (your approvals) and revoke rate (system-wide) are separate metrics. Focus on your accuracy first.',
          },
        ],
      },
    },
    {
      id: 5,
      title: 'Real-World Example',
      description: 'Walk through an actual approval request',
      estimatedTime: 8,
      content: (
        <div className="space-y-6">
          <div className="bg-gradient-to-r from-indigo-50 to-blue-50 p-6 rounded border border-indigo-200">
            <h3 className="font-semibold text-lg mb-4">Approval Request #4821</h3>
            <div className="space-y-3 bg-white p-4 rounded mb-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <p className="text-xs text-gray-600">Skill</p>
                  <p className="font-semibold">os.delegation_router</p>
                </div>
                <div>
                  <p className="text-xs text-gray-600">Confidence</p>
                  <p className="font-semibold text-green-700">87%</p>
                </div>
                <div>
                  <p className="text-xs text-gray-600">Current Value</p>
                  <p className="font-semibold">300 seconds</p>
                </div>
                <div>
                  <p className="text-xs text-gray-600">Proposed Value</p>
                  <p className="font-semibold">285 seconds</p>
                </div>
              </div>
              <div>
                <p className="text-xs text-gray-600">Metric</p>
                <p className="font-semibold">router_cache_ttl</p>
              </div>
              <div>
                <p className="text-xs text-gray-600">Change Magnitude</p>
                <p className="font-semibold">-5% (decrease)</p>
              </div>
              <div>
                <p className="text-xs text-gray-600">Reasoning</p>
                <p className="text-sm">
                  Cache TTL optimal at 285s based on request patterns. Reduces memory without
                  affecting hit rate.
                </p>
              </div>
            </div>

            <h4 className="font-semibold mb-3">Decision Process</h4>
            <ol className="space-y-2 text-sm">
              <li className="flex gap-2">
                <span className="font-semibold text-green-700">✓</span>
                <span>Confidence is 87% (above 85% threshold)</span>
              </li>
              <li className="flex gap-2">
                <span className="font-semibold text-green-700">✓</span>
                <span>Change magnitude is -5% (small, safe)</span>
              </li>
              <li className="flex gap-2">
                <span className="font-semibold text-green-700">✓</span>
                <span>Check skill history: 9/10 previous similar decisions succeeded</span>
              </li>
              <li className="flex gap-2">
                <span className="font-semibold text-green-700">✓</span>
                <span>Check for conflicts: No other pending cache-related changes</span>
              </li>
              <li className="flex gap-2">
                <span className="font-semibold text-green-700">✓</span>
                <span>Decision: APPROVE</span>
              </li>
            </ol>
          </div>

          <div className="bg-indigo-50 border border-indigo-200 p-4 rounded">
            <p className="font-semibold text-indigo-900 mb-2">What Happens Next</p>
            <ol className="text-sm text-indigo-800 space-y-1">
              <li>1. k=3 validates 285 is in range [60, 3600] ✓</li>
              <li>2. k=4 detects no conflicts ✓</li>
              <li>3. k=5 deploys with 24-hour hold</li>
              <li>4. You monitor next 24h for issues</li>
              <li>5. After 24h, change is locked (no issues found)</li>
              <li>6. Your accuracy increases (approval didn't revoke)</li>
            </ol>
          </div>
        </div>
      ),
      quiz: {
        question: 'Based on the example, your decision to APPROVE was correct because:',
        options: [
          {
            text: 'Confidence > 85%',
            correct: false,
            explanation: 'True, but not the only reason. All factors together justify the decision.',
          },
          {
            text: 'All of: confidence > 85%, small change (<10%), good skill history, no conflicts',
            correct: true,
            explanation: 'Correct! Every factor pointed to APPROVE.',
          },
          {
            text: 'The skill owner said it was safe',
            correct: false,
            explanation:
              'Never rely solely on skill claims. Use the decision framework (confidence + history + magnitude).',
          },
        ],
      },
    },
    {
      id: 6,
      title: 'Troubleshooting',
      description: 'Handle common issues and escalate when needed',
      estimatedTime: 4,
      content: (
        <div className="space-y-6">
          <div className="space-y-4">
            <div className="border-l-4 border-red-500 bg-red-50 p-4 rounded">
              <p className="font-semibold text-red-900 mb-2">I approved something, and it broke</p>
              <p className="text-sm text-red-800 mb-2">
                <strong>If still in hold period (first 24h):</strong> Click REVOKE immediately. This rolls back the change.
              </p>
              <p className="text-sm text-red-800">
                <strong>If hold expired:</strong> Escalate to ops team. Document the issue.
              </p>
            </div>

            <div className="border-l-4 border-orange-500 bg-orange-50 p-4 rounded">
              <p className="font-semibold text-orange-900 mb-2">A skill keeps proposing changes I reject</p>
              <p className="text-sm text-orange-800 mb-2">
                After 3 rejections in a row, the skill adjusts (lowers confidence, tries different approach).
              </p>
              <p className="text-sm text-orange-800">
                <strong>If it doesn't improve:</strong> Escalate to the skill owner. The skill might be broken.
              </p>
            </div>

            <div className="border-l-4 border-yellow-500 bg-yellow-50 p-4 rounded">
              <p className="font-semibold text-yellow-900 mb-2">I don't understand an approval request</p>
              <p className="text-sm text-yellow-800 mb-2">
                Ask a colleague or the skill owner. Discussion is logged.
              </p>
              <p className="text-sm text-yellow-800">
                <strong>Or:</strong> REJECT and move on. Better safe than guessing.
              </p>
            </div>

            <div className="border-l-4 border-purple-500 bg-purple-50 p-4 rounded">
              <p className="font-semibold text-purple-900 mb-2">The system looks broken</p>
              <p className="text-sm text-purple-800 mb-2">
                Check dashboard alerts. Page the L5 oncall team via Slack #l5-oncall.
              </p>
              <p className="text-sm text-purple-800">
                <strong>Don't:</strong> Try to fix it yourself. Escalate immediately.
              </p>
            </div>
          </div>

          <div className="bg-gray-50 border border-gray-200 p-4 rounded">
            <p className="font-semibold text-gray-900 mb-2">📞 Support Contacts</p>
            <ul className="text-sm text-gray-700 space-y-1">
              <li>
                <strong>Slack:</strong> #l5-oncall (urgent issues)
              </li>
              <li>
                <strong>Email:</strong> l5-support@corvin-labs.io
              </li>
              <li>
                <strong>Wiki:</strong> L5 Operator Guide + FAQ
              </li>
            </ul>
          </div>
        </div>
      ),
      quiz: {
        question: 'The queue is stuck for 2 hours. What do you do?',
        options: [
          {
            text: 'Check the dashboard alerts and escalate to #l5-oncall immediately',
            correct: true,
            explanation:
              'Correct! Stuck queue is an incident. Check alerts to diagnose, then escalate. Do not try to fix it yourself.',
          },
          {
            text: 'Wait another hour and see if it resolves',
            correct: false,
            explanation: '2 hours is already too long. Escalate immediately. Waiting makes it worse.',
          },
          {
            text: 'Try restarting the system',
            correct: false,
            explanation: 'Do not restart. That is an ops task. Page the oncall team.',
          },
        ],
      },
    },
    {
      id: 7,
      title: 'Certification Quiz',
      description: 'Prove you understand L5',
      estimatedTime: 5,
      content: (
        <div className="space-y-6">
          <div className="bg-green-50 border border-green-200 p-6 rounded">
            <div className="flex gap-3 mb-4">
              <CheckCircle2 className="h-6 w-6 text-green-700 flex-shrink-0" />
              <h3 className="font-semibold text-green-900 text-lg">You're Ready!</h3>
            </div>
            <p className="text-sm text-green-800 mb-4">
              You've completed 7 lessons covering L5 fundamentals. You've learned:
            </p>
            <ul className="text-sm text-green-800 space-y-1 mb-4">
              <li>✓ How L5 works (5-gate system)</li>
              <li>✓ Your role as an operator</li>
              <li>✓ Decision framework (confidence + history + magnitude)</li>
              <li>✓ What happens after you approve</li>
              <li>✓ How to monitor your performance</li>
              <li>✓ Real-world approval example</li>
              <li>✓ Troubleshooting and escalation</li>
            </ul>
            <p className="text-sm text-green-800 font-semibold">
              Now take this 5-question certification quiz to lock in your understanding.
            </p>
          </div>

          <div className="bg-blue-50 border border-blue-200 p-4 rounded">
            <p className="text-sm font-semibold text-blue-900 mb-2">Quiz Rules</p>
            <ul className="text-xs text-blue-800 space-y-1">
              <li>• 5 questions (multiple choice)</li>
              <li>• Pass: 4/5 correct</li>
              <li>• You can retake after mistakes</li>
              <li>• Certificate awarded on pass</li>
            </ul>
          </div>
        </div>
      ),
      quiz: {
        question: 'What is the main purpose of the 24-hour hold period (Gate k=5)?',
        options: [
          {
            text: 'To delay deployments',
            correct: false,
            explanation: 'Hold period is not about delay—it is about safety. It gives time to catch issues.',
          },
          {
            text: 'To monitor for issues before permanently locking the change',
            correct: true,
            explanation:
              'Correct! Hold is your safety net. You can revoke if issues emerge before 24h is up.',
          },
          {
            text: 'To give the skill more time to learn',
            correct: false,
            explanation: 'Skill learning happens during k=2 decision, not during hold period.',
          },
        ],
      },
    },
  ];

  const currentStepData = steps[currentStep];
  const progress = ((completedSteps.size + (quizAnswers.has(currentStep) ? 1 : 0)) / steps.length) *
    100;
  const quizAnswer = quizAnswers.get(currentStep);
  const isQuizAnswered = quizAnswer !== undefined;

  const handleQuizAnswer = (optionIndex: number) => {
    setQuizAnswers(new Map(quizAnswers).set(currentStep, optionIndex));
    if (!completedSteps.has(currentStep)) {
      const newCompleted = new Set(completedSteps);
      newCompleted.add(currentStep);
      setCompletedSteps(newCompleted);
    }
  };

  const handleNext = () => {
    if (currentStep < steps.length - 1) {
      setCurrentStep(currentStep + 1);
      window.scrollTo(0, 0);
    }
  };

  const handlePrevious = () => {
    if (currentStep > 0) {
      setCurrentStep(currentStep - 1);
      window.scrollTo(0, 0);
    }
  };

  const allCompleted = completedSteps.size === steps.length && quizAnswers.size === steps.length;

  return (
    <div className="space-y-6 max-w-4xl mx-auto p-4">
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-start justify-between">
            <div>
              <CardTitle className="text-2xl">{currentStepData.title}</CardTitle>
              <CardDescription className="text-base mt-1">
                {currentStepData.description}
              </CardDescription>
            </div>
            <div className="text-right">
              <div className="text-sm font-semibold text-gray-600">
                Step {currentStep + 1} of {steps.length}
              </div>
              <div className="flex gap-1 mt-1 text-xs text-gray-600">
                <Clock className="h-4 w-4" />
                <span>{currentStepData.estimatedTime} min</span>
              </div>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <div className="flex justify-between text-xs text-gray-600">
              <span>Progress</span>
              <span>{Math.round(progress)}%</span>
            </div>
            <Progress value={progress} className="h-2" />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-6">
          <div className="mb-8">{currentStepData.content}</div>

          {currentStepData.quiz && (
            <div className="border-t pt-6 mt-6">
              <div className="bg-blue-50 border border-blue-200 p-4 rounded mb-4">
                <p className="font-semibold text-blue-900 mb-3">Quick Quiz</p>
                <p className="text-sm text-blue-800 mb-4 font-semibold">
                  {currentStepData.quiz.question}
                </p>
                <div className="space-y-2">
                  {currentStepData.quiz.options.map((option, idx) => (
                    <button
                      key={idx}
                      onClick={() => handleQuizAnswer(idx)}
                      className={`w-full text-left p-3 rounded border transition-all ${
                        quizAnswer === idx
                          ? option.correct
                            ? 'bg-green-100 border-green-500 text-green-900'
                            : 'bg-red-100 border-red-500 text-red-900'
                          : 'bg-white border-gray-300 hover:border-blue-500'
                      }`}
                    >
                      <p className="text-sm font-semibold">{option.text}</p>
                      {quizAnswer === idx && (
                        <p className="text-xs mt-1">{option.explanation}</p>
                      )}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="flex gap-3 justify-between items-center">
        <Button onClick={handlePrevious} disabled={currentStep === 0} variant="outline">
          Previous
        </Button>
        <div className="text-sm text-gray-600">
          Step {currentStep + 1}/{steps.length}
        </div>
        {currentStep === steps.length - 1 ? (
          <Button
            disabled={!allCompleted}
            className="gap-2 bg-green-600 hover:bg-green-700"
          >
            <CheckCircle2 className="h-4 w-4" />
            {allCompleted ? 'Get Certificate' : 'Complete All Steps'}
          </Button>
        ) : (
          <Button onClick={handleNext} className="gap-2">
            Next
            <ChevronRight className="h-4 w-4" />
          </Button>
        )}
      </div>

      {allCompleted && (
        <Card className="bg-gradient-to-r from-green-50 to-emerald-50 border-2 border-green-500">
          <CardContent className="pt-6 text-center">
            <CheckCircle2 className="h-12 w-12 text-green-600 mx-auto mb-3" />
            <h3 className="font-bold text-lg text-green-900 mb-1">
              Certification Complete!
            </h3>
            <p className="text-sm text-green-800 mb-4">
              You've successfully completed the L5 Operator Training program.
            </p>
            <p className="text-xs text-green-700">
              You are now authorized to review and approve configuration changes in L5.
              <br />
              Certificate issued: {new Date().toLocaleDateString()}
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  );
};

export default L5TrainingModule;
