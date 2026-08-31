/**
 * Real-component test for the Voice provider-status panel (ADR-0185 M4).
 *
 * Unlike the fixture-based ../voice/voice-page.test.tsx (which renders a
 * hand-written mock replica of the Voice page, not the production
 * component), this test renders the ACTUAL `VoiceStatusPanel` exported
 * from `src/pages/voice.tsx` and drives it through MSW request handlers
 * against the real `/v1/console/voice/status` endpoint shape, so it
 * exercises the real fetch → useQuery → render path.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '../../fixtures/server';
import { renderWithProviders } from '../../utils/test-utils';
import { VoiceStatusPanel } from '@/pages/voice';

const ALL_READY = {
  stt: {
    local: {
      ready: true, package_installed: true, model_present: true,
      key_configured: null, detail: "ready (model 'tiny-q5_1')",
    },
    openai: {
      ready: true, package_installed: true, model_present: null,
      key_configured: true, detail: 'ready',
    },
  },
  tts: {
    openai: {
      ready: true, package_installed: true, model_present: null,
      key_configured: true, detail: 'ready',
    },
    edge: {
      ready: true, package_installed: true, model_present: null,
      key_configured: null, detail: 'ready (needs internet at synth time)',
    },
    piper: {
      ready: true, package_installed: true, model_present: true,
      key_configured: null, detail: 'ready',
    },
  },
};

const KEY_MISSING = {
  stt: {
    local: {
      ready: false, package_installed: false, model_present: null,
      key_configured: null, detail: 'pywhispercpp not installed',
    },
    openai: {
      ready: false, package_installed: true, model_present: null,
      key_configured: false, detail: 'no API key configured',
    },
  },
  tts: {
    openai: {
      ready: false, package_installed: true, model_present: null,
      key_configured: false, detail: 'no API key configured',
    },
    edge: {
      ready: false, package_installed: false, model_present: null,
      key_configured: null, detail: 'edge-tts not installed',
    },
    piper: {
      ready: false, package_installed: false, model_present: false,
      key_configured: null, detail: 'piper not installed',
    },
  },
};

const MODEL_MISSING = {
  stt: {
    local: {
      ready: false, package_installed: true, model_present: false,
      key_configured: null, detail: "model 'tiny-q5_1' not downloaded yet",
    },
    openai: {
      ready: false, package_installed: true, model_present: null,
      key_configured: false, detail: 'no API key configured',
    },
  },
  tts: {},
};

function mockVoiceStatus(body: unknown) {
  server.use(
    http.get('/v1/console/voice/status', () => HttpResponse.json(body)),
  );
}

describe('VoiceStatusPanel (ADR-0185 M4)', () => {
  afterEach(() => {
    server.resetHandlers();
  });

  it('renders a ready row for every provider when everything is configured', async () => {
    mockVoiceStatus(ALL_READY);
    renderWithProviders(<VoiceStatusPanel />);

    await waitFor(() => {
      expect(screen.getByText(/local stt/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/openai whisper/i)).toBeInTheDocument();
    expect(screen.getByText(/edge-tts/i)).toBeInTheDocument();
    expect(screen.getByText(/piper \(offline\)/i)).toBeInTheDocument();

    // "ready" appears both as the badge text and (for some rows) as the
    // detail copy itself — assert none of the 5 rows show "not configured".
    expect(screen.queryByText(/not configured/i)).not.toBeInTheDocument();
    expect(screen.getAllByText(/^ready$/i).length).toBeGreaterThanOrEqual(5);
    expect(screen.queryByText(/add key/i)).not.toBeInTheDocument();
  });

  it('shows "Add key" action when a provider needs an API key', async () => {
    mockVoiceStatus(KEY_MISSING);
    renderWithProviders(<VoiceStatusPanel />);

    await waitFor(() => {
      expect(screen.getAllByText(/not configured/i).length).toBeGreaterThan(0);
    });
    const addKeyLinks = screen.getAllByRole('link', { name: /add key/i });
    // openai STT + openai TTS both need a key.
    expect(addKeyLinks.length).toBe(2);
    addKeyLinks.forEach((link) => {
      expect(link).toHaveAttribute('href', '/app/api-keys');
    });
  });

  it('shows a model-download hint when the local model file is missing', async () => {
    mockVoiceStatus(MODEL_MISSING);
    renderWithProviders(<VoiceStatusPanel />);

    await waitFor(() => {
      expect(screen.getByText(/not downloaded yet/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/run corvin-install/i)).toBeInTheDocument();
  });

  it('degrades gracefully when the status endpoint errors', async () => {
    server.use(
      http.get('/v1/console/voice/status', () =>
        HttpResponse.json({ detail: 'boom' }, { status: 500 }),
      ),
    );
    renderWithProviders(<VoiceStatusPanel />);

    await waitFor(() => {
      expect(screen.getByText(/could not load provider status/i)).toBeInTheDocument();
    });
  });
});
