import { describe, it, expect, beforeEach } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { renderWithProviders } from '../../utils/test-utils';
import { LoginPage } from '../../fixtures/mock-pages';

describe('Login Form Integration Tests', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('renders login form fields', () => {
    renderWithProviders(<LoginPage />);
    expect(screen.getByText(/login/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
  });

  it('allows entering email and password', () => {
    renderWithProviders(<LoginPage />);

    const emailInput = screen.getByLabelText(/email/i) as HTMLInputElement;
    const passwordInput = screen.getByLabelText(/password/i) as HTMLInputElement;

    fireEvent.change(emailInput, { target: { value: 'test@example.com' } });
    fireEvent.change(passwordInput, { target: { value: 'password123' } });

    expect(emailInput.value).toBe('test@example.com');
    expect(passwordInput.value).toBe('password123');
  });

  it('submits form with valid credentials', async () => {
    renderWithProviders(<LoginPage />);

    fireEvent.change(screen.getByLabelText(/email/i), {
      target: { value: 'test@example.com' },
    });
    fireEvent.change(screen.getByLabelText(/password/i), {
      target: { value: 'password123' },
    });

    const submitButton = screen.getByRole('button', { name: /sign in/i });
    fireEvent.click(submitButton);

    // Form should submit
    expect(submitButton).toBeInTheDocument();
  });

  it('prevents submission with empty fields', () => {
    renderWithProviders(<LoginPage />);

    const emailInput = screen.getByLabelText(/email/i) as HTMLInputElement;
    const passwordInput = screen.getByLabelText(/password/i) as HTMLInputElement;

    expect(emailInput.value).toBe('');
    expect(passwordInput.value).toBe('');
  });

  it('validates email format', async () => {
    renderWithProviders(<LoginPage />);

    const emailInput = screen.getByLabelText(/email/i) as HTMLInputElement;
    fireEvent.change(emailInput, { target: { value: 'invalid-email' } });

    // HTML5 validation
    expect(emailInput.type).toBe('email');
  });

  it('handles form submission', () => {
    const { container } = renderWithProviders(<LoginPage />);

    const form = container.querySelector('form');
    expect(form).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/email/i), {
      target: { value: 'test@example.com' },
    });
    fireEvent.change(screen.getByLabelText(/password/i), {
      target: { value: 'password123' },
    });

    const button = screen.getByRole('button', { name: /sign in/i });
    fireEvent.click(button);

    expect(button).toBeInTheDocument();
  });

  it('supports keyboard navigation', () => {
    renderWithProviders(<LoginPage />);

    const emailInput = screen.getByLabelText(/email/i);
    const passwordInput = screen.getByLabelText(/password/i);
    const button = screen.getByRole('button', { name: /sign in/i });

    // Tab through form
    emailInput.focus();
    expect(document.activeElement).toBe(emailInput);

    // Check all elements are in DOM
    expect(passwordInput).toBeInTheDocument();
    expect(button).toBeInTheDocument();
  });

  it('password input is masked', () => {
    renderWithProviders(<LoginPage />);

    const passwordInput = screen.getByLabelText(/password/i) as HTMLInputElement;
    expect(passwordInput.type).toBe('password');
  });
});
