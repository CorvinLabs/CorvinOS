/**
 * Voice Summaries page: the cross-chat library of automatically-generated,
 * persisted whole-session recaps (GET /v1/console/voice/summaries). Against
 * MSW — real fetch through the real api() client, no mocked hook layer.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";

import { VoiceSummariesPage } from "@/pages/voice-summaries";

const B = "/v1/console/voice/summaries";
const T = "/v1/console/voice/task-summaries";

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

// The page always asks for both lists; tests that care about task recaps
// override this empty default.
beforeEach(() => {
  server.use(http.get(T, () => HttpResponse.json({ tenant_id: "_default", count: 0, summaries: [] })));
});

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

describe("Voice Summaries page — per-task recaps", () => {
  const noChats = () =>
    server.use(http.get(B, () => HttpResponse.json({ tenant_id: "_default", count: 0, summaries: [] })));

  it("lists each task's recap with its own audio element and a link back to its chat", async () => {
    noChats();
    server.use(
      http.get(T, () =>
        HttpResponse.json({
          tenant_id: "_default",
          count: 2,
          summaries: [
            {
              sid: "sid-a",
              task_id: "tsk_0123456789abcdef",
              title: "Build the importer",
              created_at: 1_790_000_100,
              lang: "de",
              text: "Der Importer ist fertig.",
              audio_url: "/v1/console/chat/sessions/sid-a/workdir/voice-summary/tasks/tsk_0123456789abcdef.ogg",
            },
            {
              sid: "sid-a",
              task_id: "tsk_fedcba9876543210",
              title: "Build the importer",
              created_at: 1_790_000_000,
              lang: "de",
              text: "Die Tests laufen durch.",
              audio_url: "/v1/console/chat/sessions/sid-a/workdir/voice-summary/tasks/tsk_fedcba9876543210.ogg",
            },
          ],
        }),
      ),
    );
    renderIt();

    expect(await screen.findByText("Der Importer ist fertig.")).toBeInTheDocument();
    expect(screen.getByText("Die Tests laufen durch.")).toBeInTheDocument();
    // Two tasks of the SAME chat stay two rows — nothing is keyed by chat only.
    const players = document.querySelectorAll("audio");
    expect(players).toHaveLength(2);
    expect(players[1].getAttribute("src")).toBe(
      "/v1/console/chat/sessions/sid-a/workdir/voice-summary/tasks/tsk_fedcba9876543210.ogg",
    );
    // Short task id is real data (the tail of the id), not a decoration.
    expect(screen.getByText("89abcdef")).toBeInTheDocument();
    const links = screen.getAllByRole("link", { name: "Build the importer" });
    expect(links).toHaveLength(2);
    expect(links[0].getAttribute("href")).toBe("/app/chat/sid-a");
  });

  it("shows its own empty state without hiding the chat-level list", async () => {
    renderIt();
    expect(await screen.findByText(/No task recaps yet/i)).toBeInTheDocument();
  });

  it("a failing task list does not take the chat-level recaps down", async () => {
    server.use(
      http.get(T, () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
      http.get(B, () =>
        HttpResponse.json({
          tenant_id: "_default",
          count: 1,
          summaries: [
            {
              sid: "sid-x", title: "Still here", created_at: 1_790_000_000, lang: "en",
              text: "Chat recap survives.",
              audio_url: "/v1/console/chat/sessions/sid-x/workdir/voice-summary/session-summary.ogg",
            },
          ],
        }),
      ),
    );
    renderIt();
    expect(await screen.findByText(/Could not load task summaries/i)).toBeInTheDocument();
    expect(await screen.findByText("Chat recap survives.")).toBeInTheDocument();
  });
});
