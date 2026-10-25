/**
 * "Mod activity" card + quiz badge of the Forge Skills tab (ADR-2285).
 *
 * The encodings are asserted as pure functions first: a window must never read as the whole history, and a
 * quiz count of "unknown" must never read as zero. The card is then rendered against a mocked endpoint in each
 * of its states, and the real SkillsTab is rendered to prove the badge and the card are mounted there.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ session: { csrf_token: "csrf" } }) }));

import ModActivity from "@/components/forge/ModActivity";
import SkillsTab from "@/components/forge/SkillsTab";
import {
  describeWindow,
  emptyMessage,
  formatScore,
  formatTime,
  quizBadge,
} from "@/lib/mod-activity";

afterEach(() => cleanup());

const MIB = 1024 * 1024;

describe("encodings", () => {
  it("never describes a partial window as the whole chain", () => {
    expect(describeWindow({ bytes_scanned: 100, chain_bytes: 100, complete: true })).toMatch(/^Whole audit chain read/);
    const partial = describeWindow({ bytes_scanned: 4 * MIB, chain_bytes: 43 * MIB, complete: false });
    expect(partial).toBe("Only the last 4 MiB of 43 MiB were read; older mod activity is not shown.");
    expect(partial).not.toMatch(/whole/i);
  });

  it("says 'none in the part that was read', not 'none', when the window is partial", () => {
    expect(emptyMessage({ bytes_scanned: 1, chain_bytes: 1, complete: true })).toBe("No mod activity has been recorded.");
    expect(emptyMessage({ bytes_scanned: 1, chain_bytes: 9, complete: false })).toMatch(/part of the audit chain that was read/);
  });

  it("formats numbers with a pinned locale", () => {
    expect(describeWindow({ bytes_scanned: 1.5 * MIB, chain_bytes: 1234.5 * MIB, complete: false })).toContain("1,234.5 MiB");
  });

  it("shows a missing score as a dash and a grade with two decimals", () => {
    expect(formatScore(null)).toBe("—");
    expect(formatScore(1)).toBe("1.00");
    expect(formatScore(0)).toBe("0.00");
  });

  it("formats time in UTC and survives a missing one", () => {
    // Cross-checked with `date -u -d @1792928518`; the operator's local zone must not leak in.
    expect(formatTime(1792928518)).toBe("2026-10-25 11:41:58 UTC");
    expect(formatTime(null)).toBe("unknown time");
  });

  it("gives a badge only for a known, positive card count", () => {
    expect(quizBadge(3)).toBe("3 quiz cards");
    expect(quizBadge(1)).toBe("1 quiz card");
    expect(quizBadge(0)).toBeNull();
    expect(quizBadge(null)).toBeNull(); // unknown is not zero, and not a claim either
    expect(quizBadge(undefined)).toBeNull();
  });
});

function renderWith(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const activity = (over: Record<string, unknown> = {}) => ({
  tenant_id: "_default",
  reported_by: "mod",
  window: { bytes_scanned: 4 * MIB, chain_bytes: 43 * MIB, complete: false },
  limit: 50,
  count: 2,
  events: [
    { ts: 1792928518, mod: "corvin-quiz-mod", command: "quiz-answer", skill: "assistant.mod_quiz_phase0", score: 1 },
    { ts: 1792928631, mod: "corvin-skills-mod", command: "skill-info", skill: "assistant.pptx_from_topic", score: null },
  ],
  ...over,
});

describe("ModActivity card", () => {
  it("lists the events, the window, and says the entries are reported by the mod", async () => {
    server.use(http.get("/v1/console/forge/mod-activity", () => HttpResponse.json(activity())));
    renderWith(<ModActivity />);
    await screen.findByText("assistant.mod_quiz_phase0");
    const card = screen.getByTestId("mod-activity");
    expect(within(card).getByText("reported by the mod")).toBeInTheDocument();
    expect(within(card).getByText(/Only the last 4 MiB of 43 MiB were read/)).toBeInTheDocument();
    expect(within(card).getByText("quiz-answer")).toBeInTheDocument();
    expect(within(card).getByText("1.00")).toBeInTheDocument();
    expect(within(card).getByText("—")).toBeInTheDocument(); // the lookup graded nothing
    expect(within(card).getAllByRole("listitem")).toHaveLength(2);
  });

  it("an empty partial window does not claim there is no activity at all", async () => {
    server.use(http.get("/v1/console/forge/mod-activity", () => HttpResponse.json(activity({ events: [], count: 0 }))));
    renderWith(<ModActivity />);
    await screen.findByText(/No mod activity in the part of the audit chain that was read/);
    expect(screen.queryByText("No mod activity has been recorded.")).toBeNull();
  });

  it("a 404 says 'not available on this build' and shows no rows", async () => {
    server.use(http.get("/v1/console/forge/mod-activity", () => new HttpResponse(null, { status: 404 })));
    renderWith(<ModActivity />);
    await screen.findByText("Mod activity is not available on this build.");
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("a server error is shown as an error, not as an empty list", async () => {
    server.use(http.get("/v1/console/forge/mod-activity", () => new HttpResponse(null, { status: 500 })));
    renderWith(<ModActivity />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Mod activity request failed (500)");
    expect(screen.queryByText(/No mod activity/)).toBeNull();
  });
});

describe("Forge Skills tab", () => {
  const skill = (name: string, quiz_cards: number | null | undefined) => ({
    id: name, name, enabled: true, description: `${name} description`, quiz_cards,
  });

  it("mounts the card and badges only skills with a known, positive quiz count", async () => {
    server.use(http.get("/v1/console/forge/mod-activity", () => HttpResponse.json(activity())));
    renderWith(
      <SkillsTab
        skills={[skill("with.quiz", 3), skill("no.quiz", 0), skill("bad.quiz", null), skill("old.api", undefined)]}
        setSkills={() => {}}
        searchQuery=""
        filterStatus="all"
        onCreateSkill={() => {}}
      />,
    );
    await screen.findByTestId("mod-activity");
    const badges = screen.getAllByTestId("quiz-badge");
    expect(badges).toHaveLength(1);
    expect(badges[0]).toHaveTextContent("3 quiz cards");
  });
});
