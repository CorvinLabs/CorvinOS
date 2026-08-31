import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';

describe('Input Component', () => {
  it('renders input element', () => {
    const { container } = render(<input type="text" />);
    expect(container.querySelector('input')).toBeInTheDocument();
  });

  it('accepts text input', () => {
    render(<input type="text" />);
    const input = screen.getByRole('textbox') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'test' } });
    expect(input.value).toBe('test');
  });

  it('supports placeholder', () => {
    render(<input type="text" placeholder="Enter text" />);
    expect(screen.getByPlaceholderText('Enter text')).toBeInTheDocument();
  });

  it('supports disabled state', () => {
    render(<input type="text" disabled />);
    const input = screen.getByRole('textbox');
    expect(input).toBeDisabled();
  });

  it('supports type email', () => {
    const { container } = render(<input type="email" />);
    expect(container.querySelector('input[type="email"]')).toBeInTheDocument();
  });

  it('supports type password', () => {
    const { container } = render(<input type="password" />);
    expect(container.querySelector('input[type="password"]')).toBeInTheDocument();
  });

  it('supports type number', () => {
    const { container } = render(<input type="number" />);
    expect(container.querySelector('input[type="number"]')).toBeInTheDocument();
  });

  it('supports readonly state', () => {
    const { container } = render(<input type="text" readOnly value="fixed" />);
    expect(container.querySelector('input[readonly]')).toBeInTheDocument();
  });

  it('supports value attribute', () => {
    render(<input type="text" value="initial" readOnly />);
    const input = screen.getByDisplayValue('initial');
    expect(input).toBeInTheDocument();
  });

  it('supports required attribute', () => {
    const { container } = render(<input type="text" required />);
    expect(container.querySelector('input[required]')).toBeInTheDocument();
  });
});
