/**
 * Skill-Creator UI Panel — Console Quality subsystem
 *
 * Location: Console Settings → Quality → Skill Creator
 * Features:
 *  - Generate new skills via natural language prompts
 *  - Monitor generation progress (real-time status polling)
 *  - List and manage generated skills
 *  - View quality scores and iteration history
 */

import React, { useState, useEffect } from "react";
import { useState as useState2 } from "react";

interface GenerationRun {
  run_id: string;
  status: "pending" | "running" | "success" | "failed";
  phase: string;
  progress: number;
  message: string;
  skill?: SkillInfo;
}

interface SkillInfo {
  name: string;
  purpose: string;
  scope: "assistant" | "project" | "global";
  quality: number;
  iterations: number;
  dependencies: string[];
}

interface GeneratedSkill {
  name: string;
  file: string;
  created_at: string;
}

export const SkillCreatorPanel: React.FC = () => {
  const [userRequest, setUserRequest] = useState("");
  const [isGenerating, setIsGenerating] = useState(false);
  const [currentRun, setCurrentRun] = useState<GenerationRun | null>(null);
  const [generatedSkills, setGeneratedSkills] = useState<GeneratedSkill[]>([]);
  const [error, setError] = useState<string | null>(null);

  // Load existing skills on mount
  useEffect(() => {
    loadGeneratedSkills();
  }, []);

  // Poll generation status
  useEffect(() => {
    if (!currentRun || currentRun.status !== "running") return;

    const interval = setInterval(() => {
      checkGenerationStatus(currentRun.run_id);
    }, 1000);

    return () => clearInterval(interval);
  }, [currentRun]);

  const handleGenerate = async () => {
    if (!userRequest.trim()) {
      setError("Please enter a skill description");
      return;
    }

    setError(null);
    setIsGenerating(true);

    try {
      const response = await fetch("/api/quality/skill-creator/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_request: userRequest,
          async: true, // Always use async for UI
        }),
      });

      if (!response.ok) {
        throw new Error(`Generation failed: ${response.statusText}`);
      }

      const data = await response.json();
      setCurrentRun({
        run_id: data.run_id,
        status: "running",
        phase: "planning",
        progress: 0,
        message: "Starting skill generation...",
      });

      setUserRequest("");

      // Initial status check
      setTimeout(() => checkGenerationStatus(data.run_id), 500);
    } catch (err) {
      setError(`Error: ${err instanceof Error ? err.message : String(err)}`);
      setIsGenerating(false);
    }
  };

  const checkGenerationStatus = async (runId: string) => {
    try {
      const response = await fetch(
        `/api/quality/skill-creator/status/${runId}`
      );

      if (!response.ok) {
        throw new Error("Status check failed");
      }

      const data: GenerationRun = await response.json();
      setCurrentRun(data);

      if (data.status === "success" || data.status === "failed") {
        setIsGenerating(false);

        if (data.status === "success") {
          // Refresh skills list
          loadGeneratedSkills();
        }
      }
    } catch (err) {
      console.error("Status check error:", err);
    }
  };

  const loadGeneratedSkills = async () => {
    try {
      const response = await fetch("/api/quality/skill-creator/skills");

      if (!response.ok) {
        console.error("Failed to load skills");
        return;
      }

      const data = await response.json();
      setGeneratedSkills(data.skills);
    } catch (err) {
      console.error("Error loading skills:", err);
    }
  };

  return (
    <div className="skill-creator-panel">
      <div className="skill-creator-header">
        <h2>🧠 Skill Creator</h2>
        <p className="subtitle">
          Generate reusable skills via natural language. The system uses
          Loss-Driven Development to refine skills until zero flaws are found.
        </p>
      </div>

      {/* Generation Form */}
      <div className="generation-form">
        <label htmlFor="user-request">What skill do you want to create?</label>
        <textarea
          id="user-request"
          className="request-input"
          placeholder="e.g., 'erzeuge einen Skill der JSON-Dateien validiert' or 'create a skill that analyzes code coverage'"
          value={userRequest}
          onChange={(e) => setUserRequest(e.target.value)}
          disabled={isGenerating}
          rows={3}
        />
        <button
          className="generate-button"
          onClick={handleGenerate}
          disabled={isGenerating || !userRequest.trim()}
        >
          {isGenerating ? "Generating..." : "Generate Skill"}
        </button>
      </div>

      {/* Error Display */}
      {error && (
        <div className="error-message">
          <strong>Error:</strong> {error}
        </div>
      )}

      {/* Generation Progress */}
      {currentRun && (
        <div className="generation-progress">
          <h3>Generation Progress</h3>
          <div className="progress-info">
            <span className="run-id">Run ID: {currentRun.run_id}</span>
            <span className="status">{currentRun.status.toUpperCase()}</span>
            <span className="phase">Phase: {currentRun.phase}</span>
          </div>
          <div className="progress-bar">
            <div
              className="progress-fill"
              style={{ width: `${currentRun.progress}%` }}
            />
          </div>
          <p className="progress-message">{currentRun.message}</p>

          {currentRun.status === "success" && currentRun.skill && (
            <div className="success-box">
              <h4>✅ Skill Generated Successfully</h4>
              <div className="skill-details">
                <div className="detail-row">
                  <strong>Name:</strong> <code>{currentRun.skill.name}</code>
                </div>
                <div className="detail-row">
                  <strong>Purpose:</strong> {currentRun.skill.purpose}
                </div>
                <div className="detail-row">
                  <strong>Scope:</strong> {currentRun.skill.scope}
                </div>
                <div className="detail-row">
                  <strong>Quality:</strong>
                  <span className="quality-badge">
                    {(currentRun.skill.quality * 100).toFixed(0)}%
                  </span>
                </div>
                <div className="detail-row">
                  <strong>LDD Iterations:</strong> {currentRun.skill.iterations}
                </div>
                {currentRun.skill.dependencies.length > 0 && (
                  <div className="detail-row">
                    <strong>Dependencies:</strong>
                    <code>{currentRun.skill.dependencies.join(", ")}</code>
                  </div>
                )}
              </div>
              <p className="success-note">
                The skill is now available in ~/.claude/skills/ and will be
                injected into future turns.
              </p>
            </div>
          )}

          {currentRun.status === "failed" && (
            <div className="error-box">
              <h4>❌ Generation Failed</h4>
              <p>{currentRun.message}</p>
            </div>
          )}
        </div>
      )}

      {/* Generated Skills List */}
      <div className="generated-skills">
        <h3>Generated Skills ({generatedSkills.length})</h3>

        {generatedSkills.length === 0 ? (
          <p className="empty-state">
            No skills generated yet. Create your first skill above!
          </p>
        ) : (
          <div className="skills-table">
            <table>
              <thead>
                <tr>
                  <th>Skill Name</th>
                  <th>Created</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {generatedSkills.map((skill) => (
                  <tr key={skill.name}>
                    <td className="skill-name">
                      <code>{skill.name}</code>
                    </td>
                    <td className="created-at">
                      {new Date(skill.created_at).toLocaleDateString()}
                    </td>
                    <td className="actions">
                      <button className="action-btn view-btn">View</button>
                      <button className="action-btn test-btn">Test</button>
                      <button className="action-btn delete-btn">Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <style jsx>{`
        .skill-creator-panel {
          padding: 20px;
          background: var(--bg-secondary);
          border-radius: 8px;
          max-width: 900px;
        }

        .skill-creator-header h2 {
          margin: 0 0 8px 0;
          font-size: 1.5rem;
        }

        .subtitle {
          margin: 0 0 20px 0;
          color: var(--text-secondary);
          font-size: 0.95rem;
        }

        .generation-form {
          margin-bottom: 30px;
          padding: 15px;
          background: var(--bg-primary);
          border-radius: 6px;
        }

        .generation-form label {
          display: block;
          margin-bottom: 8px;
          font-weight: 500;
        }

        .request-input {
          width: 100%;
          padding: 10px;
          border: 1px solid var(--border-color);
          border-radius: 4px;
          font-family: inherit;
          font-size: 1rem;
          margin-bottom: 12px;
          background: var(--bg-secondary);
          color: var(--text-primary);
        }

        .request-input:disabled {
          opacity: 0.6;
        }

        .generate-button {
          padding: 10px 20px;
          background: var(--color-primary);
          color: white;
          border: none;
          border-radius: 4px;
          font-weight: 600;
          cursor: pointer;
          transition: opacity 0.2s;
        }

        .generate-button:hover:not(:disabled) {
          opacity: 0.9;
        }

        .generate-button:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }

        .error-message {
          padding: 12px;
          background: var(--color-error-light);
          color: var(--color-error);
          border-radius: 4px;
          margin-bottom: 20px;
        }

        .generation-progress {
          padding: 15px;
          background: var(--bg-primary);
          border-radius: 6px;
          margin-bottom: 30px;
          border-left: 4px solid var(--color-primary);
        }

        .progress-info {
          display: flex;
          gap: 20px;
          margin-bottom: 12px;
          font-size: 0.9rem;
        }

        .progress-bar {
          width: 100%;
          height: 8px;
          background: var(--bg-secondary);
          border-radius: 4px;
          overflow: hidden;
          margin-bottom: 12px;
        }

        .progress-fill {
          height: 100%;
          background: var(--color-primary);
          transition: width 0.3s ease;
        }

        .progress-message {
          margin: 0;
          color: var(--text-secondary);
          font-size: 0.9rem;
        }

        .success-box,
        .error-box {
          margin-top: 15px;
          padding: 12px;
          border-radius: 4px;
        }

        .success-box {
          background: var(--color-success-light);
          border-left: 4px solid var(--color-success);
        }

        .error-box {
          background: var(--color-error-light);
          border-left: 4px solid var(--color-error);
        }

        .success-box h4,
        .error-box h4 {
          margin: 0 0 12px 0;
        }

        .skill-details {
          font-size: 0.9rem;
        }

        .detail-row {
          display: flex;
          justify-content: space-between;
          padding: 6px 0;
          border-bottom: 1px solid rgba(255, 255, 255, 0.1);
        }

        .detail-row strong {
          min-width: 150px;
        }

        .detail-row code {
          background: rgba(255, 255, 255, 0.1);
          padding: 2px 6px;
          border-radius: 3px;
        }

        .quality-badge {
          display: inline-block;
          padding: 4px 12px;
          background: var(--color-primary);
          color: white;
          border-radius: 12px;
          font-weight: 600;
        }

        .success-note {
          margin: 12px 0 0 0;
          font-size: 0.85rem;
          color: var(--text-secondary);
          font-style: italic;
        }

        .generated-skills {
          padding: 15px;
          background: var(--bg-primary);
          border-radius: 6px;
        }

        .generated-skills h3 {
          margin: 0 0 15px 0;
        }

        .empty-state {
          text-align: center;
          color: var(--text-secondary);
          padding: 20px;
        }

        .skills-table {
          overflow-x: auto;
        }

        .skills-table table {
          width: 100%;
          border-collapse: collapse;
          font-size: 0.9rem;
        }

        .skills-table th {
          text-align: left;
          padding: 10px;
          background: var(--bg-secondary);
          border-bottom: 2px solid var(--border-color);
        }

        .skills-table td {
          padding: 10px;
          border-bottom: 1px solid var(--border-color);
        }

        .skill-name code {
          background: var(--bg-secondary);
          padding: 2px 6px;
          border-radius: 3px;
        }

        .actions {
          display: flex;
          gap: 8px;
        }

        .action-btn {
          padding: 6px 12px;
          border: 1px solid var(--border-color);
          background: var(--bg-secondary);
          border-radius: 3px;
          cursor: pointer;
          font-size: 0.85rem;
          transition: all 0.2s;
        }

        .action-btn:hover {
          background: var(--border-color);
        }

        .delete-btn:hover {
          color: var(--color-error);
          border-color: var(--color-error);
        }
      `}</style>
    </div>
  );
};

export default SkillCreatorPanel;
