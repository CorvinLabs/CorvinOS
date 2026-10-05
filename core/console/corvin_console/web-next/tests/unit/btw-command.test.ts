/**
 * sendBtwNote must target the per-session console route (where the live
 * subprocess stdin is registered), never the old /v1/console/btw stub.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { sendBtwNote } from '@/lib/api/chat';

describe('sendBtwNote', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('POSTs the bare instruction to /chat/sessions/<sid>/btw', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true, status: 'injected' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const res = await sendBtwNote('abc/def', 'say BANANA', 'tok');

    expect(res.status).toBe('injected');
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/v1\/console\/chat\/sessions\/abc%2Fdef\/btw$/);
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ instruction: 'say BANANA' });
  });
});
