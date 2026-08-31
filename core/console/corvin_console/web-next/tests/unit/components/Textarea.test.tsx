import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';

describe('Textarea Component', () => {
  it('renders textarea element', () => {
    const { container } = render(<textarea />);
    expect(container.querySelector('textarea')).toBeInTheDocument();
  });

  it('accepts text input', () => {
    render(<textarea />);
    const textarea = screen.getByRole('textbox') as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: 'test text' } });
    expect(textarea.value).toBe('test text');
  });

  it('supports placeholder', () => {
    render(<textarea placeholder="Enter message" />);
    expect(screen.getByPlaceholderText('Enter message')).toBeInTheDocument();
  });

  it('supports disabled state', () => {
    render(<textarea disabled />);
    const textarea = screen.getByRole('textbox');
    expect(textarea).toBeDisabled();
  });

  it('supports readonly state', () => {
    render(<textarea readOnly value="fixed" />);
    const textarea = screen.getByDisplayValue('fixed');
    expect(textarea).toHaveAttribute('readonly');
  });

  it('supports rows attribute', () => {
    const { container } = render(<textarea rows={5} />);
    expect(container.querySelector('textarea[rows="5"]')).toBeInTheDocument();
  });

  it('supports cols attribute', () => {
    const { container } = render(<textarea cols={30} />);
    expect(container.querySelector('textarea[cols="30"]')).toBeInTheDocument();
  });

  it('supports multiline input', () => {
    render(<textarea />);
    const textarea = screen.getByRole('textbox') as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: 'Line 1\nLine 2\nLine 3' } });
    expect(textarea.value).toContain('Line 1');
    expect(textarea.value).toContain('Line 2');
    expect(textarea.value).toContain('Line 3');
  });

  it('supports value attribute', () => {
    render(<textarea value="initial text" readOnly />);
    expect(screen.getByDisplayValue('initial text')).toBeInTheDocument();
  });

  it('supports required attribute', () => {
    const { container } = render(<textarea required />);
    expect(container.querySelector('textarea[required]')).toBeInTheDocument();
  });

  it('supports maxLength attribute', () => {
    const { container } = render(<textarea maxLength={100} />);
    expect(container.querySelector('textarea[maxlength="100"]')).toBeInTheDocument();
  });
});
