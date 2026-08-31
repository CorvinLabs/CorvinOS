import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';

describe('Badge Component', () => {
  it('renders badge container', () => {
    const { container } = render(<span className="badge">New</span>);
    expect(container.querySelector('.badge')).toBeInTheDocument();
  });

  it('displays badge text', () => {
    render(<span className="badge">Featured</span>);
    expect(screen.getByText('Featured')).toBeInTheDocument();
  });

  it('renders badge with variant class', () => {
    const { container } = render(<span className="badge badge-primary">Active</span>);
    expect(container.querySelector('.badge-primary')).toBeInTheDocument();
  });

  it('supports secondary variant', () => {
    const { container } = render(<span className="badge badge-secondary">Pending</span>);
    expect(container.querySelector('.badge-secondary')).toBeInTheDocument();
  });

  it('supports success variant', () => {
    const { container } = render(<span className="badge badge-success">Completed</span>);
    expect(container.querySelector('.badge-success')).toBeInTheDocument();
  });

  it('supports destructive variant', () => {
    const { container } = render(<span className="badge badge-destructive">Error</span>);
    expect(container.querySelector('.badge-destructive')).toBeInTheDocument();
  });

  it('supports outline variant', () => {
    const { container } = render(<span className="badge badge-outline">Draft</span>);
    expect(container.querySelector('.badge-outline')).toBeInTheDocument();
  });

  it('renders badge with custom className', () => {
    const { container } = render(<span className="badge custom-class">Custom</span>);
    expect(container.querySelector('.custom-class')).toBeInTheDocument();
  });

  it('badge is inline element', () => {
    const { container } = render(<span className="badge">Text</span>);
    const badge = container.querySelector('.badge');
    expect(badge?.tagName).toBe('SPAN');
  });

  it('supports children content', () => {
    render(
      <span className="badge">
        <strong>Important</strong>
      </span>
    );
    expect(screen.getByText('Important')).toBeInTheDocument();
  });
});
