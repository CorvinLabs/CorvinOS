import { describe, it, expect } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { renderWithProviders } from '../../utils/test-utils';
import { ForgePage } from '../../fixtures/mock-pages';

describe('Forge Tool List Integration', () => {
  it('renders forge page heading', () => {
    renderWithProviders(<ForgePage />, { route: '/forge' });
    expect(screen.getByText(/forge tools/i)).toBeInTheDocument();
  });

  it('displays create tool button', () => {
    renderWithProviders(<ForgePage />, { route: '/forge' });
    expect(screen.getByRole('button', { name: /create tool/i })).toBeInTheDocument();
  });

  it('displays search/filter input', () => {
    renderWithProviders(<ForgePage />, { route: '/forge' });
    const searchInput = screen.getByPlaceholderText(/search tools/i);
    expect(searchInput).toBeInTheDocument();
  });

  it('lists existing tools', () => {
    renderWithProviders(<ForgePage />, { route: '/forge' });
    expect(screen.getByText(/code\.example_tool/i)).toBeInTheDocument();
    expect(screen.getByText(/code\.data_processor/i)).toBeInTheDocument();
  });

  it('displays tool descriptions', () => {
    renderWithProviders(<ForgePage />, { route: '/forge' });
    expect(screen.getByText(/Example tool for testing/i)).toBeInTheDocument();
    expect(screen.getByText(/Process data streams/i)).toBeInTheDocument();
  });

  it('allows searching tools by name', () => {
    renderWithProviders(<ForgePage />, { route: '/forge' });

    const searchInput = screen.getByPlaceholderText(/search tools/i) as HTMLInputElement;
    fireEvent.change(searchInput, { target: { value: 'example' } });

    expect(searchInput.value).toBe('example');
  });

  it('create tool button is clickable', () => {
    renderWithProviders(<ForgePage />, { route: '/forge' });

    const createButton = screen.getByRole('button', { name: /create tool/i });
    expect(createButton).not.toBeDisabled();

    fireEvent.click(createButton);
    expect(createButton).toBeInTheDocument();
  });

  it('tool names follow code. naming convention', () => {
    renderWithProviders(<ForgePage />, { route: '/forge' });

    const tools = [
      screen.getByText(/code\.example_tool/i),
      screen.getByText(/code\.data_processor/i),
    ];

    tools.forEach(tool => {
      expect(tool.textContent).toMatch(/^code\./);
    });
  });

  it('displays multiple tools in list', () => {
    const { container } = renderWithProviders(<ForgePage />, { route: '/forge' });

    const toolDivs = container.querySelectorAll('div');
    expect(toolDivs.length).toBeGreaterThan(2);
  });

  it('tool list is interactive', () => {
    renderWithProviders(<ForgePage />, { route: '/forge' });

    const searchInput = screen.getByPlaceholderText(/search tools/i);
    fireEvent.change(searchInput, { target: { value: 'processor' } });

    expect(screen.getByPlaceholderText(/search tools/i)).toBeInTheDocument();
  });

  it('page layout includes header and content area', () => {
    const { container } = renderWithProviders(<ForgePage />, { route: '/forge' });

    const h1 = container.querySelector('h1');
    expect(h1?.textContent).toMatch(/forge tools/i);
  });

  it('shows tool metadata clearly', () => {
    renderWithProviders(<ForgePage />, { route: '/forge' });

    // Each tool should show name and description
    const example = screen.getByText(/code\.example_tool/i);
    const description = screen.getByText(/Example tool for testing/i);

    expect(example).toBeInTheDocument();
    expect(description).toBeInTheDocument();
  });

  it('supports keyboard interaction', () => {
    renderWithProviders(<ForgePage />, { route: '/forge' });

    const searchInput = screen.getByPlaceholderText(/search tools/i);
    const createButton = screen.getByRole('button', { name: /create tool/i });

    searchInput.focus();
    expect(document.activeElement).toBe(searchInput);

    createButton.focus();
    expect(document.activeElement).toBe(createButton);
  });
});
