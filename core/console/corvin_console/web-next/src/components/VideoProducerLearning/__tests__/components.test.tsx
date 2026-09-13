/**
 * Tests for Video Producer Learning Components (Phase 4b)
 *
 * Unit tests for:
 - FeedbackCollector.tsx (5 tests)
 - ConfidenceMetrics.tsx (3 tests)
 - ModelPerformance.tsx (2 tests)
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import FeedbackCollector from '../FeedbackCollector';
import ConfidenceMetrics from '../ConfidenceMetrics';
import ModelPerformance from '../ModelPerformance';

// Mock fetch
global.fetch = jest.fn();

describe('FeedbackCollector Component', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('renders with title and form elements', () => {
    render(<FeedbackCollector jobId="job1" sceneId="s01" />);

    expect(screen.getByText('Provide Feedback')).toBeInTheDocument();
    expect(screen.getByText('Feedback Type')).toBeInTheDocument();
    expect(screen.getByText(/Rating:/)).toBeInTheDocument();
    expect(screen.getByText('Notes (Optional)')).toBeInTheDocument();
  });

  test('allows rating selection 1-5', async () => {
    render(<FeedbackCollector jobId="job1" sceneId="s01" />);

    const ratingButton4 = screen.getByRole('button', { name: '4' });
    fireEvent.click(ratingButton4);

    expect(ratingButton4).toHaveClass('bg-blue-500');
  });

  test('allows feedback type selection', async () => {
    render(<FeedbackCollector jobId="job1" sceneId="s01" />);

    const typeSelect = screen.getByDisplayValue('Video Quality');
    expect(typeSelect).toBeInTheDocument();
  });

  test('allows notes input with character limit', async () => {
    render(<FeedbackCollector jobId="job1" sceneId="s01" />);

    const notesInput = screen.getByPlaceholderText(/voice is too fast/);
    await userEvent.type(notesInput, 'Great quality overall');

    expect(notesInput).toHaveValue('Great quality overall');
  });

  test('submits feedback and shows success message', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ success: true }),
    });

    const onSubmitted = jest.fn();
    render(
      <FeedbackCollector jobId="job1" sceneId="s01" onSubmitted={onSubmitted} />
    );

    const submitButton = screen.getByRole('button', { name: 'Submit Feedback' });
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(screen.getByText(/Feedback recorded!/)).toBeInTheDocument();
    });
  });
});

describe('ConfidenceMetrics Component', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('loads and displays confidence metrics', async () => {
    const mockMetrics = {
      slide_renderer: {
        overall_score: 0.85,
        is_converged: true,
        convergence_rate: 0.85,
        metrics: {
          slide_quality: {
            confidence: 0.85,
            samples: 10,
            variance: 0.08,
            last_updated: '2026-09-13T12:00:00Z',
          },
        },
      },
    };

    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockMetrics,
    });

    render(<ConfidenceMetrics />);

    await waitFor(() => {
      expect(screen.getByText('slide_renderer')).toBeInTheDocument();
      expect(screen.getByText('85%')).toBeInTheDocument();
    });
  });

  test('shows convergence status for workers', async () => {
    const mockMetrics = {
      voice_synthesizer: {
        overall_score: 0.75,
        is_converged: false,
        convergence_rate: 0.5,
        metrics: {
          audio_quality: {
            confidence: 0.75,
            samples: 5,
            variance: 0.15,
            last_updated: '2026-09-13T12:00:00Z',
          },
        },
      },
    };

    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockMetrics,
    });

    render(<ConfidenceMetrics />);

    await waitFor(() => {
      expect(screen.getByText('voice_synthesizer')).toBeInTheDocument();
      expect(screen.getByText('○ Learning')).toBeInTheDocument();
    });
  });

  test('displays summary statistics', async () => {
    const mockMetrics = {
      worker1: {
        overall_score: 0.9,
        is_converged: true,
        convergence_rate: 0.9,
        metrics: {},
      },
      worker2: {
        overall_score: 0.7,
        is_converged: false,
        convergence_rate: 0.7,
        metrics: {},
      },
    };

    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockMetrics,
    });

    render(<ConfidenceMetrics />);

    await waitFor(() => {
      expect(screen.getByText('2')).toBeInTheDocument(); // Workers Tracked
    });
  });
});

describe('ModelPerformance Component', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('loads and displays model statistics', async () => {
    const mockData = {
      total_decisions: 50,
      exploration_rate: 0.1,
      by_duration: {
        '1min': {
          selected_model: 'claude-opus',
          models: {
            'gpt-4': {
              win_rate: 0.75,
              average_rating: 0.75,
              attempts: 10,
              wins: 8,
            },
            'claude-opus': {
              win_rate: 0.9,
              average_rating: 0.9,
              attempts: 10,
              wins: 9,
            },
            'claude-sonnet': {
              win_rate: 0.5,
              average_rating: 0.5,
              attempts: 10,
              wins: 5,
            },
          },
        },
      },
    };

    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockData,
    });

    render(<ModelPerformance />);

    await waitFor(() => {
      expect(screen.getByText('50')).toBeInTheDocument(); // Total Decisions
      expect(screen.getByText('1min Videos')).toBeInTheDocument();
    });
  });

  test('displays model comparison with win rates', async () => {
    const mockData = {
      total_decisions: 20,
      exploration_rate: 0.1,
      by_duration: {
        '1min': {
          selected_model: 'gpt-4',
          models: {
            'gpt-4': {
              win_rate: 0.8,
              average_rating: 0.8,
              attempts: 5,
              wins: 4,
            },
          },
        },
      },
    };

    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockData,
    });

    render(<ModelPerformance />);

    await waitFor(() => {
      expect(screen.getByText('gpt-4')).toBeInTheDocument();
      expect(screen.getByText('80%')).toBeInTheDocument();
    });
  });
});
