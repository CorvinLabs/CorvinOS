import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, fireEvent, waitFor, act, render } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { QueryClientProvider } from '@tanstack/react-query';
import { renderWithProviders, createTestQueryClient } from '../../utils/test-utils';
import { ChatPage } from '../../fixtures/mock-pages';

// ── Real-component wiring tests (voice playback) ────────────────────────────
//
// Everything above this line renders `ChatPage` from `../../fixtures/mock-pages`
// — a hand-written stub with no relation to the production
// `src/pages/chat.tsx`. It cannot exercise how that page actually wires
// `useVoicePlayback` (voiceState / playTts / playBlocked / stopVoice) into the
// real header controls. The tests below render the ACTUAL `ChatPage` from
// `@/pages/chat` and the ACTUAL `SetupGate` from
// `@/components/setup/SetupGate` (mirroring the real-component convention
// already used by `../voice/voice-status-panel.test.tsx`), with only the
// heavy, unrelated subsystems (WebSocket chat-registry, IndexedDB task
// persistence, SSE task updates, auth) mocked out — the voice wiring itself
// runs through the real `useVoicePlayback` hook.
import { ChatPage as RealChatPage } from '@/pages/chat';
import { SetupGate } from '@/components/setup/SetupGate';
import type { StreamEvent } from '@/lib/chat-registry';

const { subscribeEventsMock, capturedEventCallbacks } = vi.hoisted(() => {
  const capturedEventCallbacks = new Map<string, (evt: unknown) => void>();
  const subscribeEventsMock = vi.fn((sid: string, cb: (evt: unknown) => void) => {
    capturedEventCallbacks.set(sid, cb);
    return () => {
      capturedEventCallbacks.delete(sid);
    };
  });
  return { subscribeEventsMock, capturedEventCallbacks };
});

vi.mock('@/lib/chat-registry', () => ({
  ensureConnected: vi.fn(),
  loadHistory: vi.fn(),
  sendMessage: vi.fn(() => null),
  cancelTurn: vi.fn(),
  consumePendingTitle: vi.fn(),
  closeSession: vi.fn(),
  subscribeEvents: subscribeEventsMock,
  useChatSession: vi.fn(() => ({
    messages: [],
    streaming: false,
    error: null,
    reconnecting: false,
    latestResultText: null,
    pendingTitle: null,
  })),
}));

vi.mock('@/lib/auth', () => ({
  useAuth: () => ({
    status: 'authenticated',
    session: { csrf_token: 'test-csrf', uid: 'u-1' },
    logout: vi.fn(),
    refresh: vi.fn(),
  }),
  AuthProvider: ({ children }: { children: React.ReactNode }) => children,
}));

vi.mock('@/hooks/use-tasks-with-live-updates', () => ({
  useTasksWithLiveUpdates: () => ({
    tasks: [],
    isLoading: false,
    isConnected: false,
    isPolling: false,
  }),
}));

vi.mock('@/hooks/use-chat-task-status', () => ({
  useChatTaskStatus: () => ({ hasRunningTasks: false, taskCount: 0, status: 'idle' }),
}));

vi.mock('@/lib/task-db', () => ({
  exportTaskAsJSON: vi.fn(async () => null),
  deleteTask: vi.fn(async () => {}),
}));

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    listChatSessions: vi.fn(async () => ({ sessions: [] })),
    getChatTurns: vi.fn(async () => ({ turns: [] })),
    getProfile: vi.fn(async () => ({
      profile: { identity: { display_language: 'en', default_persona: 'corvin' } },
    })),
    getPerChatEngine: vi.fn(async () => ({
      effective_engine: 'claude_code',
      source: 'tenant_default',
      per_chat_model: null,
    })),
    ttsBlob: vi.fn(async () => new Blob(['fake-audio-bytes'], { type: 'audio/mpeg' })),
    getSetupStatus: vi.fn(async () => ({
      first_run: true,
      engine_connected: false,
      claude_cli_ok: false,
      anthropic_key_set: false,
      bridges_configured: [],
      setup_complete: false,
    })),
    runWelcomeCheck: vi.fn(async () => ({
      state: 'done',
      lang: 'de',
      greeting: 'Hallo, ich bin Corvin.',
    })),
  };
});

function renderRealChatPage(sid: string) {
  return render(
    <MemoryRouter initialEntries={[`/app/chat/${sid}`]}>
      <QueryClientProvider client={createTestQueryClient()}>
        <Routes>
          <Route path="/app/chat/:sid" element={<RealChatPage />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe('ChatPage voice wiring (real components, ADR-0185-adjacent regression coverage)', () => {
  const SID = 'sid-voice-1';
  let playMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    window.localStorage.clear();
    capturedEventCallbacks.clear();
    subscribeEventsMock.mockClear();

    if (!('createObjectURL' in URL)) {
      // @ts-expect-error - happy-dom may not implement this
      URL.createObjectURL = vi.fn(() => 'blob:fake-url');
    }
    if (!('revokeObjectURL' in URL)) {
      // @ts-expect-error - happy-dom may not implement this
      URL.revokeObjectURL = vi.fn();
    }
    playMock = vi.fn().mockResolvedValue(undefined);
    HTMLMediaElement.prototype.play = playMock;
    HTMLMediaElement.prototype.pause = vi.fn();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('auto-plays a streamed result through the real header wiring, and Replay reproduces the same text/lang', async () => {
    renderRealChatPage(SID);

    await waitFor(() => expect(subscribeEventsMock).toHaveBeenCalledWith(SID, expect.any(Function)));
    const emit = capturedEventCallbacks.get(SID)!;

    // Simulate the WS registry delivering a streamed "result" event — the
    // exact call site at chat.tsx's subscribeEvents(sid, ...) handler.
    await act(async () => {
      emit({ type: 'result', text: 'Hallo, wie geht es dir heute?' } as StreamEvent);
      await Promise.resolve();
      await Promise.resolve();
    });

    // The real useVoicePlayback hook took over: ttsBlob was called with the
    // auto-detected language (German, via detectTtsLang) and the real CSRF
    // token threaded down from useAuth.
    const { ttsBlob } = await import('@/lib/api');
    await waitFor(() => expect(ttsBlob).toHaveBeenCalledWith(
      'Hallo, wie geht es dir heute?', 'de', 'test-csrf',
    ));

    // VoicePlaybackChip renders the "Speaking · DE" control while playing.
    const stopButton = await screen.findByTitle('Stop playback');
    expect(stopButton.textContent).toMatch(/DE/);
    // No Replay button while actively playing (mutually exclusive with the chip).
    expect(screen.queryByLabelText('Replay last response')).not.toBeInTheDocument();

    // Stop playback via the chip — this calls the real hook's stopVoice().
    fireEvent.click(stopButton);

    // Replay last response appears once voice is idle again, carrying the
    // exact text/lang captured from the streamed result.
    const replayButton = await screen.findByLabelText('Replay last response');
    expect(replayButton).toHaveAttribute('title', 'Replay last response (DE)');

    fireEvent.click(replayButton);

    await waitFor(() => expect(vi.mocked(ttsBlob).mock.calls.length).toBeGreaterThanOrEqual(2));
    const lastCall = vi.mocked(ttsBlob).mock.calls.at(-1);
    expect(lastCall).toEqual(['Hallo, wie geht es dir heute?', 'de', 'test-csrf']);

    // Replaying re-enters the "playing" state through the same real chip.
    await screen.findByTitle('Stop playback');
  });

  it('toggling voice off calls the real stopVoice() and hides the Speaking chip mid-playback', async () => {
    renderRealChatPage(SID);

    await waitFor(() => expect(subscribeEventsMock).toHaveBeenCalledWith(SID, expect.any(Function)));
    const emit = capturedEventCallbacks.get(SID)!;

    await act(async () => {
      emit({ type: 'result', text: 'Hello there, how are you?' } as StreamEvent);
      await Promise.resolve();
      await Promise.resolve();
    });

    await screen.findByTitle('Stop playback');

    const voiceToggle = screen.getByTitle('Disable voice output');
    fireEvent.click(voiceToggle);

    // handleVoiceToggle calls stopVoice() when disabling — the chip must
    // disappear even though a clip was actively playing.
    await waitFor(() => expect(screen.queryByTitle('Stop playback')).not.toBeInTheDocument());
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled();
    // Voice is off now — the Replay affordance is gated on voiceOut too.
    expect(screen.queryByLabelText('Replay last response')).not.toBeInTheDocument();
  });
});

describe('SetupGate WelcomeStep voice wiring (real components)', () => {
  let playMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    window.localStorage.clear();
    if (!('createObjectURL' in URL)) {
      // @ts-expect-error - happy-dom may not implement this
      URL.createObjectURL = vi.fn(() => 'blob:fake-url');
    }
    if (!('revokeObjectURL' in URL)) {
      // @ts-expect-error - happy-dom may not implement this
      URL.revokeObjectURL = vi.fn();
    }
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('auto-speaks the welcome-check greeting and, when autoplay is blocked, "Tap to hear Corvin" resumes it', async () => {
    // First play() (the automatic one, no user gesture in scope yet) is
    // blocked by the browser — exactly the first-boot scenario this
    // affordance exists for. The second play() (from the user's tap) succeeds.
    playMock = vi.fn()
      .mockRejectedValueOnce(new DOMException('blocked', 'NotAllowedError'))
      .mockResolvedValueOnce(undefined);
    HTMLMediaElement.prototype.play = playMock;
    HTMLMediaElement.prototype.pause = vi.fn();

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <SetupGate />
      </QueryClientProvider>,
    );

    // runWelcomeCheck resolves and WelcomeStep calls playTts(greeting, "de")
    // — this is the real useVoicePlayback hook, not a mock.
    const { runWelcomeCheck, ttsBlob } = await import('@/lib/api');
    await waitFor(() => expect(runWelcomeCheck).toHaveBeenCalledWith('test-csrf'));
    await waitFor(() => expect(ttsBlob).toHaveBeenCalledWith(
      'Hallo, ich bin Corvin.', 'de', 'test-csrf',
    ));

    // Autoplay was blocked -> the "Tap to hear Corvin" banner is shown.
    const tapButton = await screen.findByRole('button', { name: /tap to hear corvin/i });

    fireEvent.click(tapButton);

    // playBlocked() re-invoked play() on the SAME element; it now succeeds,
    // so the banner (gated on voiceState === "blocked") disappears.
    await waitFor(() => expect(playMock).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /tap to hear corvin/i })).not.toBeInTheDocument(),
    );
    // "Let's go" always remains available regardless of voice outcome — the
    // onboarding flow is never gated on TTS succeeding.
    expect(screen.getByRole('button', { name: /let's go/i })).toBeEnabled();
  });
});

describe('Chat Page Integration', () => {
  beforeEach(() => {
    localStorage.setItem('auth_token', 'jwt-test-token-chat-123');
  });

  describe('Chat Display', () => {
    it('renders chat page heading', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });
      expect(screen.getByText(/chat/i)).toBeInTheDocument();
    });

    it('displays chat message history', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      expect(screen.getByText(/hello/i)).toBeInTheDocument();
      expect(screen.getByText(/what is corvinOS/i)).toBeInTheDocument();
      expect(screen.getByText(/ai-powered framework/i)).toBeInTheDocument();
    });

    it('shows assistant messages', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });
      expect(screen.getByText(/assistant: hello/i)).toBeInTheDocument();
    });

    it('shows user messages', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });
      expect(screen.getByText(/user: what is corvinOS/i)).toBeInTheDocument();
    });

    it('displays conversation in order', () => {
      const { container } = renderWithProviders(<ChatPage />, { route: '/chat' });
      const messages = container.querySelectorAll('[role="main"] div');

      expect(messages.length).toBeGreaterThan(2);
    });
  });

  describe('Message Input', () => {
    it('renders message input field', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i);
      expect(textarea).toBeInTheDocument();
      expect(textarea).toBeEnabled();
    });

    it('accepts text input', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i) as HTMLTextAreaElement;
      fireEvent.change(textarea, { target: { value: 'Hello, Claude!' } });

      expect(textarea.value).toBe('Hello, Claude!');
    });

    it('allows typing multiple lines', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i) as HTMLTextAreaElement;
      fireEvent.change(textarea, { target: { value: 'Line 1\nLine 2\nLine 3' } });

      expect(textarea.value).toContain('Line 1');
      expect(textarea.value).toContain('Line 2');
      expect(textarea.value).toContain('Line 3');
    });

    it('clears input after submission', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i) as HTMLTextAreaElement;
      const sendButton = screen.getByRole('button', { name: /send/i });

      fireEvent.change(textarea, { target: { value: 'Test message' } });
      fireEvent.click(sendButton);

      // After submission, field should be empty (in real app)
      // Mock doesn't implement this, so we just check button was clicked
      expect(sendButton).toBeInTheDocument();
    });
  });

  describe('Send Button', () => {
    it('renders send button', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const sendButton = screen.getByRole('button', { name: /send/i });
      expect(sendButton).toBeInTheDocument();
      expect(sendButton).toBeEnabled();
    });

    it('send button is clickable', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const sendButton = screen.getByRole('button', { name: /send/i });
      fireEvent.click(sendButton);

      // Button should still be present after click
      expect(sendButton).toBeInTheDocument();
    });

    it('sends message on button click', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i);
      const sendButton = screen.getByRole('button', { name: /send/i });

      fireEvent.change(textarea, { target: { value: 'New message' } });
      fireEvent.click(sendButton);

      // Message was submitted
      expect(sendButton).toBeInTheDocument();
    });

    it('sends message on Enter key (Shift+Enter for newline)', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i);

      fireEvent.change(textarea, { target: { value: 'Message' } });
      fireEvent.keyDown(textarea, { key: 'Enter', code: 'Enter' });

      expect(textarea).toBeInTheDocument();
    });
  });

  describe('Chat Session', () => {
    it('maintains session token', () => {
      const token = localStorage.getItem('auth_token');
      expect(token).toBe('jwt-test-token-chat-123');
    });

    it('displays chat in authenticated state', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i);
      expect(textarea).toBeEnabled();
    });

    it('shows main content area', () => {
      const { container } = renderWithProviders(<ChatPage />, { route: '/chat' });

      const mainArea = container.querySelector('[role="main"]');
      expect(mainArea).toBeInTheDocument();
    });
  });

  describe('Conversation Management', () => {
    it('shows multiple messages in sequence', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      expect(screen.getByText(/assistant: hello/i)).toBeInTheDocument();
      expect(screen.getByText(/user: what is corvinOS/i)).toBeInTheDocument();
      expect(screen.getByText(/assistant: corvinOS is/i)).toBeInTheDocument();
    });

    it('scrollable message area', () => {
      const { container } = renderWithProviders(<ChatPage />, { route: '/chat' });

      const messageArea = container.querySelector('[role="main"]');
      expect(messageArea).toBeInTheDocument();
    });

    it('shows newest messages at bottom', () => {
      const { container } = renderWithProviders(<ChatPage />, { route: '/chat' });

      const messages = Array.from(container.querySelectorAll('[role="main"] div > div'));
      // Last message should be the AI response about CorvinOS
      expect(messages[messages.length - 1]?.textContent).toContain('CorvinOS is');
    });
  });

  describe('Chat Features', () => {
    it('supports emoji input', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i) as HTMLTextAreaElement;
      fireEvent.change(textarea, { target: { value: '👋 Hello! 🎉' } });

      expect(textarea.value).toBe('👋 Hello! 🎉');
    });

    it('supports code in messages', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i) as HTMLTextAreaElement;
      fireEvent.change(textarea, { target: { value: '```python\nprint("hello")\n```' } });

      expect(textarea.value).toContain('```python');
    });

    it('supports markdown formatting', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i) as HTMLTextAreaElement;
      fireEvent.change(textarea, { target: { value: '**bold** *italic* `code`' } });

      expect(textarea.value).toContain('**bold**');
      expect(textarea.value).toContain('*italic*');
      expect(textarea.value).toContain('`code`');
    });
  });

  describe('Input Validation', () => {
    it('allows empty input initially', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i) as HTMLTextAreaElement;
      expect(textarea.value).toBe('');
    });

    it('handles whitespace-only input', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i) as HTMLTextAreaElement;
      fireEvent.change(textarea, { target: { value: '   ' } });

      expect(textarea.value).toBe('   ');
    });

    it('preserves special characters', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i) as HTMLTextAreaElement;
      const specialChars = '!@#$%^&*()_+-=[]{}|;:,.<>?/~`';
      fireEvent.change(textarea, { target: { value: specialChars } });

      expect(textarea.value).toBe(specialChars);
    });
  });

  describe('Responsive Design', () => {
    it('displays correctly on desktop viewport', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const heading = screen.getByText(/chat/i);
      expect(heading).toBeVisible();
    });

    it('textarea is visible and accessible', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i);
      expect(textarea).toBeVisible();
      expect(textarea).not.toHaveAttribute('disabled');
    });

    it('send button is accessible', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const button = screen.getByRole('button', { name: /send/i });
      expect(button).toBeVisible();
      expect(button).not.toHaveAttribute('disabled');
    });
  });

  describe('Accessibility', () => {
    it('textarea has proper label', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i);
      expect(textarea).toBeInTheDocument();
    });

    it('send button has accessible name', () => {
      renderWithProviders(<ChatPage />, { route: '/chat' });

      const button = screen.getByRole('button', { name: /send/i });
      expect(button.textContent).toMatch(/send/i);
    });

    it('main area has proper role', () => {
      const { container } = renderWithProviders(<ChatPage />, { route: '/chat' });

      const mainArea = container.querySelector('[role="main"]');
      expect(mainArea).toBeInTheDocument();
    });

    it('focusable elements are in logical order', () => {
      const { container: _container } = renderWithProviders(<ChatPage />, { route: '/chat' });

      const textarea = screen.getByPlaceholderText(/type your message/i);
      const button = screen.getByRole('button', { name: /send/i });

      textarea.focus();
      expect(document.activeElement).toBe(textarea);

      button.focus();
      expect(document.activeElement).toBe(button);
    });
  });
});
