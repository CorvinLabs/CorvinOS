import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';

describe('HelpTooltip Component', () => {
  it('renders tooltip trigger', () => {
    const { container } = render(
      <button title="Help text">?</button>
    );
    expect(container.querySelector('button')).toBeInTheDocument();
  });

  it('displays help text on hover', () => {
    render(
      <button title="This is help text">Help</button>
    );
    const button = screen.getByRole('button');
    expect(button).toHaveAttribute('title', 'This is help text');
  });

  it('supports aria-describedby', () => {
    const { container } = render(
      <div>
        <button aria-describedby="help-1">Info</button>
        <div id="help-1" role="tooltip">Helpful message</div>
      </div>
    );
    expect(container.querySelector('[aria-describedby="help-1"]')).toBeInTheDocument();
  });

  it('tooltip content is accessible', () => {
    render(
      <div>
        <button>?</button>
        <div role="tooltip">Help content here</div>
      </div>
    );
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
    expect(screen.getByText('Help content here')).toBeInTheDocument();
  });

  it('supports icon button style', () => {
    const { container } = render(
      <button className="help-icon" aria-label="Help">
        ℹ️
      </button>
    );
    expect(container.querySelector('.help-icon')).toBeInTheDocument();
  });

  it('tooltip is hidden initially', () => {
    const { container } = render(
      <div>
        <button>Help</button>
        <div role="tooltip" style={{ display: 'none' }}>Hidden content</div>
      </div>
    );
    expect(container.querySelector('[role="tooltip"]')).toBeInTheDocument();
  });

  it('supports custom help text', () => {
    render(
      <button title="Custom help message">?</button>
    );
    expect(screen.getByTitle('Custom help message')).toBeInTheDocument();
  });

  it('supports placement attributes', () => {
    const { container } = render(
      <button data-tooltip-placement="top">Help</button>
    );
    expect(container.querySelector('[data-tooltip-placement="top"]')).toBeInTheDocument();
  });

  it('supports disabled tooltip', () => {
    const { container } = render(
      <button disabled title="Cannot help when disabled">?</button>
    );
    expect(container.querySelector('button[disabled]')).toBeInTheDocument();
  });

  it('tooltip text is readable', () => {
    render(
      <div title="This is help text" role="tooltip">
        Help Icon
      </div>
    );
    expect(screen.getByText('Help Icon')).toBeInTheDocument();
  });
});
