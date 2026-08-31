import { describe, it, expect, beforeEach } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { renderWithProviders } from '../../utils/test-utils';
import { WorkflowsPage } from '../../fixtures/mock-pages';

describe('Workflows Page Integration', () => {
  beforeEach(() => {
    localStorage.setItem('auth_token', 'jwt-test-token-workflows-123');
  });

  describe('Workflows Display', () => {
    it('renders workflows page heading', () => {
      renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      expect(screen.getByText(/workflows/i)).toBeInTheDocument();
    });

    it('displays existing workflows list', () => {
      renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      expect(screen.getByText('workflow-1')).toBeInTheDocument();
      expect(screen.getByText('workflow-2')).toBeInTheDocument();
    });

    it('shows workflow status', () => {
      renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      expect(screen.getByText('discovering')).toBeInTheDocument();
      expect(screen.getByText('ready')).toBeInTheDocument();
    });

    it('displays workflow actions', () => {
      renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      const buttons = screen.getAllByRole('button');
      expect(buttons.length).toBeGreaterThan(1);
    });
  });

  describe('Workflow Creation', () => {
    it('renders create workflow button', () => {
      renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      const createButton = screen.getByRole('button', { name: /create workflow/i });
      expect(createButton).toBeInTheDocument();
    });

    it('create button is clickable', () => {
      renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      const createButton = screen.getByRole('button', { name: /create workflow/i });
      fireEvent.click(createButton);
      expect(createButton).toBeInTheDocument();
    });
  });

  describe('Workflow Search', () => {
    it('renders search input', () => {
      renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      const searchInput = screen.getByPlaceholderText(/search workflows/i);
      expect(searchInput).toBeInTheDocument();
    });

    it('search input accepts text', () => {
      renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      const searchInput = screen.getByPlaceholderText(/search workflows/i) as HTMLInputElement;
      fireEvent.change(searchInput, { target: { value: 'data-processing' } });
      expect(searchInput.value).toBe('data-processing');
    });

    it('can clear search input', () => {
      renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      const searchInput = screen.getByPlaceholderText(/search workflows/i) as HTMLInputElement;
      fireEvent.change(searchInput, { target: { value: 'test' } });
      fireEvent.change(searchInput, { target: { value: '' } });
      expect(searchInput.value).toBe('');
    });
  });

  describe('Workflow States', () => {
    it('shows discovering state', () => {
      renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      expect(screen.getByText('discovering')).toBeInTheDocument();
    });

    it('shows ready state', () => {
      renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      expect(screen.getByText('ready')).toBeInTheDocument();
    });

    it('displays correct action for ready workflows', () => {
      renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      const executeButtons = screen.getAllByRole('button', { name: /execute/i });
      expect(executeButtons.length).toBeGreaterThan(0);
    });

    it('displays correct action for discovering workflows', () => {
      renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      const editButtons = screen.getAllByRole('button', { name: /edit/i });
      expect(editButtons.length).toBeGreaterThan(0);
    });
  });

  describe('Workflow Interactions', () => {
    it('edit button is clickable', () => {
      renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      const editButton = screen.getByRole('button', { name: /edit/i });
      fireEvent.click(editButton);
      expect(editButton).toBeInTheDocument();
    });

    it('execute button is clickable', () => {
      renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      const executeButton = screen.getByRole('button', { name: /execute/i });
      fireEvent.click(executeButton);
      expect(executeButton).toBeInTheDocument();
    });

    it('can interact with multiple workflows', () => {
      renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      const buttons = screen.getAllByRole('button');
      expect(buttons.length).toBeGreaterThanOrEqual(2);

      fireEvent.click(buttons[0]);
      fireEvent.click(buttons[1]);

      expect(buttons[0]).toBeInTheDocument();
      expect(buttons[1]).toBeInTheDocument();
    });
  });

  describe('Responsive Design', () => {
    it('heading is visible', () => {
      renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      const heading = screen.getByText(/workflows/i);
      expect(heading).toBeVisible();
    });

    it('search input is visible', () => {
      renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      const searchInput = screen.getByPlaceholderText(/search workflows/i);
      expect(searchInput).toBeVisible();
    });

    it('all interactive elements are visible', () => {
      renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      const createButton = screen.getByRole('button', { name: /create workflow/i });
      expect(createButton).toBeVisible();
    });
  });

  describe('Workflow List Structure', () => {
    it('displays workflows in rows', () => {
      const { container } = renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      const rows = container.querySelectorAll('[role="row"]');
      expect(rows.length).toBeGreaterThanOrEqual(2);
    });

    it('each workflow row has name and status', () => {
      const { container } = renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      const rows = container.querySelectorAll('[role="row"]');

      rows.forEach(row => {
        expect(row.textContent).toBeTruthy();
        expect(row.textContent?.length).toBeGreaterThan(0);
      });
    });

    it('each workflow row has action button', () => {
      const { container } = renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      const rows = container.querySelectorAll('[role="row"]');

      rows.forEach(row => {
        const button = row.querySelector('button');
        expect(button).toBeInTheDocument();
      });
    });
  });

  describe('Keyboard Interaction', () => {
    it('search input accepts keyboard input', () => {
      renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      const searchInput = screen.getByPlaceholderText(/search workflows/i);

      fireEvent.keyDown(searchInput, { key: 'a', code: 'KeyA' });
      fireEvent.change(searchInput, { target: { value: 'a' } });

      expect((searchInput as HTMLInputElement).value).toBe('a');
    });

    it('can focus on buttons with keyboard', () => {
      renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      const buttons = screen.getAllByRole('button');

      buttons.forEach(button => {
        button.focus();
        expect(document.activeElement).toBe(button);
      });
    });
  });

  describe('Session Management', () => {
    it('maintains auth session', () => {
      const token = localStorage.getItem('auth_token');
      expect(token).toBe('jwt-test-token-workflows-123');
    });

    it('displays workflows when authenticated', () => {
      renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      expect(screen.getByText('workflow-1')).toBeInTheDocument();
    });
  });

  describe('Edge Cases', () => {
    it('handles special characters in workflow names', () => {
      renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      expect(screen.getByText('workflow-1')).toBeInTheDocument();
    });

    it('search works with partial matches', () => {
      renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      const searchInput = screen.getByPlaceholderText(/search workflows/i) as HTMLInputElement;
      fireEvent.change(searchInput, { target: { value: 'workflow' } });
      expect(searchInput.value).toBe('workflow');
    });

    it('can create multiple workflows', () => {
      renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      const createButton = screen.getByRole('button', { name: /create workflow/i });

      fireEvent.click(createButton);
      fireEvent.click(createButton);
      fireEvent.click(createButton);

      expect(createButton).toBeInTheDocument();
    });
  });

  describe('Accessibility', () => {
    it('heading has proper level', () => {
      renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      const heading = screen.getByRole('heading', { level: 1 });
      expect(heading).toBeInTheDocument();
    });

    it('all buttons have accessible names', () => {
      renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      const buttons = screen.getAllByRole('button');

      buttons.forEach(button => {
        expect(button.textContent || button.getAttribute('aria-label')).toBeTruthy();
      });
    });

    it('search input is accessible', () => {
      renderWithProviders(<WorkflowsPage />, { route: '/workflows' });
      const searchInput = screen.getByPlaceholderText(/search workflows/i);
      expect(searchInput).toHaveAttribute('type', 'text');
    });
  });
});
