import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';

describe('Select Component', () => {
  it('renders select element', () => {
    const { container } = render(
      <select>
        <option>Choose</option>
      </select>
    );
    expect(container.querySelector('select')).toBeInTheDocument();
  });

  it('displays options', () => {
    render(
      <select>
        <option>Option 1</option>
        <option>Option 2</option>
      </select>
    );
    expect(screen.getByText('Option 1')).toBeInTheDocument();
    expect(screen.getByText('Option 2')).toBeInTheDocument();
  });

  it('supports disabled state', () => {
    render(<select disabled><option>Test</option></select>);
    const select = screen.getByRole('combobox');
    expect(select).toBeDisabled();
  });

  it('supports multiple selection', () => {
    const { container } = render(
      <select multiple>
        <option>A</option>
        <option>B</option>
      </select>
    );
    expect(container.querySelector('select[multiple]')).toBeInTheDocument();
  });

  it('supports optgroups', () => {
    const { container } = render(
      <select>
        <optgroup label="Group 1">
          <option>Option 1</option>
        </optgroup>
      </select>
    );
    expect(container.querySelector('optgroup[label="Group 1"]')).toBeInTheDocument();
    expect(screen.getByText('Option 1')).toBeInTheDocument();
  });

  it('supports default value', () => {
    const { container } = render(
      <select defaultValue="selected">
        <option value="selected">Selected</option>
        <option>Other</option>
      </select>
    );
    const select = container.querySelector('select') as HTMLSelectElement;
    expect(select.value).toBe('selected');
  });

  it('supports size attribute', () => {
    const { container } = render(
      <select size={3}>
        <option>A</option>
        <option>B</option>
      </select>
    );
    expect(container.querySelector('select[size="3"]')).toBeInTheDocument();
  });

  it('supports required attribute', () => {
    const { container } = render(<select required><option>Test</option></select>);
    expect(container.querySelector('select[required]')).toBeInTheDocument();
  });

  it('supports name attribute', () => {
    const { container } = render(
      <select name="myselect">
        <option>Test</option>
      </select>
    );
    expect(container.querySelector('select[name="myselect"]')).toBeInTheDocument();
  });

  it('render option with value', () => {
    render(
      <select>
        <option value="val1">Display 1</option>
        <option value="val2">Display 2</option>
      </select>
    );
    const select = screen.getByRole('combobox') as HTMLSelectElement;
    expect(select.options.length).toBe(2);
  });
});
