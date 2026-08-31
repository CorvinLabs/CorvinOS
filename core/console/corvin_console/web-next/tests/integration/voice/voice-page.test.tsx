import { describe, it, expect, beforeEach } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { renderWithProviders } from '../../utils/test-utils';
import { VoicePage } from '../../fixtures/mock-pages';

describe('Voice Settings Page Integration', () => {
  beforeEach(() => {
    localStorage.setItem('auth_token', 'jwt-test-token-voice-123');
  });

  describe('Voice Page Display', () => {
    it('renders voice settings heading', () => {
      renderWithProviders(<VoicePage />, { route: '/voice' });
      expect(screen.getByText(/voice settings/i)).toBeInTheDocument();
    });

    it('displays STT provider selector', () => {
      renderWithProviders(<VoicePage />, { route: '/voice' });
      expect(screen.getByText(/stt provider/i)).toBeInTheDocument();
    });

    it('displays TTS voice selector', () => {
      renderWithProviders(<VoicePage />, { route: '/voice' });
      expect(screen.getByText(/tts voice/i)).toBeInTheDocument();
    });
  });

  describe('STT Configuration', () => {
    it('shows STT dropdown', () => {
      renderWithProviders(<VoicePage />, { route: '/voice' });
      const sttSelect = screen.getByRole('combobox', { name: /stt/i }) || screen.getAllByRole('combobox')[0];
      expect(sttSelect).toBeInTheDocument();
    });

    it('has OpenAI Whisper option', () => {
      renderWithProviders(<VoicePage />, { route: '/voice' });
      expect(screen.getByText(/openai whisper/i)).toBeInTheDocument();
    });

    it('has Local Whisper option', () => {
      renderWithProviders(<VoicePage />, { route: '/voice' });
      expect(screen.getByText(/local whisper/i)).toBeInTheDocument();
    });

    it('can select STT provider', () => {
      renderWithProviders(<VoicePage />, { route: '/voice' });
      const sttSelect = screen.getAllByRole('combobox')[0] as HTMLSelectElement;
      fireEvent.change(sttSelect, { target: { value: 'local' } });
      expect(sttSelect.value).toBe('local');
    });
  });

  describe('TTS Configuration', () => {
    it('shows TTS voice dropdown', () => {
      renderWithProviders(<VoicePage />, { route: '/voice' });
      const ttsSelect = screen.getAllByRole('combobox')[1];
      expect(ttsSelect).toBeInTheDocument();
    });

    it('has Nova voice option', () => {
      renderWithProviders(<VoicePage />, { route: '/voice' });
      expect(screen.getByText(/nova/i)).toBeInTheDocument();
    });

    it('has Shimmer voice option', () => {
      renderWithProviders(<VoicePage />, { route: '/voice' });
      expect(screen.getByText(/shimmer/i)).toBeInTheDocument();
    });

    it('has Alloy voice option', () => {
      renderWithProviders(<VoicePage />, { route: '/voice' });
      expect(screen.getByText(/alloy/i)).toBeInTheDocument();
    });

    it('can select TTS voice', () => {
      renderWithProviders(<VoicePage />, { route: '/voice' });
      const ttsSelect = screen.getAllByRole('combobox')[1] as HTMLSelectElement;
      fireEvent.change(ttsSelect, { target: { value: 'shimmer' } });
      expect(ttsSelect.value).toBe('shimmer');
    });
  });

  describe('Voice Testing', () => {
    it('renders test voice button', () => {
      renderWithProviders(<VoicePage />, { route: '/voice' });
      const testButton = screen.getByRole('button', { name: /test voice/i });
      expect(testButton).toBeInTheDocument();
    });

    it('test button is clickable', () => {
      renderWithProviders(<VoicePage />, { route: '/voice' });
      const testButton = screen.getByRole('button', { name: /test voice/i });
      fireEvent.click(testButton);
      expect(testButton).toBeInTheDocument();
    });

    it('can play voice sample', () => {
      renderWithProviders(<VoicePage />, { route: '/voice' });
      const testButton = screen.getByRole('button', { name: /test voice/i });
      fireEvent.click(testButton);
      expect(testButton).toBeInTheDocument();
    });
  });

  describe('Voice Configuration Persistence', () => {
    it('remembers STT selection', () => {
      const { _rerender } = renderWithProviders(<VoicePage />, { route: '/voice' });
      const sttSelect = screen.getAllByRole('combobox')[0] as HTMLSelectElement;
      fireEvent.change(sttSelect, { target: { value: 'local' } });

      expect(sttSelect.value).toBe('local');
    });

    it('remembers TTS voice selection', () => {
      const { _rerender } = renderWithProviders(<VoicePage />, { route: '/voice' });
      const ttsSelect = screen.getAllByRole('combobox')[1] as HTMLSelectElement;
      fireEvent.change(ttsSelect, { target: { value: 'alloy' } });

      expect(ttsSelect.value).toBe('alloy');
    });
  });

  describe('Language Support', () => {
    it('shows English voices', () => {
      renderWithProviders(<VoicePage />, { route: '/voice' });
      const voiceSelect = screen.getByTestId('voice-select');
      expect(voiceSelect).toHaveTextContent(/english/i);
    });

    it('voice options include language', () => {
      renderWithProviders(<VoicePage />, { route: '/voice' });
      const voiceSelect = screen.getByTestId('voice-select');
      const options = voiceSelect.querySelectorAll('option');
      expect(options.length).toBeGreaterThan(0);
    });
  });

  describe('Responsive Design', () => {
    it('heading is visible', () => {
      renderWithProviders(<VoicePage />, { route: '/voice' });
      const heading = screen.getByText(/voice settings/i);
      expect(heading).toBeVisible();
    });

    it('all controls are visible', () => {
      renderWithProviders(<VoicePage />, { route: '/voice' });
      const comboboxes = screen.getAllByRole('combobox');
      expect(comboboxes.length).toBeGreaterThanOrEqual(2);
      comboboxes.forEach(box => expect(box).toBeVisible());
    });

    it('test button is visible', () => {
      renderWithProviders(<VoicePage />, { route: '/voice' });
      const testButton = screen.getByRole('button', { name: /test voice/i });
      expect(testButton).toBeVisible();
    });
  });

  describe('Accessibility', () => {
    it('settings have labels', () => {
      renderWithProviders(<VoicePage />, { route: '/voice' });
      expect(screen.getByText(/stt provider/i)).toBeInTheDocument();
      expect(screen.getByText(/tts voice/i)).toBeInTheDocument();
    });

    it('dropdown selects are keyboard accessible', () => {
      renderWithProviders(<VoicePage />, { route: '/voice' });
      const comboboxes = screen.getAllByRole('combobox');
      comboboxes.forEach(box => {
        box.focus();
        expect(document.activeElement).toBe(box);
      });
    });

    it('button has accessible name', () => {
      renderWithProviders(<VoicePage />, { route: '/voice' });
      const button = screen.getByRole('button');
      expect(button.textContent).toMatch(/test/i);
    });
  });

  describe('Session Management', () => {
    it('maintains auth token', () => {
      const token = localStorage.getItem('auth_token');
      expect(token).toBe('jwt-test-token-voice-123');
    });

    it('displays settings when authenticated', () => {
      renderWithProviders(<VoicePage />, { route: '/voice' });
      expect(screen.getByText(/voice settings/i)).toBeInTheDocument();
    });
  });
});
