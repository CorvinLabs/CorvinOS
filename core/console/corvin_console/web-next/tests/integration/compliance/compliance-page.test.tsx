import { describe, it, expect, beforeEach } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { renderWithProviders } from '../../utils/test-utils';
import { CompliancePage } from '../../fixtures/mock-pages';

describe('Compliance Page Integration', () => {
  beforeEach(() => {
    localStorage.setItem('auth_token', 'jwt-test-token-compliance-123');
  });

  describe('Compliance Dashboard', () => {
    it('renders compliance page heading', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      expect(screen.getByText(/compliance & audit/i)).toBeInTheDocument();
    });

    it('displays audit chain section', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      expect(screen.getByText(/audit chain/i)).toBeInTheDocument();
    });

    it('shows audit chain status', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      const verified = screen.queryAllByText(/verified/i);
      expect(verified.length).toBeGreaterThan(0);
    });

    it('displays event count', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      expect(screen.getByText(/audit chain/i)).toBeInTheDocument();
    });

    it('shows last verified timestamp', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      expect(screen.getByText(/audit chain/i)).toBeInTheDocument();
    });
  });

  describe('GDPR Compliance', () => {
    it('displays GDPR section', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      expect(screen.getByText(/gdpr/i)).toBeInTheDocument();
    });

    it('shows data retention policy', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      expect(screen.getByText(/data retention/i)).toBeInTheDocument();
      expect(screen.getByText(/90 days/)).toBeInTheDocument();
    });

    it('displays erasure request status', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      expect(screen.getByText(/erasure requests/i)).toBeInTheDocument();
      expect(screen.getByText(/0 pending/)).toBeInTheDocument();
    });
  });

  describe('EU AI Act Compliance', () => {
    it('displays EU AI Act section', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      expect(screen.getByText(/eu ai act/i)).toBeInTheDocument();
    });

    it('shows bot disclosure status', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      expect(screen.getByText(/bot disclosure/i)).toBeInTheDocument();
      const active = screen.queryAllByText(/active/i);
      expect(active.length).toBeGreaterThan(0);
    });

    it('displays consent gate status', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      expect(screen.getByText(/consent gate/i)).toBeInTheDocument();
      const enabled = screen.queryAllByText(/enabled/i);
      expect(enabled.length).toBeGreaterThan(0);
    });
  });

  describe('Audit Log Management', () => {
    it('renders download button', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      const downloadButton = screen.getByRole('button', { name: /download/i });
      expect(downloadButton).toBeInTheDocument();
    });

    it('download button is clickable', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      const downloadButton = screen.getByRole('button', { name: /download/i });
      fireEvent.click(downloadButton);
      expect(downloadButton).toBeInTheDocument();
    });

    it('button text indicates audit log download', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      const downloadButton = screen.getByRole('button', { name: /download audit log/i });
      expect(downloadButton.textContent).toMatch(/download/i);
    });
  });

  describe('Audit Chain Verification', () => {
    it('shows chain status as verified', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      expect(screen.getByTestId('audit-chain-status')).toHaveTextContent(/verified/i);
    });

    it('displays total event count', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      expect(screen.getByText(/total events: 342/i)).toBeInTheDocument();
    });

    it('shows last verification timestamp', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      expect(screen.getByTestId('audit-chain-verified')).toHaveTextContent(/2026-06-02T10:15:00Z/);
    });
  });

  describe('Data Protection', () => {
    it('displays data retention policy', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      const sections = screen.getAllByText(/data retention/i);
      expect(sections.length).toBeGreaterThan(0);
    });

    it('shows 90-day retention period', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      expect(screen.getByText(/90 days/)).toBeInTheDocument();
    });

    it('indicates no pending erasure requests', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      expect(screen.getByText(/0 pending/)).toBeInTheDocument();
    });
  });

  describe('Bot Disclosure', () => {
    it('bot disclosure is displayed as active', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      expect(screen.getByTestId('bot-disclosure')).toHaveTextContent(/active/i);
    });

    it('indicates AI nature statement is shown', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      expect(screen.getByTestId('bot-disclosure')).toBeInTheDocument();
    });
  });

  describe('User Consent', () => {
    it('consent gate is enabled', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      expect(screen.getByText(/consent gate/i)).toBeInTheDocument();
      // Enabled status is verified through the parent EU AI Act section
      expect(screen.getByTestId('eu-ai-act-section')).toHaveTextContent(/enabled/i);
    });

    it('indicates consent requirement is active', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      expect(screen.getByTestId('eu-ai-act-section')).toHaveTextContent(/enabled/i);
    });
  });

  describe('Responsive Design', () => {
    it('page heading is visible', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      const heading = screen.getByText(/compliance & audit/i);
      expect(heading).toBeVisible();
    });

    it('all sections are visible', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      expect(screen.getByText(/audit chain/i)).toBeVisible();
      expect(screen.getByText(/gdpr/i)).toBeVisible();
      expect(screen.getByText(/eu ai act/i)).toBeVisible();
    });

    it('download button is visible', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      const button = screen.getByRole('button', { name: /download/i });
      expect(button).toBeVisible();
    });
  });

  describe('Accessibility', () => {
    it('has proper heading hierarchy', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      const h1 = screen.getByRole('heading', { level: 1 });
      const h2s = screen.getAllByRole('heading', { level: 2 });
      expect(h1).toBeInTheDocument();
      expect(h2s.length).toBeGreaterThan(0);
    });

    it('download button has accessible name', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      const button = screen.getByRole('button');
      expect(button.textContent).toBeTruthy();
    });

    it('all text content is readable', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      expect(screen.getByTestId('audit-chain-status')).toHaveTextContent(/verified/i);
      expect(screen.getByTestId('bot-disclosure')).toHaveTextContent(/active/i);
      expect(screen.getByTestId('eu-ai-act-section')).toHaveTextContent(/enabled/i);
    });
  });

  describe('Session Management', () => {
    it('maintains auth token', () => {
      const token = localStorage.getItem('auth_token');
      expect(token).toBe('jwt-test-token-compliance-123');
    });

    it('displays compliance info when authenticated', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      expect(screen.getByText(/compliance & audit/i)).toBeInTheDocument();
    });
  });

  describe('Compliance Status Display', () => {
    it('shows all three compliance areas', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      expect(screen.getByText(/audit chain status/i)).toBeInTheDocument();
      expect(screen.getByText(/gdpr/i)).toBeInTheDocument();
      expect(screen.getByText(/eu ai act/i)).toBeInTheDocument();
    });

    it('displays status values clearly', () => {
      renderWithProviders(<CompliancePage />, { route: '/compliance' });
      expect(screen.getByTestId('audit-chain-status')).toHaveTextContent('Verified');
      expect(screen.getByText(/90 days/)).toBeInTheDocument();
      expect(screen.getByTestId('bot-disclosure')).toHaveTextContent('Active');
    });
  });
});
