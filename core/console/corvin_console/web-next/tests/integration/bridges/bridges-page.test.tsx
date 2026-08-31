import { describe, it, expect, beforeEach } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { renderWithProviders } from '../../utils/test-utils';
import { BridgesPage } from '../../fixtures/mock-pages';

describe('Bridges Page Integration', () => {
  beforeEach(() => {
    localStorage.setItem('auth_token', 'jwt-test-token-bridges-123');
  });

  describe('Bridges Page Display', () => {
    it('renders bridges page heading', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
      expect(screen.getByText(/bridges & channels/i)).toBeInTheDocument();
    });

    it('displays available bridge platforms', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
      expect(screen.getByText(/discord/i)).toBeInTheDocument();
      expect(screen.getByText(/telegram/i)).toBeInTheDocument();
      expect(screen.getByText(/slack/i)).toBeInTheDocument();
    });
  });

  describe('Discord Bridge', () => {
    it('displays discord bridge section', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
      expect(screen.getByText(/^Discord$/)).toBeInTheDocument();
    });

    it('shows discord connection status', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
      const discordElements = screen.getAllByText(/connected/i);
      expect(discordElements.length).toBeGreaterThan(0);
    });

    it('shows discord channel count', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
      expect(screen.getByText(/channels: 3/i)).toBeInTheDocument();
    });

    it('has configure button for discord', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
      const configureButtons = screen.getAllByRole('button', { name: /configure/i });
      expect(configureButtons.length).toBeGreaterThan(0);
    });
  });

  describe('Telegram Bridge', () => {
    it('displays telegram bridge section', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
      expect(screen.getByText(/^Telegram$/)).toBeInTheDocument();
    });

    it('shows telegram connection status', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
      expect(screen.getByTestId('telegram-status')).toBeInTheDocument();
      expect(screen.getByTestId('telegram-status')).toHaveTextContent(/connected: yes/i);
    });

    it('shows telegram bot ID', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
      expect(screen.getByText(/@corvin_bot/)).toBeInTheDocument();
    });

    it('has configure button for telegram', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
      const configureButtons = screen.getAllByRole('button', { name: /configure/i });
      expect(configureButtons.length).toBeGreaterThan(0);
    });
  });

  describe('Slack Bridge', () => {
    it('displays slack bridge section', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
      expect(screen.getByText(/^Slack$/)).toBeInTheDocument();
    });

    it('shows slack connection status as not connected', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
      expect(screen.getByText(/connected: no/i)).toBeInTheDocument();
    });

    it('shows connect button for slack', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
      const connectButton = screen.getByRole('button', { name: /connect/i });
      expect(connectButton).toBeInTheDocument();
    });
  });

  describe('Bridge Configuration', () => {
    it('configure buttons are clickable', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
      const configureButtons = screen.getAllByRole('button', { name: /configure/i });
      configureButtons.forEach(btn => {
        fireEvent.click(btn);
      });
      expect(configureButtons[0]).toBeInTheDocument();
    });

    it('connect button is clickable', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
      const connectButton = screen.getByRole('button', { name: /connect/i });
      fireEvent.click(connectButton);
      expect(connectButton).toBeInTheDocument();
    });

    it('can configure multiple bridges', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
      const allButtons = screen.getAllByRole('button');
      allButtons.forEach(btn => {
        fireEvent.click(btn);
      });
      expect(allButtons[0]).toBeInTheDocument();
    });
  });

  describe('Bridge Information Display', () => {
    it('displays all bridges clearly', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
      expect(screen.getByText(/^Discord$/)).toBeInTheDocument();
      expect(screen.getByText(/^Telegram$/)).toBeInTheDocument();
      expect(screen.getByText(/^Slack$/)).toBeInTheDocument();
    });

    it('shows connection status for each bridge', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
      const connectedElements = screen.getAllByText(/connected/i);
      expect(connectedElements.length).toBeGreaterThan(0);
    });

    it('displays bridge-specific information', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
      expect(screen.getByText(/channels: 3/i)).toBeInTheDocument();
      expect(screen.getByText(/@corvin_bot/)).toBeInTheDocument();
    });
  });

  describe('Responsive Design', () => {
    it('heading is visible', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
      const heading = screen.getByText(/bridges & channels/i);
      expect(heading).toBeVisible();
    });

    it('all bridge sections are visible', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
      const regions = screen.getAllByRole('region');
      regions.forEach(region => expect(region).toBeVisible());
    });

    it('all buttons are visible', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
      const buttons = screen.getAllByRole('button');
      buttons.forEach(btn => expect(btn).toBeVisible());
    });
  });

  describe('Accessibility', () => {
    it('bridge sections have proper roles', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
      const regions = screen.getAllByRole('region');
      expect(regions.length).toBeGreaterThanOrEqual(3);
    });

    it('buttons have accessible names', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
      const buttons = screen.getAllByRole('button');
      buttons.forEach(btn => {
        expect(btn.textContent).toBeTruthy();
      });
    });

    it('can focus on all buttons', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
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
      expect(token).toBe('jwt-test-token-bridges-123');
    });

    it('displays bridges when authenticated', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
      expect(screen.getByText(/bridges & channels/i)).toBeInTheDocument();
    });
  });

  describe('Channel Management', () => {
    it('shows channel information', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
      expect(screen.getByText(/channels: 3/i)).toBeInTheDocument();
    });

    it('displays bot identity for connected services', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
      expect(screen.getByText(/@corvin_bot/)).toBeInTheDocument();
    });
  });

  describe('Bridge Status Indicators', () => {
    it('clearly indicates connected bridges', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
      const connectedYes = screen.getAllByText(/connected: yes/i);
      expect(connectedYes.length).toBeGreaterThan(0);
    });

    it('clearly indicates disconnected bridges', () => {
      renderWithProviders(<BridgesPage />, { route: '/bridges' });
      expect(screen.getByText(/connected: no/i)).toBeInTheDocument();
    });
  });
});
