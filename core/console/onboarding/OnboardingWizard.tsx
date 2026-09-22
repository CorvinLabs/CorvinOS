/**
 * OnboardingWizard.tsx
 *
 * Interactive 5-step onboarding wizard for new CorvinOS operators.
 *
 * Features:
 * - Step 1: Introduction (what is CorvinOS?)
 * - Step 2: Install first skill
 * - Step 3: Configure learning
 * - Step 4: Execute skill demo
 * - Step 5: Verify completion
 *
 * Non-blocking: Users can skip at any time.
 * Completion badge shown on console.
 */

import React, { useState, useEffect } from 'react';
import './OnboardingWizard.css';

interface OnboardingStep {
  id: number;
  title: string;
  description: string;
  content: React.ReactNode;
  completed: boolean;
  optional?: boolean;
}

interface OnboardingState {
  currentStep: number;
  completedSteps: number[];
  wizardDismissed: boolean;
  completionBadgeShown: boolean;
}

const OnboardingWizard: React.FC = () => {
  const [state, setState] = useState<OnboardingState>({
    currentStep: 0,
    completedSteps: [],
    wizardDismissed: false,
    completionBadgeShown: false,
  });

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load state from localStorage
  useEffect(() => {
    const savedState = localStorage.getItem('corvin_onboarding_state');
    if (savedState) {
      try {
        setState(JSON.parse(savedState));
      } catch (e) {
        console.error('Failed to load onboarding state:', e);
      }
    }
  }, []);

  // Save state to localStorage
  useEffect(() => {
    localStorage.setItem('corvin_onboarding_state', JSON.stringify(state));
  }, [state]);

  const steps: OnboardingStep[] = [
    {
      id: 1,
      title: 'Welcome to CorvinOS',
      description: 'Learn what CorvinOS is and how it works',
      content: (
        <div className="onboarding-step-content">
          <h2>Welcome to CorvinOS</h2>
          <p>
            CorvinOS is an intelligent operating system that automates tasks using
            AI-powered skills. Each skill learns from feedback to make better decisions over time.
          </p>
          <div className="feature-list">
            <div className="feature">
              <h4>🚀 Skills</h4>
              <p>Reusable, learnable decision-making modules</p>
            </div>
            <div className="feature">
              <h4>📚 Learning Loop</h4>
              <p>Feedback improves skill performance automatically</p>
            </div>
            <div className="feature">
              <h4>💰 Cost Optimization</h4>
              <p>Skills learn to use cheaper models and APIs</p>
            </div>
            <div className="feature">
              <h4>🔐 Audit Trail</h4>
              <p>Complete, immutable record of all operations</p>
            </div>
          </div>
        </div>
      ),
      completed: false,
    },
    {
      id: 2,
      title: 'Install Your First Skill',
      description: 'Choose and install a skill to start with',
      content: (
        <div className="onboarding-step-content">
          <h2>Install Your First Skill</h2>
          <p>
            Skills are modules that perform specific tasks. Here are some popular ones to get started:
          </p>
          <div className="skill-options">
            <SkillOption
              id="assistant.quick_fix"
              name="Quick Fix"
              description="Identify and fix common errors in code"
              difficulty="Easy"
              onInstall={() => handleInstallSkill('assistant.quick_fix')}
              loading={loading}
            />
            <SkillOption
              id="assistant.cost_optimizer"
              name="Cost Optimizer"
              description="Optimize model selection for cost efficiency"
              difficulty="Medium"
              onInstall={() => handleInstallSkill('assistant.cost_optimizer')}
              loading={loading}
            />
            <SkillOption
              id="assistant.workflow_generator"
              name="Workflow Generator"
              description="Generate and optimize execution workflows"
              difficulty="Advanced"
              onInstall={() => handleInstallSkill('assistant.workflow_generator')}
              loading={loading}
            />
          </div>
        </div>
      ),
      completed: false,
    },
    {
      id: 3,
      title: 'Configure Learning',
      description: 'Enable the learning loop for automatic improvement',
      content: (
        <div className="onboarding-step-content">
          <h2>Enable Learning Loop</h2>
          <p>
            The learning loop allows skills to improve based on feedback. Enable it here:
          </p>
          <div className="learning-config">
            <label>
              <input
                type="checkbox"
                defaultChecked={true}
                onChange={(e) => handleLearningToggle(e.target.checked)}
              />
              Enable learning loop
            </label>
            <p className="description">
              Skills will automatically improve from feedback. Disable only for testing.
            </p>
            <div className="learning-settings">
              <h4>Learning Settings</h4>
              <label>
                Confidence Threshold: <input type="number" defaultValue="0.7" min="0" max="1" step="0.1" />
              </label>
              <label>
                Feedback Interval: <input type="number" defaultValue="10" min="1" /> hours
              </label>
              <label>
                Auto-Optimize: <input type="checkbox" defaultChecked={true} />
              </label>
            </div>
          </div>
        </div>
      ),
      completed: false,
    },
    {
      id: 4,
      title: 'Execute a Skill',
      description: 'Run your first skill and see it in action',
      content: (
        <div className="onboarding-step-content">
          <h2>Execute Your First Skill</h2>
          <p>
            Now let's run a skill. Enter some input and watch the skill execute:
          </p>
          <div className="skill-demo">
            <label>
              Input: <input type="text" placeholder='e.g., "def hello( " for Quick Fix' />
            </label>
            <button onClick={handleExecuteSkill} disabled={loading}>
              {loading ? 'Executing...' : 'Execute Skill'}
            </button>
            {error && <div className="error">{error}</div>}
            <div className="demo-output">
              <h4>Output:</h4>
              <p>Skills analyze your input and provide intelligent suggestions.</p>
              <p>Each execution is recorded and the skill learns from your feedback.</p>
            </div>
          </div>
        </div>
      ),
      completed: false,
    },
    {
      id: 5,
      title: 'Onboarding Complete',
      description: 'You are ready to use CorvinOS',
      content: (
        <div className="onboarding-step-content completion">
          <h2>🎉 Congratulations!</h2>
          <p>You have successfully completed the CorvinOS onboarding.</p>
          <div className="completion-checklist">
            <h3>What You've Learned:</h3>
            <ul>
              <li>✅ What CorvinOS is and how it works</li>
              <li>✅ How to install and execute skills</li>
              <li>✅ How learning loop improves skills</li>
              <li>✅ How to run your first skill</li>
            </ul>
          </div>
          <div className="next-steps">
            <h3>Next Steps:</h3>
            <ul>
              <li>📖 Read the API Reference</li>
              <li>🔧 Explore the Settings Panel</li>
              <li>📊 View your learning progress</li>
              <li>🚀 Install more skills from the marketplace</li>
            </ul>
          </div>
          <div className="completion-badge">
            <span className="badge">✓ ONBOARDING COMPLETE</span>
          </div>
        </div>
      ),
      completed: false,
    },
  ];

  const currentStepData = steps[state.currentStep];
  const isComplete = state.currentStep === steps.length - 1;

  const handleInstallSkill = async (skillId: string) => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch('/v1/skills/install', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${process.env.REACT_APP_API_KEY}`,
        },
        body: JSON.stringify({ skill_id: skillId }),
      });

      if (!response.ok) throw new Error('Failed to install skill');

      handleCompleteStep();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An error occurred');
    } finally {
      setLoading(false);
    }
  };

  const handleLearningToggle = async (enabled: boolean) => {
    try {
      const response = await fetch('/v1/learning/toggle', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${process.env.REACT_APP_API_KEY}`,
        },
        body: JSON.stringify({ enabled }),
      });

      if (!response.ok) throw new Error('Failed to update learning settings');

      handleCompleteStep();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An error occurred');
    }
  };

  const handleExecuteSkill = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch('/v1/skills/assistant.quick_fix/execute', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${process.env.REACT_APP_API_KEY}`,
        },
        body: JSON.stringify({
          input: 'def hello( ',
          tenant_id: '_default',
        }),
      });

      if (!response.ok) throw new Error('Failed to execute skill');

      handleCompleteStep();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An error occurred');
    } finally {
      setLoading(false);
    }
  };

  const handleCompleteStep = () => {
    setState((prev) => ({
      ...prev,
      completedSteps: [...new Set([...prev.completedSteps, state.currentStep])],
    }));

    if (!isComplete) {
      handleNextStep();
    }
  };

  const handleNextStep = () => {
    if (state.currentStep < steps.length - 1) {
      setState((prev) => ({
        ...prev,
        currentStep: prev.currentStep + 1,
      }));
    }
  };

  const handlePreviousStep = () => {
    if (state.currentStep > 0) {
      setState((prev) => ({
        ...prev,
        currentStep: prev.currentStep - 1,
      }));
    }
  };

  const handleDismiss = () => {
    setState((prev) => ({
      ...prev,
      wizardDismissed: true,
    }));
  };

  const handleReset = () => {
    setState({
      currentStep: 0,
      completedSteps: [],
      wizardDismissed: false,
      completionBadgeShown: false,
    });
  };

  if (state.wizardDismissed && !isComplete) {
    return (
      <div className="onboarding-minimized">
        <button onClick={handleReset} className="btn-resume">
          📚 Resume Onboarding
        </button>
      </div>
    );
  }

  return (
    <div className="onboarding-wizard">
      <div className="wizard-container">
        {/* Header */}
        <div className="wizard-header">
          <h1>CorvinOS Onboarding</h1>
          <div className="progress-bar">
            <div
              className="progress-fill"
              style={{
                width: `${((state.currentStep + 1) / steps.length) * 100}%`,
              }}
            />
          </div>
          <p className="progress-text">
            Step {state.currentStep + 1} of {steps.length}
          </p>
        </div>

        {/* Step Indicator */}
        <div className="step-indicators">
          {steps.map((step, idx) => (
            <div
              key={idx}
              className={`step-indicator ${idx === state.currentStep ? 'active' : ''} ${
                state.completedSteps.includes(idx) ? 'completed' : ''
              }`}
              onClick={() => setState((prev) => ({ ...prev, currentStep: idx }))}
            >
              <span className="step-number">{idx + 1}</span>
              <span className="step-label">{step.title}</span>
            </div>
          ))}
        </div>

        {/* Content */}
        <div className="wizard-content">
          <div className="step-content">{currentStepData.content}</div>
        </div>

        {/* Footer */}
        <div className="wizard-footer">
          <div className="button-group">
            <button
              onClick={handlePreviousStep}
              disabled={state.currentStep === 0}
              className="btn btn-secondary"
            >
              ← Previous
            </button>

            {!isComplete && (
              <button
                onClick={handleDismiss}
                className="btn btn-ghost"
              >
                Skip for Now
              </button>
            )}

            <button
              onClick={isComplete ? handleReset : handleNextStep}
              disabled={loading}
              className="btn btn-primary"
            >
              {isComplete ? '↺ Start Over' : 'Next →'}
            </button>
          </div>

          {state.completionBadgeShown && isComplete && (
            <div className="completion-notification">
              ✓ Onboarding complete! You can now access all CorvinOS features.
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

interface SkillOptionProps {
  id: string;
  name: string;
  description: string;
  difficulty: string;
  onInstall: () => void;
  loading: boolean;
}

const SkillOption: React.FC<SkillOptionProps> = ({
  id,
  name,
  description,
  difficulty,
  onInstall,
  loading,
}) => (
  <div className="skill-card">
    <h3>{name}</h3>
    <p className="description">{description}</p>
    <div className="difficulty">
      <span className={`badge-${difficulty.toLowerCase()}`}>{difficulty}</span>
    </div>
    <button
      onClick={onInstall}
      disabled={loading}
      className="btn btn-small"
    >
      {loading ? 'Installing...' : 'Install'}
    </button>
  </div>
);

export default OnboardingWizard;
