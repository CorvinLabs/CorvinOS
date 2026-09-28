/**
 * Voice Summaries page: the cross-chat library of automatically-generated,
 * persisted whole-session recaps (GET /v1/console/voice/summaries). Against
 * MSW — real fetch through the real api() client, no mocked hook layer.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";

import { VoiceSummariesPage } from "@/pages/voice-summaries";

const B = "/v1/console/voice/summaries";

function renderIt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <VoiceSummariesPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

afterEach(() => cleanup());

describe("Voice Summaries page", () => {
  it("shows an empty state when the tenant has no summaries yet", async () => {
    server.use(http.get(B, () => HttpResponse.json({ tenant_id: "_default", count: 0, summaries: [] })));
    renderIt();
    expect(await screen.findByText(/No voice summaries yet/i)).toBeInTheDocument();
  });

  it("lists each chat's summary with its recap text, language and a playable audio element", async () => {
    server.use(
      http.get(B, () =>
        HttpResponse.json({
          tenant_id: "_default",
          count: 2,
          summaries: [
            {
              sid: "sid-new",
              title: "Refactor the auth module",
              created_at: 1_790_000_000,
              lang: "de",
              text: "Ihr habt die Auth-Middleware überarbeitet.",
              audio_url: "/v1/console/chat/sessions/sid-new/workdir/voice-summary/session-summary.ogg",
            },
            {
              sid: "sid-old",
              title: "Plan the release",
              created_at: 1_789_000_000,
              lang: "en",
              text: "You planned next week's release.",
              audio_url: "/v1/console/chat/sessions/sid-old/workdir/voice-summary/session-summary.mp3",
            },
          ],
        }),
      ),
    );
    renderIt();

    expect(await screen.findByText("Refactor the auth module")).toBeInTheDocument();
    expect(screen.getByText("Plan the release")).toBeInTheDocument();
    expect(screen.getByText("Ihr habt die Auth-Middleware überarbeitet.")).toBeInTheDocument();
    // Badge text is lowercase "de"/"en" in the DOM; the CSS `uppercase`
    // utility class only changes the visual rendering, not the text node.
    expect(screen.getByText("de")).toBeInTheDocument();
    expect(screen.getByText("en")).toBeInTheDocument();

    // Real <audio> elements pointing at the server-persisted files — no
    // custom player, no fabricated waveform/duration.
    const players = document.querySelectorAll("audio");
    expect(players).toHaveLength(2);
    expect(players[0].getAttribute("src")).toBe(
      "/v1/console/chat/sessions/sid-new/workdir/voice-summary/session-summary.ogg",
    );

    // Opening the chat is a real navigation link, not a fabricated affordance.
    const link = screen.getByRole("link", { name: "Refactor the auth module" });
    expect(link.getAttribute("href")).toBe("/app/chat/sid-new");
  });

  it("shows an error state when the request fails", async () => {
    server.use(http.get(B, () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
    renderIt();
    await waitFor(() =>
      expect(screen.getByText(/Could not load voice summaries/i)).toBeInTheDocument(),
    );
  });
});
