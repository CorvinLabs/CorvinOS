/**
 * Video Producer Learning Components
 *
 * Phase 4b React components for feedback collection, confidence visualization,
 * and model performance tracking.
 */

export { FeedbackCollector, type FeedbackCollectorProps, type FeedbackSubmission } from './FeedbackCollector';
export { ConfidenceMetrics } from './ConfidenceMetrics';
export { ModelPerformance } from './ModelPerformance';

/**
 * Combined Learning Dashboard Component
 *
 * Integrates all learning components into a single dashboard panel.
 */
import React, { useState } from 'react';
import FeedbackCollector from './FeedbackCollector';
import ConfidenceMetrics from './ConfidenceMetrics';
import ModelPerformance from './ModelPerformance';

export interface VideoProducerLearningDashboardProps {
  jobId: string;
  sceneId?: string;
}

export const VideoProducerLearningDashboard: React.FC<VideoProducerLearningDashboardProps> = ({
  jobId,
  sceneId = 's01',
}) => {
  const [activeTab, setActiveTab] = useState<'feedback' | 'confidence' | 'models'>('feedback');

  return (
    <div className="video-producer-learning-dashboard space-y-4">
      {/* Tab Navigation */}
      <div className="flex gap-2 border-b border-gray-200">
        {[
          { value: 'feedback', label: 'Feedback', icon: '📝' },
          { value: 'confidence', label: 'Confidence', icon: '📊' },
          { value: 'models', label: 'Models', icon: '🤖' },
        ].map((tab) => (
          <button
            key={tab.value}
            onClick={() => setActiveTab(tab.value as any)}
            className={`px-4 py-2 font-medium transition-colors ${
              activeTab === tab.value
                ? 'border-b-2 border-blue-500 text-blue-600'
                : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            <span className="mr-2">{tab.icon}</span>
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      <div className="mt-4">
        {activeTab === 'feedback' && (
          <FeedbackCollector jobId={jobId} sceneId={sceneId} />
        )}
        {activeTab === 'confidence' && <ConfidenceMetrics />}
        {activeTab === 'models' && <ModelPerformance />}
      </div>
    </div>
  );
};

export default VideoProducerLearningDashboard;
