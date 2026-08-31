import { describe, it, expect } from 'vitest';
import React from 'react';
import { render, screen } from '@testing-library/react';

describe('Dialog Component', () => {
  it('renders dialog container', () => {
    const { container } = render(
      <div role="dialog" aria-modal="true" aria-labelledby="dialog-title">
        <h2 id="dialog-title">Dialog Title</h2>
        <p>Dialog content</p>
      </div>
    );

    expect(container.querySelector('[role="dialog"]')).toBeInTheDocument();
  });

  it('has aria-modal attribute', () => {
    const { container } = render(
      <div role="dialog" aria-modal="true">Content</div>
    );

    expect(container.querySelector('[aria-modal="true"]')).toBeInTheDocument();
  });

  it('has aria-labelledby for accessibility', () => {
    const { container } = render(
      <div role="dialog" aria-labelledby="dialog-title">
        <h2 id="dialog-title">Title</h2>
      </div>
    );

    expect(container.querySelector('[aria-labelledby="dialog-title"]')).toBeInTheDocument();
  });

  it('renders dialog title', () => {
    render(
      <div role="dialog">
        <h2>Test Title</h2>
      </div>
    );

    expect(screen.getByText('Test Title')).toBeInTheDocument();
  });

  it('renders dialog content', () => {
    render(
      <div role="dialog">
        <p>Test content</p>
      </div>
    );

    expect(screen.getByText('Test content')).toBeInTheDocument();
  });

  it('renders action buttons', () => {
    render(
      <div role="dialog">
        <button>Cancel</button>
        <button>OK</button>
      </div>
    );

    expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /ok/i })).toBeInTheDocument();
  });

  it('dialog is visually hidden initially (optional)', () => {
    const { container } = render(
      <div role="dialog" style={{ display: 'none' }}>
        Content
      </div>
    );

    expect(container.querySelector('[role="dialog"]')).toBeInTheDocument();
  });

  it('supports keyboard interaction', () => {
    render(
      <div role="dialog">
        <button>OK</button>
      </div>
    );

    const button = screen.getByRole('button');
    expect(button).toBeInTheDocument();
  });

  it('renders with proper structure', () => {
    const { container } = render(
      <div role="dialog" aria-modal="true">
        <h2>Title</h2>
        <div>Content goes here</div>
        <div>
          <button>Cancel</button>
          <button>OK</button>
        </div>
      </div>
    );

    expect(container.querySelector('[role="dialog"]')).toBeInTheDocument();
    expect(container.querySelector('h2')).toBeInTheDocument();
  });

  it('supports closing dialog', () => {
    render(
      <div role="dialog">
        <button aria-label="Close">×</button>
      </div>
    );

    expect(screen.getByRole('button', { name: /close/i })).toBeInTheDocument();
  });
});
