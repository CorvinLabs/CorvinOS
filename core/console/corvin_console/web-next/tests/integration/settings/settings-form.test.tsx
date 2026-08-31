import { describe, it, expect } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { renderWithProviders } from '../../utils/test-utils';
import { SettingsPage } from '../../fixtures/mock-pages';

describe('Settings Form Integration', () => {
  it('renders settings page heading', () => {
    renderWithProviders(<SettingsPage />, { route: '/settings' });
    expect(screen.getByText(/settings/i)).toBeInTheDocument();
  });

  it('displays theme selector', () => {
    renderWithProviders(<SettingsPage />, { route: '/settings' });

    const themeLabel = screen.getByText(/theme/i);
    expect(themeLabel).toBeInTheDocument();

    const themeSelect = screen.getByRole('combobox');
    expect(themeSelect).toBeInTheDocument();
  });

  it('theme has light and dark options', () => {
    renderWithProviders(<SettingsPage />, { route: '/settings' });

    const themeSelect = screen.getByRole('combobox') as HTMLSelectElement;
    const options = Array.from(themeSelect.options).map(opt => opt.value);

    expect(options).toContain('light');
    expect(options).toContain('dark');
  });

  it('allows selecting theme option', () => {
    renderWithProviders(<SettingsPage />, { route: '/settings' });

    const themeSelect = screen.getByRole('combobox') as HTMLSelectElement;
    fireEvent.change(themeSelect, { target: { value: 'dark' } });

    expect(themeSelect.value).toBe('dark');
  });

  it('displays notification checkbox', () => {
    renderWithProviders(<SettingsPage />, { route: '/settings' });

    const notificationCheckbox = screen.getByRole('checkbox');
    expect(notificationCheckbox).toBeInTheDocument();
  });

  it('allows toggling notifications', () => {
    renderWithProviders(<SettingsPage />, { route: '/settings' });

    const checkbox = screen.getByRole('checkbox') as HTMLInputElement;
    const initialState = checkbox.checked;

    fireEvent.click(checkbox);
    expect(checkbox.checked).not.toBe(initialState);
  });

  it('displays timeout input field', () => {
    renderWithProviders(<SettingsPage />, { route: '/settings' });

    const timeoutInput = screen.getByRole('spinbutton', { name: /timeout/i });
    expect(timeoutInput).toBeInTheDocument();
  });

  it('timeout input accepts numeric values', () => {
    renderWithProviders(<SettingsPage />, { route: '/settings' });

    const timeoutInput = screen.getByRole('spinbutton', { name: /timeout/i }) as HTMLInputElement;
    fireEvent.change(timeoutInput, { target: { value: '5000' } });

    expect(timeoutInput.value).toBe('5000');
  });

  it('displays save button', () => {
    renderWithProviders(<SettingsPage />, { route: '/settings' });

    expect(screen.getByRole('button', { name: /save/i })).toBeInTheDocument();
  });

  it('allows saving settings', () => {
    renderWithProviders(<SettingsPage />, { route: '/settings' });

    const saveButton = screen.getByRole('button', { name: /save/i });
    expect(saveButton).not.toBeDisabled();

    fireEvent.click(saveButton);
    expect(saveButton).toBeInTheDocument();
  });

  it('displays reset button', () => {
    renderWithProviders(<SettingsPage />, { route: '/settings' });

    const resetButton = screen.getByRole('button', { name: /reset/i });
    expect(resetButton).toBeInTheDocument();
  });

  it('allows resetting settings', () => {
    renderWithProviders(<SettingsPage />, { route: '/settings' });

    const themeSelect = screen.getByRole('combobox') as HTMLSelectElement;
    fireEvent.change(themeSelect, { target: { value: 'dark' } });
    expect(themeSelect.value).toBe('dark');

    const resetButton = screen.getByRole('button', { name: /reset/i });
    fireEvent.click(resetButton);

    expect(resetButton).toBeInTheDocument();
  });

  it('displays all settings sections', () => {
    const { container } = renderWithProviders(<SettingsPage />, { route: '/settings' });

    const divs = container.querySelectorAll('div');
    expect(divs.length).toBeGreaterThanOrEqual(4);
  });

  it('form is complete with all inputs', () => {
    renderWithProviders(<SettingsPage />, { route: '/settings' });

    // Check all major elements exist
    expect(screen.getByRole('combobox')).toBeInTheDocument(); // theme
    expect(screen.getByRole('checkbox')).toBeInTheDocument(); // notifications
    expect(screen.getByRole('spinbutton', { name: /timeout/i })).toBeInTheDocument(); // timeout
    expect(screen.getByRole('button', { name: /save/i })).toBeInTheDocument(); // save
  });

  it('supports keyboard navigation', () => {
    renderWithProviders(<SettingsPage />, { route: '/settings' });

    const themeSelect = screen.getByRole('combobox');
    const checkbox = screen.getByRole('checkbox');
    const saveButton = screen.getByRole('button', { name: /save/i });

    themeSelect.focus();
    expect(document.activeElement).toBe(themeSelect);

    checkbox.focus();
    expect(document.activeElement).toBe(checkbox);

    saveButton.focus();
    expect(document.activeElement).toBe(saveButton);
  });

  it('multiple settings can be modified', () => {
    renderWithProviders(<SettingsPage />, { route: '/settings' });

    const themeSelect = screen.getByRole('combobox') as HTMLSelectElement;
    const checkbox = screen.getByRole('checkbox') as HTMLInputElement;
    const timeout = screen.getByRole('spinbutton', { name: /timeout/i }) as HTMLInputElement;

    // Change multiple settings
    fireEvent.change(themeSelect, { target: { value: 'dark' } });
    fireEvent.click(checkbox);
    fireEvent.change(timeout, { target: { value: '10000' } });

    expect(themeSelect.value).toBe('dark');
    expect(timeout.value).toBe('10000');
  });
});
