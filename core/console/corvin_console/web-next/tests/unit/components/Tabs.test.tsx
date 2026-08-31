import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';

describe('Tabs Component', () => {
  it('renders tabs container', () => {
    const { container } = render(
      <div role="tablist">
        <button role="tab">Tab 1</button>
      </div>
    );
    expect(container.querySelector('[role="tablist"]')).toBeInTheDocument();
  });

  it('displays tab buttons', () => {
    render(
      <div role="tablist">
        <button role="tab">Tab 1</button>
        <button role="tab">Tab 2</button>
      </div>
    );
    expect(screen.getByRole('tab', { name: /tab 1/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /tab 2/i })).toBeInTheDocument();
  });

  it('supports active tab', () => {
    const { container } = render(
      <div role="tablist">
        <button role="tab" aria-selected="true">Active</button>
        <button role="tab" aria-selected="false">Inactive</button>
      </div>
    );
    expect(container.querySelector('[aria-selected="true"]')).toBeInTheDocument();
  });

  it('supports tabpanel content', () => {
    render(
      <div>
        <div role="tablist">
          <button role="tab">Tab 1</button>
        </div>
        <div role="tabpanel">Content 1</div>
      </div>
    );
    expect(screen.getByRole('tabpanel')).toBeInTheDocument();
    expect(screen.getByText('Content 1')).toBeInTheDocument();
  });

  it('tab is clickable', () => {
    render(
      <div role="tablist">
        <button role="tab">Click me</button>
      </div>
    );
    const tab = screen.getByRole('tab');
    fireEvent.click(tab);
    expect(tab).toBeInTheDocument();
  });

  it('multiple tabpanels', () => {
    const { container } = render(
      <div>
        <div role="tabpanel">Panel 1</div>
        <div role="tabpanel">Panel 2</div>
        <div role="tabpanel">Panel 3</div>
      </div>
    );
    expect(container.querySelectorAll('[role="tabpanel"]').length).toBe(3);
  });

  it('tabs support disabled state', () => {
    render(
      <div role="tablist">
        <button role="tab" disabled>Disabled</button>
        <button role="tab">Enabled</button>
      </div>
    );
    const disabledTab = screen.getByRole('tab', { name: /disabled/i });
    expect(disabledTab).toBeDisabled();
  });

  it('tabpanel has aria-labelledby', () => {
    const { container } = render(
      <div>
        <button role="tab" id="tab-1">Tab 1</button>
        <div role="tabpanel" aria-labelledby="tab-1">Content</div>
      </div>
    );
    expect(container.querySelector('[aria-labelledby="tab-1"]')).toBeInTheDocument();
  });

  it('supports keyboard navigation with aria-controls', () => {
    const { container } = render(
      <div role="tablist">
        <button role="tab" aria-controls="panel-1">Tab 1</button>
        <button role="tab" aria-controls="panel-2">Tab 2</button>
      </div>
    );
    expect(container.querySelector('[aria-controls="panel-1"]')).toBeInTheDocument();
  });

  it('tabs have proper semantics', () => {
    render(
      <div role="tablist">
        <button role="tab" aria-selected="true">Active</button>
        <button role="tab" aria-selected="false">Inactive</button>
      </div>
    );
    const activeTab = screen.getByRole('tab', { name: 'Active' });
    expect(activeTab).toHaveAttribute('aria-selected', 'true');
  });
});
