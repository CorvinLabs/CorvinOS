/**
 * VideoOrchestrationTimeline.tsx
 *
 * Timeline UI for orchestration visualization
 * - Storyboard cards for each frame (TTS, Screenshot, FFmpeg, YouTube)
 * - Progress tracking with executor status
 * - Worker status display + error messages
 */

import React, { useState, useEffect } from 'react';
import './VideoOrchestrationTimeline.css';

// Types
export interface FrameState {
  frameId: string;
  workerType: 'tts' | 'screenshot' | 'ffmpeg' | 'youtube';
  status: 'pending' | 'running' | 'completed' | 'error';
  progress?: number;
  errorMessage?: string;
  metadata?: Record<string, any>;
}

export interface ExecutorStatus {
  totalFrames: number;
  completedFrames: number;
  currentFrameId?: string;
  isRunning: boolean;
  estimatedTimeRemaining?: number;
  overallProgress: number;
}

export interface VideoOrchestrationTimelineProps {
  frames: FrameState[];
  executorStatus: ExecutorStatus;
  onFrameClick?: (frameId: string) => void;
  onRetry?: (frameId: string) => void;
}

/**
 * Get worker icon based on worker type
 */
function getWorkerIcon(workerType: string): string {
  const icons: Record<string, string> = {
    tts: '🔊',
    screenshot: '📷',
    ffmpeg: '🎬',
    youtube: '📺',
  };
  return icons[workerType] || '⚙️';
}

/**
 * Get status color
 */
function getStatusColor(status: string): string {
  const colors: Record<string, string> = {
    pending: '#cccccc',
    running: '#3b82f6',
    completed: '#10b981',
    error: '#ef4444',
  };
  return colors[status] || '#9ca3af';
}

/**
 * StoryboardCard component
 */
const StoryboardCard: React.FC<{
  frame: FrameState;
  icon: string;
  onFrameClick?: (frameId: string) => void;
  onRetry?: (frameId: string) => void;
}> = ({ frame, icon, onFrameClick, onRetry }) => {
  return (
    <div
      className={`storyboard-card storyboard-card--${frame.status}`}
      onClick={() => onFrameClick?.(frame.frameId)}
      style={{
        backgroundColor: getStatusColor(frame.status),
        cursor: 'pointer',
      }}
    >
      <div className="storyboard-card__icon">{icon}</div>
      <div className="storyboard-card__content">
        <div className="storyboard-card__title">Frame {frame.frameId}</div>
        <div className="storyboard-card__status">{frame.status}</div>
        {frame.progress !== undefined && frame.status === 'running' && (
          <div className="storyboard-card__progress">
            <div
              className="storyboard-card__progress-bar"
              style={{ width: `${frame.progress}%` }}
            />
          </div>
        )}
        {frame.errorMessage && (
          <div className="storyboard-card__error">{frame.errorMessage}</div>
        )}
        {frame.status === 'error' && onRetry && (
          <button
            className="storyboard-card__retry-btn"
            onClick={(e) => {
              e.stopPropagation();
              onRetry(frame.frameId);
            }}
          >
            Retry
          </button>
        )}
      </div>
    </div>
  );
};

/**
 * ProgressTracker component
 */
const ProgressTracker: React.FC<{ status: ExecutorStatus }> = ({ status }) => {
  const percentage = (status.completedFrames / status.totalFrames) * 100;

  return (
    <div className="progress-tracker">
      <div className="progress-tracker__header">
        <span className="progress-tracker__title">Overall Progress</span>
        <span className="progress-tracker__percentage">{percentage.toFixed(0)}%</span>
      </div>
      <div className="progress-tracker__bar-container">
        <div
          className="progress-tracker__bar"
          style={{ width: `${percentage}%` }}
        />
      </div>
      <div className="progress-tracker__stats">
        <span>
          {status.completedFrames} / {status.totalFrames} frames
        </span>
        {status.estimatedTimeRemaining && (
          <span>
            ETA: {Math.ceil(status.estimatedTimeRemaining / 1000)}s
          </span>
        )}
        <span className="progress-tracker__status">
          {status.isRunning ? '🟢 Running' : '⏸️ Paused'}
        </span>
      </div>
    </div>
  );
};

/**
 * Main Timeline Component
 */
export const VideoOrchestrationTimeline: React.FC<
  VideoOrchestrationTimelineProps
> = ({ frames, executorStatus, onFrameClick, onRetry }) => {
  return (
    <div className="orchestration-timeline">
      <div className="orchestration-timeline__header">
        <h2>Video Orchestration Timeline</h2>
      </div>

      <div className="orchestration-timeline__progress">
        <ProgressTracker status={executorStatus} />
      </div>

      <div className="orchestration-timeline__frames">
        {frames.map((frame, idx) => (
          <div key={frame.frameId} className="orchestration-timeline__frame-wrapper">
            <div className="orchestration-timeline__frame-number">#{idx + 1}</div>
            <StoryboardCard
              frame={frame}
              icon={getWorkerIcon(frame.workerType)}
              onFrameClick={onFrameClick}
              onRetry={onRetry}
            />
          </div>
        ))}
      </div>

      <div className="orchestration-timeline__footer">
        <p className="orchestration-timeline__hint">
          Click on a card to view details or retry failed frames
        </p>
      </div>
    </div>
  );
};

export default VideoOrchestrationTimeline;
