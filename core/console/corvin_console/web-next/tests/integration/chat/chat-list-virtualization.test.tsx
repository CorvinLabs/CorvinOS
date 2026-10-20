import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, fireEvent, render, act } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { QueryClientProvider } from '@tanstack/react-query';
import { createTestQueryClient } from '../../utils/test-utils';
import { installFakeScrollLayout } from '../../utils/fake-scroll-layout';
import { ChatPage as RealChatPage } from '@/pages/chat';
import { useChatSession } from '@/lib/chat-registry';
import type { ChatMessage } from '@/lib/chat-registry';

// Mounted-row bounds for a long session. The list must mount only the rows
// around the viewport, however long the session is, so the DOM does not grow
// with the conversation (see ChatMessageRows / useVirtualList).

vi.mock('@/lib/chat-registry', () => ({
  ensureConnected: vi.fn(),
  loadHistory: vi.fn(),
  registerHistoryReader: vi.fn(),
  refreshHistory: vi.fn(),
  sendMessage: vi.fn(() => null),
  cancelTurn: vi.fn(),
  consumePendingTitle: vi.fn(),
  closeSession: vi.fn(),
  subscribeEvents: vi.fn(() => () => {}),
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
  useTasksWithLiveUpdates: () => ({ tasks: [], isLoading: false, isConnected: false, isPolling: false }),
}));

vi.mock('@/hooks/use-chat-task-status', () => ({
  useChatTaskStatus: () => ({ phase: 'idle', task: null, activeCount: 0, nowS: 0 }),
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
    getSetupStatus: vi.fn(async () => ({
      first_run: false,
      engine_connected: true,
      claude_cli_ok: true,
      anthropic_key_set: true,
      bridges_configured: [],
      setup_complete: true,
    })),
  };
});

function longChat(n: number): ChatMessage[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `m${i}`,
    role: i % 2 ? 'assistant' : 'user',
    ts: 1_700_000_000 + i * 600,
    parts: [{ kind: 'text', text: `message-body-${i}` }],
  })) as ChatMessage[];
}

function setMessages(messages: ChatMessage[], streaming = false) {
  vi.mocked(useChatSession).mockReturnValue({
    messages,
    streaming,
    error: null,
    reconnecting: false,
    latestResultText: null,
    pendingTitle: null,
    steerOpen: false,
    bgOpen: 0,
    bgChildren: [],
    bgChildrenAt: null,
  } as unknown as ReturnType<typeof useChatSession>);
}

function renderChat(sid: string) {
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

const mountedRows = () => document.querySelectorAll('[data-row-key]').length;

describe('ChatPage message list virtualisation (real components)', () => {
  let restoreLayout: () => void;
  beforeEach(() => {
    restoreLayout = installFakeScrollLayout({ rowHeight: 120, viewport: 800 });
  });
  afterEach(() => {
    restoreLayout();
  });

  it('keeps the mounted rows bounded for a 3000-message session and still shows the newest message', async () => {
    setMessages(longChat(3000));
    renderChat('sid-virt-3000');

    expect(await screen.findByText('message-body-2999')).toBeTruthy();
    expect(screen.queryByText('message-body-0')).toBeNull();
    // Viewport 800 px + 1000 px overscan each side at 120 px per row ≈ 19 rows.
    expect(mountedRows()).toBeGreaterThan(0);
    expect(mountedRows()).toBeLessThanOrEqual(40);
  });

  it('mounts the same number of rows for a 300-message session as for 3000', async () => {
    setMessages(longChat(300));
    const first = renderChat('sid-virt-300');
    await screen.findByText('message-body-299');
    const small = mountedRows();
    first.unmount();

    setMessages(longChat(3000));
    renderChat('sid-virt-3000b');
    await screen.findByText('message-body-2999');
    expect(mountedRows()).toBeLessThanOrEqual(40);
    expect(Math.abs(mountedRows() - small)).toBeLessThanOrEqual(2);
  });

  it('caps the listed history: "Show older" stops at the cap and the button goes away', async () => {
    setMessages(longChat(3000));
    renderChat('sid-virt-cap');
    await screen.findByText('message-body-2999');

    // 60 initially shown; each click reveals 100 more until the 1000-message cap.
    for (let k = 0; k < 15 && screen.queryByTestId('show-older-messages'); k++) {
      fireEvent.click(screen.getByTestId('show-older-messages'));
    }
    expect(screen.queryByTestId('show-older-messages')).toBeNull();
    expect(mountedRows()).toBeLessThanOrEqual(40);
  });

  it('shows a streamed delta in the last row and stays pinned to the bottom', async () => {
    const msgs = longChat(200);
    setMessages(msgs, true);
    renderChat('sid-virt-stream');
    await screen.findByText('message-body-199');

    // A delta replaces only the last message object; earlier message objects keep
    // their identity, so their memoised rows are not rebuilt (MessageRow is memo'd).
    const next = [...msgs.slice(0, -1), { ...msgs[199], parts: [{ kind: 'text', text: 'message-body-199 +delta' }] }] as ChatMessage[];
    await act(async () => {
      setMessages(next, true);
    });
    expect(await screen.findByText('message-body-199 +delta')).toBeTruthy();
    // Still pinned to the bottom (fake geometry: scrollTop is written to scrollHeight).
    const scroller = document.querySelector<HTMLElement>('.overflow-y-auto')!;
    expect(scroller.scrollTop).toBe(scroller.scrollHeight);
  });
});
