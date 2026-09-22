/**
 * HelpMenu.tsx
 *
 * Comprehensive help menu with integrated documentation, videos, and runbooks.
 *
 * Features:
 * - Access to setup guides and API references
 * - Troubleshooting search
 * - Video tutorials (with playback)
 * - Incident response runbook
 * - Quick search across all resources
 * - Community links and support contacts
 */

import React, { useState } from 'react';
import './HelpMenu.css';

interface HelpResource {
  id: string;
  title: string;
  category: 'guide' | 'api' | 'troubleshooting' | 'video' | 'incident' | 'faq';
  description: string;
  url?: string;
  content?: string;
  duration?: string; // For videos
  difficulty?: 'beginner' | 'intermediate' | 'advanced';
}

interface HelpMenuState {
  isOpen: boolean;
  selectedCategory: string | null;
  searchQuery: string;
  selectedResource: HelpResource | null;
}

const HelpMenu: React.FC = () => {
  const [state, setState] = useState<HelpMenuState>({
    isOpen: false,
    selectedCategory: null,
    searchQuery: '',
    selectedResource: null,
  });

  const helpResources: HelpResource[] = [
    // Setup & Installation
    {
      id: 'setup-guide',
      title: 'Phase 1: Setup Guide',
      category: 'guide',
      description: 'Complete installation and initial configuration guide',
      url: '/docs/onboarding/PHASE1_SETUP_GUIDE.md',
      difficulty: 'beginner',
    },
    {
      id: 'api-reference',
      title: 'API Reference',
      category: 'api',
      description: 'Complete API endpoints, authentication, and examples',
      url: '/docs/onboarding/PHASE1_API_REFERENCE.md',
      difficulty: 'intermediate',
    },

    // Troubleshooting
    {
      id: 'troubleshooting',
      title: 'Troubleshooting Guide',
      category: 'troubleshooting',
      description: '20+ common issues and solutions',
      url: '/docs/onboarding/PHASE1_TROUBLESHOOTING.md',
      difficulty: 'intermediate',
    },

    // Videos
    {
      id: 'video-install',
      title: 'Installing Your First Skill',
      category: 'video',
      description: 'Learn how to install and configure a skill',
      duration: '2:15',
      difficulty: 'beginner',
    },
    {
      id: 'video-cost',
      title: 'Understanding Cost Insights',
      category: 'video',
      description: 'Understand cost tracking and optimization',
      duration: '2:30',
      difficulty: 'intermediate',
    },
    {
      id: 'video-learning',
      title: 'Using the Learning Loop',
      category: 'video',
      description: 'How feedback improves skill performance',
      duration: '2:45',
      difficulty: 'beginner',
    },

    // Incident Response
    {
      id: 'incident-runbook',
      title: 'Incident Response Runbook',
      category: 'incident',
      description: '12 critical incidents with step-by-step solutions',
      url: '/docs/onboarding/INCIDENT_RESPONSE_RUNBOOK.md',
      difficulty: 'advanced',
    },
    {
      id: 'oncall-procedures',
      title: 'On-Call Procedures',
      category: 'incident',
      description: 'On-call responsibilities, escalation, and handoff',
      url: '/docs/onboarding/ON_CALL_PROCEDURES.md',
      difficulty: 'intermediate',
    },

    // FAQ
    {
      id: 'faq-basics',
      title: 'FAQ: Basics',
      category: 'faq',
      description: 'Frequently asked questions about CorvinOS',
      difficulty: 'beginner',
    },
    {
      id: 'faq-advanced',
      title: 'FAQ: Advanced Topics',
      category: 'faq',
      description: 'Advanced features and customization',
      difficulty: 'advanced',
    },
  ];

  const categories = [
    { id: 'guide', label: '📖 Guides', icon: '📖' },
    { id: 'api', label: '⚙️ API Reference', icon: '⚙️' },
    { id: 'troubleshooting', label: '🔧 Troubleshooting', icon: '🔧' },
    { id: 'video', label: '🎥 Videos', icon: '🎥' },
    { id: 'incident', label: '🚨 Incidents', icon: '🚨' },
    { id: 'faq', label: '❓ FAQ', icon: '❓' },
  ];

  const filteredResources = state.searchQuery
    ? helpResources.filter(
        (resource) =>
          resource.title.toLowerCase().includes(state.searchQuery.toLowerCase()) ||
          resource.description.toLowerCase().includes(state.searchQuery.toLowerCase())
      )
    : state.selectedCategory
    ? helpResources.filter((resource) => resource.category === state.selectedCategory)
    : helpResources;

  const handleToggle = () => {
    setState((prev) => ({
      ...prev,
      isOpen: !prev.isOpen,
      selectedCategory: null,
      searchQuery: '',
      selectedResource: null,
    }));
  };

  const handleCategorySelect = (categoryId: string) => {
    setState((prev) => ({
      ...prev,
      selectedCategory: categoryId,
      searchQuery: '',
      selectedResource: null,
    }));
  };

  const handleResourceSelect = (resource: HelpResource) => {
    setState((prev) => ({
      ...prev,
      selectedResource: resource,
    }));
  };

  const handleSearch = (query: string) => {
    setState((prev) => ({
      ...prev,
      searchQuery: query,
      selectedCategory: null,
      selectedResource: null,
    }));
  };

  const handleOpenResource = (resource: HelpResource) => {
    if (resource.url) {
      window.open(resource.url, '_blank');
    } else if (resource.category === 'video') {
      // Open video player modal
      setState((prev) => ({
        ...prev,
        selectedResource: resource,
      }));
    }
  };

  const handleBackToList = () => {
    setState((prev) => ({
      ...prev,
      selectedResource: null,
    }));
  };

  if (!state.isOpen) {
    return (
      <button
        onClick={handleToggle}
        className="help-button"
        title="Open Help Menu"
      >
        ❓
      </button>
    );
  }

  return (
    <div className="help-menu">
      <div className="help-panel">
        {/* Header */}
        <div className="help-header">
          <h2>Help & Documentation</h2>
          <button
            onClick={handleToggle}
            className="close-button"
            title="Close Help Menu"
          >
            ✕
          </button>
        </div>

        {state.selectedResource ? (
          // Resource Detail View
          <ResourceDetailView
            resource={state.selectedResource}
            onBack={handleBackToList}
          />
        ) : (
          // Main Help View
          <>
            {/* Search */}
            <div className="search-section">
              <input
                type="text"
                placeholder="Search documentation..."
                className="search-input"
                value={state.searchQuery}
                onChange={(e) => handleSearch(e.target.value)}
              />
            </div>

            {/* Categories */}
            {!state.searchQuery && (
              <div className="categories-section">
                <div className="categories-grid">
                  {categories.map((category) => (
                    <button
                      key={category.id}
                      onClick={() => handleCategorySelect(category.id)}
                      className={`category-button ${
                        state.selectedCategory === category.id ? 'active' : ''
                      }`}
                    >
                      <span className="icon">{category.icon}</span>
                      <span className="label">{category.label}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Resources List */}
            <div className="resources-list">
              {filteredResources.length === 0 ? (
                <div className="no-results">
                  <p>No resources found.</p>
                  <p className="hint">Try a different search or category.</p>
                </div>
              ) : (
                filteredResources.map((resource) => (
                  <ResourceCard
                    key={resource.id}
                    resource={resource}
                    onSelect={() => handleResourceSelect(resource)}
                    onOpen={() => handleOpenResource(resource)}
                  />
                ))
              )}
            </div>

            {/* Quick Links */}
            <div className="quick-links-section">
              <h3>Quick Links</h3>
              <a href="https://github.com/CorvinLabs/CorvinOS/issues" target="_blank" rel="noopener noreferrer">
                💬 Report Issue
              </a>
              <a href="https://corvinlabs.slack.com" target="_blank" rel="noopener noreferrer">
                🔔 Slack Channel
              </a>
              <a href="mailto:support@corvinlabs.io">
                ✉️ Email Support
              </a>
            </div>
          </>
        )}
      </div>
    </div>
  );
};

interface ResourceCardProps {
  resource: HelpResource;
  onSelect: () => void;
  onOpen: () => void;
}

const ResourceCard: React.FC<ResourceCardProps> = ({ resource, onSelect, onOpen }) => {
  const categoryIcons: Record<string, string> = {
    guide: '📖',
    api: '⚙️',
    troubleshooting: '🔧',
    video: '🎥',
    incident: '🚨',
    faq: '❓',
  };

  const difficultyBadgeColor: Record<string, string> = {
    beginner: 'badge-beginner',
    intermediate: 'badge-intermediate',
    advanced: 'badge-advanced',
  };

  return (
    <div className="resource-card">
      <div className="resource-header">
        <div className="resource-icon-title">
          <span className="icon">{categoryIcons[resource.category]}</span>
          <h3>{resource.title}</h3>
        </div>
        {resource.difficulty && (
          <span className={`difficulty-badge ${difficultyBadgeColor[resource.difficulty]}`}>
            {resource.difficulty}
          </span>
        )}
      </div>

      <p className="resource-description">{resource.description}</p>

      {resource.duration && (
        <p className="resource-duration">⏱️ {resource.duration}</p>
      )}

      <div className="resource-actions">
        <button onClick={onSelect} className="btn btn-secondary">
          Preview
        </button>
        <button onClick={onOpen} className="btn btn-primary">
          {resource.category === 'video' ? '▶️ Watch' : '📖 Read'}
        </button>
      </div>
    </div>
  );
};

interface ResourceDetailViewProps {
  resource: HelpResource;
  onBack: () => void;
}

const ResourceDetailView: React.FC<ResourceDetailViewProps> = ({ resource, onBack }) => {
  return (
    <div className="resource-detail-view">
      <div className="detail-header">
        <button onClick={onBack} className="back-button">
          ← Back
        </button>
        <h2>{resource.title}</h2>
      </div>

      {resource.category === 'video' ? (
        <div className="video-player">
          <div className="video-placeholder">
            <h3>📹 Video: {resource.title}</h3>
            <p>{resource.description}</p>
            <p>Duration: {resource.duration}</p>
            <div className="video-stub">
              [Video player would display here: {resource.id}]
            </div>
            <p className="hint">
              This video tutorial is available in the full release.
              For now, see the setup guide and API reference above.
            </p>
          </div>
        </div>
      ) : (
        <div className="document-view">
          <div className="document-meta">
            <span className="category">{resource.category}</span>
            {resource.difficulty && (
              <span className={`difficulty difficulty-${resource.difficulty}`}>
                {resource.difficulty}
              </span>
            )}
          </div>

          <p className="document-description">{resource.description}</p>

          {resource.url && (
            <div className="document-actions">
              <a href={resource.url} target="_blank" rel="noopener noreferrer" className="btn btn-primary">
                📖 Open Full Document
              </a>
            </div>
          )}

          <div className="document-content">
            {resource.content && <div dangerouslySetInnerHTML={{ __html: resource.content }} />}
            {!resource.content && (
              <div className="placeholder">
                <p>
                  This document is loaded from{' '}
                  <code>{resource.url}</code>
                </p>
                <p>Click "Open Full Document" above to view the complete content.</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default HelpMenu;
