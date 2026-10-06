/**
 * A peer's reply renders like a chat answer (2026-10-06): the worker's
 * `{"output": "..."}` dict used to show as raw JSON in the bubble. Driven
 * through the real PeerConversation with a mocked feed API.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import * as React from "react";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PeerConversation } from "@/components/chat/PeerConversation";
import { getA2AFeed } from "@/lib/api/a2a";
import { inlineImageNames, messageMarkdown, referencedImageAttachment } from "@/lib/a2a-feed";

vi.mock("@/lib/api/a2a", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/a2a")>("@/lib/api/a2a");
  return { ...actual, getA2AFeed: vi.fn() };
});

const PNG = { name: "chart.png", mime: "image/png", sha256: "a".repeat(64), size: 10 };
const WAV = { name: "voice.wav", mime: "audio/wav", sha256: "b".repeat(64), size: 10 };

function reply(data: Record<string, unknown>, attachments: unknown[] = []) {
  return {
    id: "r1", seq: 2, ts: 1_791_305_900, direction: "in", kind: "response", peer_id: "peer-1",
    peer_label: "PF65XQC9", task_id: "t1", status: "ok", text: "", data, attachments, error: null,
  };
}

function renderWith(messages: unknown[]) {
  vi.mocked(getA2AFeed).mockResolvedValue({
    peers: [{ peer_id: "peer-1", label: "PF65XQC9", can_send: true, can_receive: true }],
    messages,
  } as unknown as Awaited<ReturnType<typeof getA2AFeed>>);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <PeerConversation peerId="peer-1" csrf="csrf" />
    </QueryClientProvider>,
  );
}

describe("PeerConversation renders replies as Markdown", () => {
  beforeEach(() => vi.mocked(getA2AFeed).mockReset());

  it("shows the output prose formatted, never the raw JSON dict", async () => {
    renderWith([reply({ output: "**Hallo!** Wie kann ich helfen?\n\n- eins\n- zwei" })]);
    const strong = await screen.findByText("Hallo!");
    expect(strong.tagName).toBe("STRONG");
    expect(screen.getByText("eins").tagName).toBe("LI");
    expect(document.body.textContent).not.toContain('"output"');
  });

  it("shows an attachment named in the text inline, once, from the console's blob store", async () => {
    const { container } = await (async () => {
      const r = renderWith([reply({ output: "Here:\n\n![Umsatz](chart.png)" }, [PNG, WAV])]);
      await screen.findByText("Here:");
      return r;
    })();
    const imgs = container.querySelectorAll("img");
    expect(imgs).toHaveLength(1);
    expect(imgs[0].getAttribute("src")).toContain(`/v1/console/a2a/feed/blob/${PNG.sha256}`);
    // The audio attachment that the text does not name still renders as media.
    expect(container.querySelector("audio")).not.toBeNull();
  });

  it("keeps a peer-chosen image URL a link (no request to it)", async () => {
    const { container } = renderWith([reply({ output: "x ![t](https://t.example/p.png)" })]);
    await screen.findByText(/image: t/);
    expect(container.querySelector("img")).toBeNull();
  });
});

describe("a2a-feed Markdown helpers", () => {
  it("appends leftover structured fields as a JSON block whose fence a value cannot close", () => {
    const md = messageMarkdown({ text: "", data: { output: "hi", score: 3, note: "a ``` b" } });
    expect(md.startsWith("hi\n\n````json\n")).toBe(true);
    expect(md.endsWith("\n````")).toBe(true);
    expect(md).not.toContain('"output"');
  });

  it("returns only the prose when nothing else is left", () => {
    expect(messageMarkdown({ text: "", data: { output: "Hallo!" } })).toBe("Hallo!");
  });

  it("resolves only a bare attachment filename that is an image", () => {
    expect(referencedImageAttachment("chart.png", [PNG])).toBe(PNG);
    expect(referencedImageAttachment("./chart.png", [PNG])).toBeNull();
    expect(referencedImageAttachment("https://x/chart.png", [PNG])).toBeNull();
    expect(referencedImageAttachment("voice.wav", [WAV])).toBeNull();
    expect([...inlineImageNames("a ![x](chart.png) b", [PNG, WAV])]).toEqual(["chart.png"]);
  });
});
