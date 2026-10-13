/**
 * Peer-chat readability in the real PeerConversation: an actor glyph per row, a day
 * separator wherever the thread crosses a calendar day, and a time-only stamp whose
 * full date lives in the tooltip. The status symbol (ADR-2242) is left untouched.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PeerConversation } from "@/components/chat/PeerConversation";
import { getA2AFeed, getPeerThreadCommands, type A2AFeedMessage } from "@/lib/api/a2a";
import { listConversations } from "@/lib/api/federation";
import { PEER_ROLE_MARK } from "@/lib/a2a-feed";
import { dayLabel, localDayKey } from "@/lib/chat-time";

vi.mock("@/lib/api/a2a", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/a2a")>("@/lib/api/a2a");
  return { ...actual, getA2AFeed: vi.fn(), getPeerThreadCommands: vi.fn(), sendA2AFeedMessage: vi.fn() };
});
vi.mock("@/lib/api/federation", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/federation")>("@/lib/api/federation");
  return { ...actual, listConversations: vi.fn(), stopConversation: vi.fn() };
});

const NOW_S = () => Math.floor(Date.now() / 1000);
/** Noon, `daysAgo` local calendar days back — never near a midnight boundary. */
function noon(daysAgo: number): number {
  const d = new Date();
  return Math.floor(new Date(d.getFullYear(), d.getMonth(), d.getDate() - daysAgo, 12).getTime() / 1000);
}

function feedMsg(p: Partial<A2AFeedMessage>): A2AFeedMessage {
  return {
    id: p.id ?? Math.random().toString(36), ts: p.ts ?? NOW_S() - 10,
    direction: p.direction ?? "out", kind: p.kind ?? "task",
    peer_id: "peer-1", peer_label: "Test Peer", task_id: p.task_id ?? "t1",
    status: p.status ?? "ok", text: p.text ?? "x", data: {}, attachments: [],
    duration_ms: null, error: p.error ?? null, thread_ref: null,
  };
}

function renderWith(messages: A2AFeedMessage[]) {
  vi.mocked(getA2AFeed).mockResolvedValue({
    peers: [{ peer_id: "peer-1", label: "Test Peer", can_send: true, can_receive: true }],
    messages, stages: {},
  } as unknown as Awaited<ReturnType<typeof getA2AFeed>>);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><PeerConversation peerId="peer-1" csrf="c" /></QueryClientProvider>);
}

describe("PeerConversation — readability", () => {
  beforeEach(() => {
    vi.mocked(getPeerThreadCommands).mockResolvedValue({ commands: [] });
    vi.mocked(listConversations).mockResolvedValue({ conversations: [] });
  });

  it("marks each of the four actors with its own glyph, hidden from screen readers", async () => {
    renderWith([
      feedMsg({ id: "1", task_id: "a", direction: "out", kind: "task" }),       // You
      feedMsg({ id: "2", task_id: "a", direction: "in", kind: "response" }),    // their agent
      feedMsg({ id: "3", task_id: "b", direction: "in", kind: "task" }),        // the peer
      feedMsg({ id: "4", task_id: "b", direction: "out", kind: "response" }),   // your agent
    ]);
    const rows = await screen.findAllByTestId("peer-message");
    const marks = rows.map((r) => within(r).getByTestId("peer-message-role-mark"));
    expect(marks.map((m) => m.textContent)).toEqual([
      PEER_ROLE_MARK.operator, PEER_ROLE_MARK.peer_agent, PEER_ROLE_MARK.peer_operator, PEER_ROLE_MARK.local_agent,
    ]);
    for (const m of marks) expect(m.getAttribute("aria-hidden")).toBe("true");
    // The label still names the actor in words — the glyph never replaces it.
    expect(rows.map((r) => within(r).getByTestId("peer-message-role").textContent))
      .toEqual(["You", "Test Peer's agent", "Test Peer", "Your agent"]);
  });

  it("puts one separator above each new calendar day, and none between same-day rows", async () => {
    renderWith([
      feedMsg({ id: "1", task_id: "a", ts: noon(3) }),
      feedMsg({ id: "2", task_id: "b", ts: noon(3) + 60 }),
      feedMsg({ id: "3", task_id: "c", ts: noon(1) }),
      feedMsg({ id: "4", task_id: "d", ts: NOW_S() - 30 }),
    ]);
    await screen.findAllByTestId("peer-message");
    const seps = screen.getAllByTestId("chat-day-separator");
    expect(seps.map((s) => s.getAttribute("aria-label"))).toEqual([dayLabel(noon(3), NOW_S()), "Yesterday", "Today"]);
    for (const s of seps) expect(s.getAttribute("role")).toBe("separator");
  });

  it("shows the time on the row and the full date in its tooltip", async () => {
    const ts = noon(2);
    renderWith([feedMsg({ id: "1", ts })]);
    const row = (await screen.findAllByTestId("peer-message"))[0];
    const time = within(row).getByTestId("peer-message-time");
    expect(time.tagName).toBe("TIME");
    expect(time.getAttribute("datetime")).toBe(new Date(ts * 1000).toISOString());
    expect(time.textContent).toMatch(/^\d{1,2}:\d{2}\s?(AM|PM)$/);
    expect(time.getAttribute("title")).toContain(String(new Date(ts * 1000).getFullYear()));
  });

  it("leaves the ADR-2242 status symbol and its words in place on every row", async () => {
    renderWith([feedMsg({ id: "1", task_id: "a", status: "sent" })]);
    const row = (await screen.findAllByTestId("peer-message"))[0];
    const sym = within(row).getByTestId("peer-message-status");
    expect(sym.querySelector("svg")).toBeTruthy();         // the coloured Lucide icon, not an emoji
    expect(sym.textContent).toMatch(/\w/);                  // a visible label, not a glyph alone
  });
});

describe("dayLabel / localDayKey", () => {
  it("names today and yesterday, and dates anything older", () => {
    const now = noon(0);
    expect(dayLabel(now - 3600, now)).toBe("Today");
    expect(dayLabel(noon(1), now)).toBe("Yesterday");
    expect(dayLabel(noon(5), now)).toMatch(/^[A-Z][a-z]{2}, [A-Z][a-z]{2} \d{1,2}/);
  });

  it("adds the year once the day is not in the current year", () => {
    const now = noon(0);
    const lastYear = Math.floor(new Date(new Date().getFullYear() - 1, 5, 15, 12).getTime() / 1000);
    expect(dayLabel(lastYear, now)).toContain(String(new Date().getFullYear() - 1));
  });

  it("keys by the local calendar day, not by 24-hour buckets", () => {
    const d = new Date(2026, 2, 29, 0, 30); // a DST-change night in the EU
    const late = Math.floor(new Date(2026, 2, 29, 23, 30).getTime() / 1000);
    expect(localDayKey(Math.floor(d.getTime() / 1000))).toBe(localDayKey(late));
  });
});
