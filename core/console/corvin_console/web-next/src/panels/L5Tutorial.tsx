/**
 * Phase 6: L5 Operator Training — Interactive Tutorial & Onboarding
 *
 * Features:
 * - Step-by-step guided tour of L5 approval workflow
 * - Real-world scenario walkthroughs (k=1 through k=5 gates)
 * - Best practices and tuning strategies
 * - Common mistakes and recovery procedures
 * - Interactive sandbox for learning
 * - Progress tracking and completion badges
 *
 * ADR-0589: L5 Operator Training & Support
 */

import React, { useState, useCallback, useMemo } from "react";
import {
  ChevronRight,
  ChevronLeft,
  CheckCircle,
  AlertCircle,
  Lightbulb,
  BookOpen,
  Play,
  SkipForward,
  Award,
} from "lucide-react";

// ============================================================================
// Types & Constants
// ============================================================================

interface TutorialStep {
  id: string;
  title: string;
  description: string;
  content: string;
  objectives: string[];
  codeExample?: string;
  bestPractices?: string[];
  commonMistakes?: string[];
  quiz?: QuizQuestion[];
}

interface QuizQuestion {
  id: string;
  question: string;
  options: string[];
  correctAnswer: number;
  explanation: string;
}

interface TutorialProgress {
  completedSteps: string[];
  currentStepIndex: number;
  quizScores: Record<string, number>; // step_id -> score (0-100)
}

// ============================================================================
// Tutorial Content
// ============================================================================

const TUTORIAL_STEPS: TutorialStep[] = [
  {
    id: "step_1_intro",
    title: "What is L5? Overview",
    description: "Understand the L5 approval system and why it matters",
    content: `
L5 is CorvinOS's automated decision approval system for production configuration changes.
It bridges AI decision-making with human oversight through a 5-gate workflow:

- **k=1 (Smooth)**: Automatically approve high-confidence changes
- **k=2 (Operator)**: Route low-confidence changes to operator for approval
- **k=3 (Quality)**: Validate configuration quality before applying
- **k=4 (Conflict)**: Detect cross-skill conflicts and coordinate resolution
- **k=5 (Hold)**: Temporary hold period before permanent deployment

Each gate runs independently; all 5 must pass for a change to deploy.

Why it matters:
✓ Reduces operator load (60% auto-approved)
✓ Prevents bad configs (quality gate catches errors)
✓ Coordinates across skills (conflict resolution)
✓ Provides safety net (hold period for rollback)
✓ Tracks everything (complete audit trail)
    `,
    objectives: [
      "Understand the 5-gate workflow",
      "Learn why each gate exists",
      "Recognize when to escalate decisions",
    ],
    bestPractices: [
      "Read approval details carefully — they contain reasoning",
      "Use the hold period to monitor for issues",
      "Set up alerts for CRITICAL status changes",
    ],
    commonMistakes: [
      "Ignoring low-confidence approvals (they often hide issues)",
      "Forcing approval of quality gate rejects",
      "Skipping the hold period — it's there for a reason",
    ],
    quiz: [
      {
        id: "q1",
        question: "How many gates does L5 have?",
        options: ["3", "5", "7", "9"],
        correctAnswer: 1,
        explanation: "L5 has 5 gates (k=1 through k=5), each serving a distinct purpose.",
      },
      {
        id: "q2",
        question: "What is the purpose of the k=3 (Quality) gate?",
        options: [
          "Route to operator",
          "Validate configuration quality",
          "Detect conflicts",
          "Hold for period",
        ],
        correctAnswer: 1,
        explanation: "k=3 validates configuration quality and catches syntax errors before deployment.",
      },
    ],
  },

  {
    id: "step_2_k1_smooth",
    title: "k=1 (Smooth): Auto-Approval",
    description: "Learn how Smooth gate automatically approves high-confidence changes",
    content: `
The Smooth gate automatically approves changes when confidence is high (typically >95%).

How it works:
1. Skill proposes a config change with confidence score
2. Smooth gate checks confidence threshold (default 95%)
3. If confidence > threshold → auto-approved (zero operator involvement)
4. If confidence ≤ threshold → routed to k=2 (Operator) gate

Example: A simple latency tuning from 100ms → 95ms with 98% confidence confidence
→ Smooth gate auto-approves in milliseconds
→ No operator action needed

Benefits:
• Reduces decision load on operators
• Fastest path to deployment
• Lower latency for safe changes

When to adjust Smooth threshold:
• Too high (>98%): Misses good changes that need review
• Too low (<90%): Too many changes skip quality review
• Goldilocks (95%): Catches 60-70% of good changes, routes 30-40% for review
    `,
    objectives: [
      "Understand auto-approval criteria",
      "Know when Smooth gate auto-approves",
      "Recognize when to lower Smooth threshold",
    ],
    codeExample: `
// Smooth gate logic
if (confidence > SMOOTH_THRESHOLD):  // 95% default
    approval_status = "auto_approved"
else:
    approval_status = "pending"  // Route to k=2
    `,
    bestPractices: [
      "Trust the auto-approval (it's been tested extensively)",
      "Monitor auto-approved changes for unexpected side effects",
      "Use dashboard to see auto-approval rate trending",
    ],
    commonMistakes: [
      "Lowering Smooth threshold too much (approves bad configs)",
      "Ignoring auto-approved changes (leads to surprises)",
      "Manually reviewing auto-approved changes (defeats the purpose)",
    ],
  },

  {
    id: "step_3_k2_operator",
    title: "k=2 (Operator): Manual Approval",
    description: "Approve or reject changes that don't meet auto-approval criteria",
    content: `
The Operator gate routes low-confidence changes to you for manual decision.

When you'll see an approval request:
✓ Confidence between 70-95% (uncertain decisions)
✓ New metric (unseen before, limited history)
✓ Edge case behavior (outside normal distribution)
✓ Config with risky parameters (e.g., timeout values)

Your decision:
1. APPROVE → Change proceeds to k=3 (Quality) gate
2. REJECT → Change is blocked; skill learns why
3. HOLD → Temporarily pending (rare; for more data)

What you see in approval request:
• Skill ID (which subsystem is proposing)
• Metric name (what's being changed)
• Magnitude (how much change)
• Confidence (probability it's correct)
• Reason (why the skill thinks this is right)
• Previous config (what we're changing from)
• Proposed config (what we're changing to)
• Audit event ID (full history link)

SLA: You have 5 minutes to decide (before it escalates)

Decision tips:
✓ Read the reason carefully — it explains the decision
✓ Check the magnitude — small changes are safer
✓ Review the previous config — was the old value reasonable?
✓ Trust the confidence — high confidence usually means correct
    `,
    objectives: [
      "Understand when k=2 approval is needed",
      "Know how to evaluate an approval request",
      "Learn the SLA (5 minute decision time)",
    ],
    codeExample: `
// Operator gate flow
for approval in pending_approvals:
    if time_since_request > 5_minutes:
        escalate_to_management()
    elif operator_approved:
        send_to_k3_quality_gate()
    elif operator_rejected:
        log_rejection_reason()
        skill.learn_from_rejection()
    `,
    bestPractices: [
      "Batch approvals (10 at a time) to reduce context switching",
      "Document your reasoning in the comment field",
      "Trust your gut — if something feels wrong, reject it",
      "Use historical context (previous 10 similar decisions)",
    ],
    commonMistakes: [
      "Approving without reading the reason",
      "Rejecting based on gut feeling without data",
      "Taking too long (SLA is 5 minutes)",
      "Approving risky changes without checking",
    ],
  },

  {
    id: "step_4_k3_quality",
    title: "k=3 (Quality): Validation Gate",
    description: "Understand how the Quality gate validates configuration correctness",
    content: `
The Quality gate automatically validates that a proposed config is:
✓ Syntactically correct (proper types, ranges)
✓ Semantically valid (makes sense in context)
✓ Within safe bounds (doesn't violate constraints)
✓ Non-conflicting (doesn't contradict other params)

When Quality gate REJECTS:
• Syntax error (e.g., timeout="abc" when number expected)
• Out of bounds (e.g., confidence=-0.5 when 0-1 range expected)
• Type mismatch (e.g., string when integer expected)
• Constraint violation (e.g., min_latency > max_latency)

What happens on rejection:
1. Change is BLOCKED (never applied)
2. Skill receives feedback (learns constraint)
3. Skill must propose a different value
4. Change re-enters at k=1 (Smooth) gate

Quality gate rejects are GOOD — they catch bugs before production.
If you're seeing many quality rejects on a skill:
→ That skill may need recalibration
→ Contact the skill owner to debug

Example rejection:
  Config: {"timeout_ms": -100}
  Reason: "timeout_ms must be > 0"
  Action: Skill learns constraint, proposes {"timeout_ms": 5000}
    `,
    objectives: [
      "Understand what Quality gate validates",
      "Know why rejections happen",
      "Learn to handle quality gate rejects",
    ],
    bestPractices: [
      "Treat quality rejects as learning signals (don't override)",
      "Monitor rejection patterns (indicates skill issues)",
      "Work with skill owners on persistent rejects",
    ],
    commonMistakes: [
      "Forcing approval of quality rejects (breaks guarantees)",
      "Ignoring reject patterns (misses skill bugs)",
      "Assuming quality rejects are false positives (usually not)",
    ],
  },

  {
    id: "step_5_k4_conflict",
    title: "k=4 (Conflict): Cross-Skill Coordination",
    description: "Resolve conflicts when multiple skills propose incompatible changes",
    content: `
The Conflict gate detects when multiple skills propose changes that contradict each other.

Example conflict:
• Skill A says: "Increase connection timeout to 10s"
• Skill B says: "Decrease max connections to 100"
• Result: Higher timeout + lower connections = resource deadlock risk

What k=4 does:
1. Detects incompatible change combinations
2. Ranks changes by confidence + importance
3. Proposes winning change (highest priority)
4. Routes others back for reconsideration

Your role:
Usually none — k=4 auto-resolves 95% of conflicts.
But when it escalates to you:
→ Two equally important skills disagree
→ You pick the winner (by priority/urgency)
→ Loser is rerouted to reconsider

Conflict resolution SLA: 2 minutes (faster than operator approval)

Example escalation:
  Conflict: Latency optimization vs Cost optimization
  Impact: Latency wins → Higher throughput, slightly higher cost
          Cost wins → Lower cost, slightly higher latency
  Your call: Which is more important right now?
    `,
    objectives: [
      "Understand cross-skill conflict scenarios",
      "Know when k=4 escalates to you",
      "Learn to resolve conflicts by priority",
    ],
    bestPractices: [
      "Establish clear priority rules (latency > cost in peak hours)",
      "Document conflict decisions for future reference",
      "Review conflict patterns (may indicate skill tuning issues)",
    ],
    commonMistakes: [
      "Not understanding the trade-off (read it carefully)",
      "Making conflict decisions too fast (take 30 seconds)",
      "Ignoring long-term impact (think beyond immediate decision)",
    ],
  },

  {
    id: "step_6_k5_hold",
    title: "k=5 (Hold): Safety Holdover",
    description: "Understand the hold period and rollback safety net",
    content: `
The Hold gate applies a temporary hold period (default 24 hours) before permanent deployment.

Why the hold period?
✓ Catches unexpected side effects (time-dependent bugs)
✓ Allows rollback within window (if problems emerge)
✓ Lets other systems adapt (downstream dependencies)
✓ Provides data for learning (how did this actually behave?)

Timeline for a single change:
  T+0m: Change auto-approved (k=1, k=2, k=3, k=4 pass)
  T+0m: Deployed to production (with hold flag)
  T+0-24h: MONITORING window
           - Watch metrics (latency, errors, CPU)
           - Monitor logs (any failures?)
           - Check user complaints (tickets/forum)
  T+24h: Hold expires
         - If all green → permanent (change locked in)
         - If problems found → automatic rollback

Your role during Hold:
1. Monitor metrics for anomalies
2. Watch for error spikes
3. Check performance traces
4. Early rollback if needed (don't wait 24h)

Early rollback:
If you notice issues during hold:
  1. Click "REVOKE" in dashboard
  2. Change is immediately rolled back to previous version
  3. Skill receives negative feedback (learns what went wrong)
  4. Skill proposes different approach next time

What NOT to do:
✗ Wait until hold expires to notice issues
✗ Assume "no news = good news"
✗ Ignore anomalies that correlate with change time
✗ Force revoke without understanding the issue
    `,
    objectives: [
      "Understand why hold period exists",
      "Know how to monitor during hold",
      "Learn when to early revoke",
    ],
    codeExample: `
// Hold gate timeline
change.status = "deployed_with_hold"
change.hold_expiry = now() + 24_hours

while (not change.hold_expiry):
    if metrics_show_anomaly():
        change.revoke()  // Early rollback
        skill.learn_from_revoke()
        break

    sleep(60)  // Monitor every 60 seconds

if not revoked:
    change.status = "permanent"
    `,
    bestPractices: [
      "Set up alerts for metric anomalies during hold period",
      "Batch monitor (check all holds every 15 minutes)",
      "Document early revokes (helps skill learning)",
      "Trust the metrics (they don't lie)",
    ],
    commonMistakes: [
      "Not monitoring during hold (defeats the purpose)",
      "Revoking on false alarms (causes unnecessary churn)",
      "Waiting too long to revoke (production impact grows)",
      "Revoking without understanding why (skill can't learn)",
    ],
  },

  {
    id: "step_7_best_practices",
    title: "Best Practices & Tuning Strategies",
    description: "Learn how to optimize L5 for your environment",
    content: `
Approval workflow best practices:

1. BATCH PROCESSING
   ✓ Group similar approvals (10-15 at a time)
   ✓ Reduces context switching
   ✓ Faster decision making
   ✗ Don't approve individually (context loss)

2. CONFIDENCE THRESHOLDS
   Current (default):
   • Smooth (k=1): 95% → auto-approve
   • Operator (k=2): 70-95% → manual review
   • Quality (k=3): 0-100% → validate correctness

   Tuning for your org:
   • High stability (SLA-critical): Raise to 97% smooth threshold
   • High throughput (growth-focused): Lower to 92% smooth threshold
   • Risky changes (untested): Lower to 70% operator threshold

3. MONITORING DURING HOLDS
   Set up dashboard alerts for:
   • Latency spike (>10% vs baseline)
   • Error rate spike (>5% new errors)
   • CPU spike (>20% vs baseline)
   • Custom metrics (business KPIs)

4. ROLLBACK STRATEGIES
   Early rollback decision tree:
   • Latency spike + hold window < 2h → REVOKE (usually propagation delay)
   • Error spike > 50% → REVOKE (definitely wrong)
   • Error spike 5-50% → WAIT 10min, then decide
   • Slow degradation → WAIT until clear trend

5. LEARNING FEEDBACK
   After each approval decision, write 1-2 sentences:
   • APPROVED: "Config good, tested in dev"
   • REJECTED: "Too aggressive, conflict with B service"
   • REVOKED: "Latency spiked 15ms, rolled back"

   This teaches the system your decision criteria.

6. SKILL COORDINATION
   If same skill keeps getting rejected:
   → Contact skill owner, discuss constraints
   → Review historical rejections
   → Adjust skill tuning together

7. ALERT FATIGUE MANAGEMENT
   Too many alerts? Adjust thresholds:
   • Baseline learning (week 1): All alerts enabled
   • Tuning phase (week 2): Adjust thresholds for signal/noise
   • Production (week 3+): Only critical alerts
    `,
    objectives: [
      "Learn batch processing for efficiency",
      "Understand confidence threshold tuning",
      "Know when to early rollback",
      "Learn to provide feedback for skill learning",
    ],
  },

  {
    id: "step_8_troubleshooting",
    title: "Troubleshooting & Common Issues",
    description: "What to do when things go wrong",
    content: `
Common issues and recovery procedures:

ISSUE 1: Queue backing up (100+ pending approvals)
Status: ⚠️ WARNING
Cause: Operator load too high, or auto-approval threshold too conservative

Recovery:
1. Increase Smooth threshold (95% → 98%)
   → More auto-approvals, less manual load
2. Check operator SLA (5 minutes)
   → May need to add more operators
3. Review approval reasons
   → May indicate skill miscalibration

Expected time to clear: 15-30 minutes

ISSUE 2: Quality gate rejecting good configs
Status: ⚠️ WARNING
Cause: Skill is violating constraints, or constraints too strict

Recovery:
1. Check rejection pattern
   → Same error repeatedly? Skill misunderstanding constraint
2. Contact skill owner
   → Review constraint definition
   → May need constraint relaxation
3. Document rejection reasons
   → Helps skill learning

Expected time to resolve: 1-4 hours

ISSUE 3: High revoke rate during hold period
Status: 🔴 CRITICAL
Cause: Auto-approval threshold too low (catching bad changes)
       OR: Quality validation too permissive

Recovery:
1. Lower Smooth threshold (95% → 90%)
   → Catches more uncertain changes for manual review
2. Strengthen Quality validation
   → Review constraint set
   → Add missing constraints
3. Increase hold period (24h → 48h)
   → More time to catch issues
4. Review revoked changes
   → What patterns do they share?

Expected time to resolve: 1-3 days

ISSUE 4: Operator latency exceeding SLA (>5 minutes)
Status: 🔴 CRITICAL
Cause: Too many pending approvals, operator shortage

Recovery:
1. Increase Smooth threshold
   → Reduce manual approval load
2. Hire/train more operators
   → Need faster decision velocity
3. Improve approval UI/UX
   → May be latency from slow decisions
4. Check priority routing
   → CRITICAL approvals should come first

Expected time to resolve: 1-7 days

ISSUE 5: High conflict resolution escalation rate
Status: ⚠️ WARNING
Cause: Multiple skills proposing incompatible changes frequently

Recovery:
1. Review skill dependencies
   → May need explicit coordination
2. Adjust skill priorities
   → Clear priority ranking reduces escalations
3. Contact skill owners
   → Discuss coordination strategies
4. Consider skill separation
   → Very different concerns may need different skills

Expected time to resolve: 1-2 weeks

EMERGENCY: COMPLETE SYSTEM FAILURE
Status: 🔴 CRITICAL
Recovery:
1. Switch to MANUAL MODE
   → All changes require manual approval
   → k=1 (Smooth) gate disabled
2. Contact L5 maintainers
   → Get system back to health
3. Audit recent changes
   → Find what caused failure
4. Restore from last known good config
   → Manual rollback if needed

Expected time to restore: 30 minutes - 2 hours
    `,
    objectives: [
      "Recognize common L5 issues",
      "Know recovery procedures",
      "Understand when to escalate",
      "Learn emergency procedures",
    ],
  },
];

// ============================================================================
// Tutorial Component
// ============================================================================

export default function L5Tutorial() {
  const [progress, setProgress] = useState<TutorialProgress>({
    completedSteps: [],
    currentStepIndex: 0,
    quizScores: {},
  });

  const [showQuiz, setShowQuiz] = useState(false);
  const [quizAnswers, setQuizAnswers] = useState<Record<string, number>>({});

  const currentStep = TUTORIAL_STEPS[progress.currentStepIndex];
  const isStepComplete = progress.completedSteps.includes(currentStep.id);
  const completionPercentage = Math.round(
    (progress.completedSteps.length / TUTORIAL_STEPS.length) * 100
  );

  const handleNextStep = useCallback(() => {
    if (progress.currentStepIndex < TUTORIAL_STEPS.length - 1) {
      setProgress((prev) => ({
        ...prev,
        currentStepIndex: prev.currentStepIndex + 1,
        completedSteps: [...new Set([...prev.completedSteps, currentStep.id])],
      }));
      setShowQuiz(false);
      setQuizAnswers({});
    }
  }, [progress.currentStepIndex, currentStep.id]);

  const handlePreviousStep = useCallback(() => {
    if (progress.currentStepIndex > 0) {
      setProgress((prev) => ({
        ...prev,
        currentStepIndex: prev.currentStepIndex - 1,
      }));
      setShowQuiz(false);
      setQuizAnswers({});
    }
  }, [progress.currentStepIndex]);

  const handleCompleteStep = useCallback(() => {
    setProgress((prev) => ({
      ...prev,
      completedSteps: [...new Set([...prev.completedSteps, currentStep.id])],
    }));
  }, [currentStep.id]);

  const handleQuizSubmit = useCallback(() => {
    let score = 0;
    if (currentStep.quiz) {
      currentStep.quiz.forEach((q) => {
        if (quizAnswers[q.id] === q.correctAnswer) {
          score += 100 / currentStep.quiz!.length;
        }
      });
    }
    setProgress((prev) => ({
      ...prev,
      quizScores: { ...prev.quizScores, [currentStep.id]: Math.round(score) },
      completedSteps: [...new Set([...prev.completedSteps, currentStep.id])],
    }));
    setShowQuiz(false);
  }, [currentStep, quizAnswers]);

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 p-8">
      <div className="max-w-5xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <div className="flex items-center gap-3 mb-4">
            <BookOpen className="w-8 h-8 text-blue-600" />
            <h1 className="text-4xl font-bold text-gray-900">L5 Operator Training</h1>
          </div>
          <p className="text-lg text-gray-700">
            Interactive tutorial for L5 approval system operators
          </p>
        </div>

        {/* Progress Bar */}
        <div className="bg-white rounded-lg p-6 mb-8 shadow-md">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-semibold text-gray-600">Course Progress</span>
            <span className="text-lg font-bold text-blue-600">{completionPercentage}%</span>
          </div>
          <div className="h-3 bg-gray-200 rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-blue-500 to-indigo-600 transition-all duration-300"
              style={{ width: `${completionPercentage}%` }}
            />
          </div>
          <div className="mt-4 text-sm text-gray-600">
            {progress.completedSteps.length} of {TUTORIAL_STEPS.length} steps completed
          </div>
        </div>

        {/* Main Content */}
        <div className="bg-white rounded-lg shadow-lg overflow-hidden">
          {/* Step Navigation */}
          <div className="bg-gradient-to-r from-blue-600 to-indigo-600 text-white p-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm opacity-90">
                  Step {progress.currentStepIndex + 1} of {TUTORIAL_STEPS.length}
                </p>
                <h2 className="text-2xl font-bold mt-1">{currentStep.title}</h2>
                <p className="text-indigo-100 mt-2">{currentStep.description}</p>
              </div>
              {isStepComplete && (
                <CheckCircle className="w-12 h-12 text-green-300 flex-shrink-0" />
              )}
            </div>
          </div>

          {/* Content Area */}
          <div className="p-8">
            {!showQuiz ? (
              <>
                {/* Main Content */}
                <div className="prose prose-sm max-w-none mb-8">
                  <div className="whitespace-pre-wrap text-gray-700 leading-relaxed">
                    {currentStep.content}
                  </div>
                </div>

                {/* Code Example (if present) */}
                {currentStep.codeExample && (
                  <div className="mb-8 bg-gray-900 rounded-lg p-6 overflow-x-auto">
                    <p className="text-sm font-semibold text-gray-300 mb-3">Code Example</p>
                    <pre className="text-sm text-gray-100 font-mono">
                      {currentStep.codeExample}
                    </pre>
                  </div>
                )}

                {/* Best Practices */}
                {currentStep.bestPractices && (
                  <div className="mb-8 bg-green-50 border-l-4 border-green-500 p-6 rounded">
                    <p className="font-semibold text-green-900 mb-3 flex items-center gap-2">
                      <Lightbulb className="w-5 h-5" />
                      Best Practices
                    </p>
                    <ul className="space-y-2 text-green-800">
                      {currentStep.bestPractices.map((practice, i) => (
                        <li key={i} className="flex gap-2">
                          <span className="text-green-600 font-bold">✓</span>
                          {practice}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Common Mistakes */}
                {currentStep.commonMistakes && (
                  <div className="mb-8 bg-yellow-50 border-l-4 border-yellow-500 p-6 rounded">
                    <p className="font-semibold text-yellow-900 mb-3 flex items-center gap-2">
                      <AlertCircle className="w-5 h-5" />
                      Common Mistakes
                    </p>
                    <ul className="space-y-2 text-yellow-800">
                      {currentStep.commonMistakes.map((mistake, i) => (
                        <li key={i} className="flex gap-2">
                          <span className="text-yellow-600 font-bold">✗</span>
                          {mistake}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Objectives */}
                <div className="mb-8 bg-blue-50 border-l-4 border-blue-500 p-6 rounded">
                  <p className="font-semibold text-blue-900 mb-3">Learning Objectives</p>
                  <ul className="space-y-2">
                    {currentStep.objectives.map((obj, i) => (
                      <li key={i} className="text-blue-800 flex gap-2">
                        <span className="text-blue-600">◆</span>
                        {obj}
                      </li>
                    ))}
                  </ul>
                </div>
              </>
            ) : (
              <>
                {/* Quiz */}
                <div className="space-y-6">
                  <h3 className="text-xl font-bold text-gray-900">Knowledge Check</h3>
                  {currentStep.quiz?.map((question) => (
                    <div key={question.id} className="border border-gray-200 rounded-lg p-6">
                      <p className="font-semibold text-gray-900 mb-4">{question.question}</p>
                      <div className="space-y-2">
                        {question.options.map((option, idx) => (
                          <label key={idx} className="flex items-center gap-3 cursor-pointer">
                            <input
                              type="radio"
                              name={question.id}
                              value={idx}
                              checked={quizAnswers[question.id] === idx}
                              onChange={(e) =>
                                setQuizAnswers((prev) => ({
                                  ...prev,
                                  [question.id]: idx,
                                }))
                              }
                              className="w-4 h-4"
                            />
                            <span className="text-gray-700">{option}</span>
                          </label>
                        ))}
                      </div>
                      {quizAnswers[question.id] !== undefined && (
                        <div
                          className={`mt-4 p-3 rounded ${
                            quizAnswers[question.id] === question.correctAnswer
                              ? "bg-green-100 text-green-800"
                              : "bg-red-100 text-red-800"
                          }`}
                        >
                          <p className="font-semibold">
                            {quizAnswers[question.id] === question.correctAnswer
                              ? "✓ Correct!"
                              : "✗ Incorrect"}
                          </p>
                          <p className="text-sm mt-1">{question.explanation}</p>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>

          {/* Navigation Buttons */}
          <div className="bg-gray-50 border-t border-gray-200 p-6 flex justify-between items-center">
            <button
              onClick={handlePreviousStep}
              disabled={progress.currentStepIndex === 0}
              className="flex items-center gap-2 px-4 py-2 bg-gray-200 text-gray-700 rounded-lg disabled:opacity-50 disabled:cursor-not-allowed hover:bg-gray-300"
            >
              <ChevronLeft className="w-5 h-5" />
              Previous
            </button>

            <div className="flex gap-4">
              {!showQuiz && currentStep.quiz && (
                <button
                  onClick={() => setShowQuiz(true)}
                  className="flex items-center gap-2 px-6 py-2 bg-yellow-500 text-white rounded-lg hover:bg-yellow-600"
                >
                  <Play className="w-5 h-5" />
                  Take Quiz
                </button>
              )}

              {showQuiz && (
                <button
                  onClick={handleQuizSubmit}
                  className="flex items-center gap-2 px-6 py-2 bg-green-500 text-white rounded-lg hover:bg-green-600"
                >
                  <CheckCircle className="w-5 h-5" />
                  Submit Quiz
                </button>
              )}

              {!showQuiz && (
                <>
                  {!isStepComplete && (
                    <button
                      onClick={handleCompleteStep}
                      className="flex items-center gap-2 px-6 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600"
                    >
                      <Award className="w-5 h-5" />
                      Mark Complete
                    </button>
                  )}
                  <button
                    onClick={handleNextStep}
                    disabled={progress.currentStepIndex === TUTORIAL_STEPS.length - 1}
                    className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg disabled:opacity-50 disabled:cursor-not-allowed hover:bg-blue-700"
                  >
                    Next
                    <ChevronRight className="w-5 h-5" />
                  </button>
                </>
              )}
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="text-center mt-8 text-gray-600">
          <p>ADR-0589: L5 Operator Training & Support</p>
        </div>
      </div>
    </div>
  );
}
