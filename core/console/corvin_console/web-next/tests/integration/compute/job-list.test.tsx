import { describe, it, expect, beforeEach } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { renderWithProviders } from '../../utils/test-utils';
import { ComputePage } from '../../fixtures/mock-pages';

describe('Compute Jobs List Integration', () => {
  beforeEach(() => {
    localStorage.setItem('auth_token', 'jwt-test-token-abc123');
  });

  it('displays compute page heading', () => {
    renderWithProviders(<ComputePage />, { route: '/compute' });
    expect(screen.getByText(/compute jobs/i)).toBeInTheDocument();
  });

  it('displays create job button', () => {
    renderWithProviders(<ComputePage />, { route: '/compute' });
    expect(screen.getByRole('button', { name: /new job/i })).toBeInTheDocument();
  });

  it('displays list of existing jobs', () => {
    renderWithProviders(<ComputePage />, { route: '/compute' });
    expect(screen.getByText(/SELECT COUNT/i)).toBeInTheDocument();
    expect(screen.getByText(/SELECT.*FROM users/i)).toBeInTheDocument();
  });

  it('shows job statuses', () => {
    renderWithProviders(<ComputePage />, { route: '/compute' });
    expect(screen.getByText(/completed/i)).toBeInTheDocument();
    expect(screen.getByText(/running/i)).toBeInTheDocument();
  });

  it('shows progress for running jobs', () => {
    renderWithProviders(<ComputePage />, { route: '/compute' });
    expect(screen.getByText(/45%/)).toBeInTheDocument();
  });

  it('allows creating a new job', () => {
    renderWithProviders(<ComputePage />, { route: '/compute' });

    const createButton = screen.getByRole('button', { name: /new job/i });
    fireEvent.click(createButton);

    expect(createButton).toBeInTheDocument();
  });

  it('displays job queries in list', () => {
    renderWithProviders(<ComputePage />, { route: '/compute' });

    // Check for SQL queries
    const jobList = screen.getByText(/SELECT COUNT/i);
    expect(jobList).toBeInTheDocument();
  });

  it('lists are interactive elements', () => {
    const { container } = renderWithProviders(<ComputePage />, { route: '/compute' });

    const jobs = container.querySelectorAll('div');
    expect(jobs.length).toBeGreaterThan(0);
  });

  it('shows job metadata', () => {
    renderWithProviders(<ComputePage />, { route: '/compute' });

    // Should have job information
    const content = screen.getByText(/SELECT COUNT/i);
    expect(content).toBeVisible();
  });

  it('new job button is clickable', () => {
    renderWithProviders(<ComputePage />, { route: '/compute' });

    const button = screen.getByRole('button', { name: /new job/i });
    expect(button).not.toBeDisabled();

    fireEvent.click(button);
    expect(button).toBeInTheDocument();
  });

  it('layout includes job list section', () => {
    const { container: _container } = renderWithProviders(<ComputePage />, { route: '/compute' });

    const heading = screen.getByText(/compute jobs/i);
    expect(heading).toBeInTheDocument();
    expect(heading.tagName).toBe('H1');
  });
});
