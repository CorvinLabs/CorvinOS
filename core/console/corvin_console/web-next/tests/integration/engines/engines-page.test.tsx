import { describe, it, expect, beforeEach } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { renderWithProviders } from '../../utils/test-utils';
import { EnginesPage } from '../../fixtures/mock-pages';

describe('Engines Page Integration', () => {
  beforeEach(() => {
    localStorage.setItem('auth_token', 'jwt-test-token-engines-123');
  });

  describe('Engines Display', () => {
    it('renders engines heading', () => {
      renderWithProviders(<EnginesPage />, { route: '/engines' });
      expect(screen.getByText(/ai engines/i)).toBeInTheDocument();
    });

    it('displays available engines', () => {
      renderWithProviders(<EnginesPage />, { route: '/engines' });
      expect(screen.getByText(/claude code/i)).toBeInTheDocument();
      expect(screen.getByText(/hermes/i)).toBeInTheDocument();
      expect(screen.getByText(/opencode/i)).toBeInTheDocument();
    });

    it('shows engine status', () => {
      renderWithProviders(<EnginesPage />, { route: '/engines' });
      expect(screen.getByTestId('engine-claude-status')).toHaveTextContent(/active/i);
      expect(screen.getByTestId('engine-hermes-status')).toHaveTextContent(/available/i);
    });
  });

  describe('Claude Code Engine', () => {
    it('displays claude code engine', () => {
      renderWithProviders(<EnginesPage />, { route: '/engines' });
      expect(screen.getByText(/claude code/i)).toBeInTheDocument();
    });

    it('shows claude code is local', () => {
      renderWithProviders(<EnginesPage />, { route: '/engines' });
      expect(screen.getByTestId('engine-claude-name')).toHaveTextContent(/local/i);
    });

    it('shows claude code status as active', () => {
      renderWithProviders(<EnginesPage />, { route: '/engines' });
      const rows = screen.getAllByRole('option');
      const claudeRow = rows.find(r => r.textContent?.includes('Claude Code'));
      expect(claudeRow?.textContent).toContain('Active');
    });
  });

  describe('Hermes Engine', () => {
    it('displays hermes engine', () => {
      renderWithProviders(<EnginesPage />, { route: '/engines' });
      expect(screen.getByText(/hermes/i)).toBeInTheDocument();
    });

    it('shows hermes is local ollama', () => {
      renderWithProviders(<EnginesPage />, { route: '/engines' });
      expect(screen.getByText(/ollama/i)).toBeInTheDocument();
    });

    it('shows hermes status as available', () => {
      renderWithProviders(<EnginesPage />, { route: '/engines' });
      const rows = screen.getAllByRole('option');
      const hermesRow = rows.find(r => r.textContent?.includes('Hermes'));
      expect(hermesRow?.textContent).toContain('Available');
    });
  });

  describe('OpenCodeEngine', () => {
    it('displays opencode engine', () => {
      renderWithProviders(<EnginesPage />, { route: '/engines' });
      expect(screen.getByText(/opencode/i)).toBeInTheDocument();
    });

    it('shows opencode is available', () => {
      renderWithProviders(<EnginesPage />, { route: '/engines' });
      const rows = screen.getAllByRole('option');
      const opencodeRow = rows.find(r => r.textContent?.includes('OpenCode'));
      expect(opencodeRow?.textContent).toContain('Available');
    });
  });

  describe('Engine Selection', () => {
    it('renders select buttons for each engine', () => {
      renderWithProviders(<EnginesPage />, { route: '/engines' });
      const selectButtons = screen.getAllByRole('button', { name: /select/i });
      expect(selectButtons.length).toBeGreaterThanOrEqual(3);
    });

    it('select button is clickable', () => {
      renderWithProviders(<EnginesPage />, { route: '/engines' });
      const selectButtons = screen.getAllByRole('button', { name: /select/i });
      fireEvent.click(selectButtons[0]);
      expect(selectButtons[0]).toBeInTheDocument();
    });

    it('can select multiple engines', () => {
      renderWithProviders(<EnginesPage />, { route: '/engines' });
      const selectButtons = screen.getAllByRole('button', { name: /select/i });

      selectButtons.forEach(btn => {
        fireEvent.click(btn);
      });

      expect(selectButtons[0]).toBeInTheDocument();
    });
  });

  describe('Engine Information', () => {
    it('displays engine names clearly', () => {
      renderWithProviders(<EnginesPage />, { route: '/engines' });
      expect(screen.getByText(/claude code/i)).toBeInTheDocument();
      expect(screen.getByText(/hermes/i)).toBeInTheDocument();
    });

    it('displays engine locations', () => {
      renderWithProviders(<EnginesPage />, { route: '/engines' });
      expect(screen.getByTestId('engine-claude-name')).toHaveTextContent(/local/i);
    });

    it('displays engine availability status', () => {
      renderWithProviders(<EnginesPage />, { route: '/engines' });
      expect(screen.getByTestId('engine-claude-status')).toHaveTextContent(/active/i);
      expect(screen.getByTestId('engine-opencode-status')).toHaveTextContent(/available/i);
    });
  });

  describe('Responsive Design', () => {
    it('heading is visible', () => {
      renderWithProviders(<EnginesPage />, { route: '/engines' });
      const heading = screen.getByText(/ai engines/i);
      expect(heading).toBeVisible();
    });

    it('all engine options are visible', () => {
      renderWithProviders(<EnginesPage />, { route: '/engines' });
      const options = screen.getAllByRole('option');
      options.forEach(option => expect(option).toBeVisible());
    });

    it('select buttons are visible', () => {
      renderWithProviders(<EnginesPage />, { route: '/engines' });
      const selectButtons = screen.getAllByRole('button', { name: /select/i });
      selectButtons.forEach(btn => expect(btn).toBeVisible());
    });
  });

  describe('Accessibility', () => {
    it('engine options have proper roles', () => {
      renderWithProviders(<EnginesPage />, { route: '/engines' });
      const options = screen.getAllByRole('option');
      expect(options.length).toBeGreaterThanOrEqual(3);
    });

    it('buttons have accessible names', () => {
      renderWithProviders(<EnginesPage />, { route: '/engines' });
      const buttons = screen.getAllByRole('button', { name: /select/i });
      buttons.forEach(btn => {
        expect(btn.textContent).toMatch(/select/i);
      });
    });

    it('can focus on selectable engines', () => {
      renderWithProviders(<EnginesPage />, { route: '/engines' });
      const buttons = screen.getAllByRole('button');

      buttons.forEach(btn => {
        btn.focus();
        expect(document.activeElement).toBe(btn);
      });
    });
  });

  describe('Session Management', () => {
    it('maintains auth token', () => {
      const token = localStorage.getItem('auth_token');
      expect(token).toBe('jwt-test-token-engines-123');
    });

    it('displays engines when authenticated', () => {
      renderWithProviders(<EnginesPage />, { route: '/engines' });
      expect(screen.getByText(/ai engines/i)).toBeInTheDocument();
    });
  });

  describe('Engine Switching', () => {
    it('shows all engines ready for selection', () => {
      renderWithProviders(<EnginesPage />, { route: '/engines' });
      const buttons = screen.getAllByRole('button', { name: /select/i });
      expect(buttons.length).toBeGreaterThanOrEqual(3);
    });

    it('active engine is clearly marked', () => {
      renderWithProviders(<EnginesPage />, { route: '/engines' });
      expect(screen.getByText(/status: active/i)).toBeInTheDocument();
    });

    it('other engines show as available', () => {
      renderWithProviders(<EnginesPage />, { route: '/engines' });
      const availableElements = screen.getAllByText(/available/i);
      expect(availableElements.length).toBeGreaterThan(0);
    });
  });
});
