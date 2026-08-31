import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';

describe('Label Component', () => {
  it('renders label element', () => {
    const { container } = render(
      <label htmlFor="input-1">Label text</label>
    );
    expect(container.querySelector('label')).toBeInTheDocument();
  });

  it('displays label text', () => {
    render(
      <label htmlFor="input-1">Username</label>
    );
    expect(screen.getByText('Username')).toBeInTheDocument();
  });

  it('associates with input via htmlFor', () => {
    const { container } = render(
      <div>
        <label htmlFor="username">Username</label>
        <input id="username" />
      </div>
    );
    expect(container.querySelector('label[for="username"]')).toBeInTheDocument();
  });

  it('supports required indicator', () => {
    render(
      <label>
        Email <span className="required">*</span>
      </label>
    );
    expect(screen.getByText('*')).toBeInTheDocument();
  });

  it('label text is readable', () => {
    render(
      <label htmlFor="email">Email Address</label>
    );
    expect(screen.getByText('Email Address')).toBeInTheDocument();
  });

  it('supports nested input', () => {
    const { container } = render(
      <label>
        <input type="checkbox" />
        <span>Agree to terms</span>
      </label>
    );
    expect(container.querySelector('input[type="checkbox"]')).toBeInTheDocument();
  });

  it('supports custom className', () => {
    const { container } = render(
      <label className="form-label">Test</label>
    );
    expect(container.querySelector('.form-label')).toBeInTheDocument();
  });

  it('supports disabled styling', () => {
    const { container } = render(
      <label className="disabled">Disabled label</label>
    );
    expect(container.querySelector('.disabled')).toBeInTheDocument();
  });

  it('label can wrap multiple elements', () => {
    const { container } = render(
      <label>
        <span>Text: </span>
        <strong>Bold</strong>
      </label>
    );
    expect(container.querySelector('strong')).toBeInTheDocument();
  });

  it('supports data attributes', () => {
    const { container } = render(
      <label data-testid="my-label">Test Label</label>
    );
    expect(container.querySelector('[data-testid="my-label"]')).toBeInTheDocument();
  });
});
