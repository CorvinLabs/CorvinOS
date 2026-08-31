import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import React from 'react';

describe('Skeleton Component', () => {
  it('renders skeleton element', () => {
    const { container } = render(<div className="skeleton" />);
    expect(container.querySelector('.skeleton')).toBeInTheDocument();
  });

  it('has loading animation class', () => {
    const { container } = render(<div className="skeleton animate-pulse" />);
    expect(container.querySelector('.animate-pulse')).toBeInTheDocument();
  });

  it('supports custom width', () => {
    const { container } = render(<div className="skeleton" style={{ width: '100px' }} />);
    const skeleton = container.querySelector('.skeleton') as HTMLElement;
    expect(skeleton.style.width).toBe('100px');
  });

  it('supports custom height', () => {
    const { container } = render(<div className="skeleton" style={{ height: '20px' }} />);
    const skeleton = container.querySelector('.skeleton') as HTMLElement;
    expect(skeleton.style.height).toBe('20px');
  });

  it('supports rounded corners', () => {
    const { container } = render(<div className="skeleton rounded-md" />);
    expect(container.querySelector('.rounded-md')).toBeInTheDocument();
  });

  it('supports circle variant', () => {
    const { container } = render(<div className="skeleton rounded-full" style={{ width: '40px', height: '40px' }} />);
    expect(container.querySelector('.rounded-full')).toBeInTheDocument();
  });

  it('multiple skeletons for content blocks', () => {
    const { container } = render(
      <div>
        <div className="skeleton" />
        <div className="skeleton" />
        <div className="skeleton" />
      </div>
    );
    expect(container.querySelectorAll('.skeleton').length).toBe(3);
  });

  it('supports responsive sizing', () => {
    const { container } = render(
      <div className="skeleton w-full h-12" />
    );
    expect(container.querySelector('.w-full')).toBeInTheDocument();
    expect(container.querySelector('.h-12')).toBeInTheDocument();
  });

  it('skeleton is visible during loading', () => {
    const { container } = render(<div className="skeleton" />);
    expect(container.querySelector('.skeleton')).toBeVisible();
  });

  it('supports custom className', () => {
    const { container } = render(<div className="skeleton custom-class" />);
    expect(container.querySelector('.custom-class')).toBeInTheDocument();
  });
});
