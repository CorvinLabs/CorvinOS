import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '../../utils/test-utils';
import { DashboardPage } from '../../fixtures/mock-pages';

describe('Dashboard Display Integration', () => {
  it('renders dashboard heading', () => {
    renderWithProviders(<DashboardPage />, { route: '/dashboard' });
    expect(screen.getByText(/corvin overview/i)).toBeInTheDocument();
  });

  it('displays engines online status', () => {
    renderWithProviders(<DashboardPage />, { route: '/dashboard' });
    expect(screen.getByText(/Engines online: 2/i)).toBeInTheDocument();
  });

  it('displays connected channels', () => {
    renderWithProviders(<DashboardPage />, { route: '/dashboard' });
    expect(screen.getByText(/Discord/i)).toBeInTheDocument();
    expect(screen.getByText(/Telegram/i)).toBeInTheDocument();
    expect(screen.getByText(/Slack/i)).toBeInTheDocument();
  });

  it('displays audit events count', () => {
    renderWithProviders(<DashboardPage />, { route: '/dashboard' });
    expect(screen.getByText(/142/)).toBeInTheDocument();
  });

  it('displays uptime percentage', () => {
    renderWithProviders(<DashboardPage />, { route: '/dashboard' });
    expect(screen.getByText(/99\.8%/)).toBeInTheDocument();
  });

  it('displays last sync timestamp', () => {
    renderWithProviders(<DashboardPage />, { route: '/dashboard' });
    expect(screen.getByText(/2026-06/)).toBeInTheDocument();
  });

  it('lists available personas', () => {
    renderWithProviders(<DashboardPage />, { route: '/dashboard' });
    expect(screen.getByText(/Assistant|Coder|Researcher/)).toBeInTheDocument();
  });

  it('has proper heading structure', () => {
    const { container } = renderWithProviders(<DashboardPage />, { route: '/dashboard' });

    const h1 = container.querySelector('h1');
    expect(h1).toBeInTheDocument();
    expect(h1?.textContent).toMatch(/corvin overview/i);
  });

  it('displays multiple dashboard sections', () => {
    renderWithProviders(<DashboardPage />, { route: '/dashboard' });

    // Multiple data sections should be visible
    const allText = screen.getByText(/Engines online/i);
    expect(allText).toBeInTheDocument();
  });

  it('dashboard data is readable', () => {
    const { container } = renderWithProviders(<DashboardPage />, { route: '/dashboard' });

    const allDivs = container.querySelectorAll('div');
    expect(allDivs.length).toBeGreaterThan(5);
  });

  it('shows status indicators', () => {
    renderWithProviders(<DashboardPage />, { route: '/dashboard' });

    // Uptime percentage is a status indicator
    expect(screen.getByText(/99\.8%/)).toBeInTheDocument();
  });

  it('displays operational status of system', () => {
    renderWithProviders(<DashboardPage />, { route: '/dashboard' });

    // Online status and recent data indicate operational system
    expect(screen.getByText(/Engines online/i)).toBeInTheDocument();
    expect(screen.getByText(/Last sync/i)).toBeInTheDocument();
  });

  it('renders all required dashboard sections', () => {
    renderWithProviders(<DashboardPage />, { route: '/dashboard' });

    // Check all key sections exist
    const sections = [
      screen.getByText(/Engines online/),
      screen.getByText(/Channels/),
      screen.getByText(/142/),
      screen.getByText(/99\.8%/),
    ];

    sections.forEach(section => {
      expect(section).toBeInTheDocument();
    });
  });
});
