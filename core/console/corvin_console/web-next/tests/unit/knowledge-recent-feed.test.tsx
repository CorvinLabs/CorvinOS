/**
 * The "New" view of the Knowledge Graph (RecentFeed): the REAL component against a mocked HTTP boundary (msw).
 * What a reader sees: the request carries the period, the project and the filter, the tiles count the whole period and
 * the server does the filtering, the list is grouped by day, a click hands the entry's node key to the page, the
 * controls live in the URL, and an honest state is shown for an empty or unreadable list.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";
import { RecentFeed } from "@/pages/corvin-knowledge/RecentFeed";
import type { RecentPayload } from "@/pages/corvin-knowledge/recent-model";

const iso = (hoursAgo: number) => new Date(Date.now() - hoursAgo * 3_600_000).toISOString();
const ITEMS: RecentPayload["items"] = [
  { id: "01AAA", label: "T-0003", type: "task", title: "Add the New view", status: "open", changed_at: iso(1), summary: "kb: new task T-0003 — Add the New view", commits: 1, kind: "new" },
  { id: "01BBB", label: "ADR-0001", type: "decision", title: "Encrypted storage", status: "proposed", changed_at: iso(2), summary: "docs: link the plan", commits: 2, kind: "updated" },
  { id: "01CCC", label: "ADR-0004", type: "decision", title: "Aliases are bound", status: "proposed", changed_at: iso(60), summary: "", commits: 1, kind: "new" },
];

/** What the real route answers: the whole period's counts, and items/matching narrowed by kind and type. */
function answer(search: URLSearchParams, over: Partial<RecentPayload> = {}): RecentPayload {
  const kind = search.get("kind"); const type = search.get("type");
  const narrowed = ITEMS.filter((i) => (!kind || i.kind === kind) && (!type || i.type === type));
  const counts: Record<string, number> = {};
  for (const i of ITEMS) counts[i.type] = (counts[i.type] ?? 0) + 1;
  return { state: "ok", window_days: Number(search.get("days") ?? 14), items: narrowed, total: 3, new_count: 2, updated_count: 1,
    matching: narrowed.length, type_counts: counts, generated_at: iso(0), project: null, graph_stale: false, unindexed: 0, index_error: null, ...over };
}

function Where() { const l = useLocation(); return <span data-testid="where">{l.search}</span>; }

function mount(props: Partial<React.ComponentProps<typeof RecentFeed>> = {}, at = "/") {
  // the console's own defaults (main.tsx): a 15 s refetch interval the feed must opt out of
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, refetchInterval: 15_000 } } });
  const onOpen = vi.fn();
  const view = render(
    <MemoryRouter initialEntries={[at]}>
      <QueryClientProvider client={qc}>
        <RecentFeed project={null} scopeName="demo-kb" joined={false} ready colours={{ proposed: "#ca9b49", open: "#8a8a8a" }} onOpen={onOpen} {...props} />
        <Where />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  return { ...view, onOpen };
}

afterEach(cleanup);

describe("RecentFeed", () => {
  it("requests the chosen period and project, groups the answer by day, and keeps the period in the URL", async () => {
    const seen: string[] = [];
    server.use(http.get("/v1/console/plugins/corvin-knowledge/recent", ({ request }) => { const u = new URL(request.url); seen.push(u.search); return HttpResponse.json(answer(u.searchParams)); }));
    mount({ project: "alpha" });
    await screen.findByTestId("recent-timeline");
    expect(seen[0]).toBe("?days=14&project=alpha&limit=80");
    expect(screen.getAllByTestId("recent-item")).toHaveLength(3);
    expect(screen.getAllByTestId("recent-day").length).toBeGreaterThanOrEqual(2);
    fireEvent.click(screen.getByRole("button", { name: "30 days" }));
    await waitFor(() => expect(seen.some((s) => s.startsWith("?days=30"))).toBe(true));
    expect(screen.getByTestId("where").textContent).toContain("days=30");
  });

  it("waits until the page knows which knowledge base is meant", async () => {
    let hits = 0;
    server.use(http.get("/v1/console/plugins/corvin-knowledge/recent", ({ request }) => { hits++; return HttpResponse.json(answer(new URL(request.url).searchParams)); }));
    mount({ ready: false });
    await new Promise((r) => setTimeout(r, 60));
    expect(hits).toBe(0);
    expect(screen.getByLabelText("Loading")).toBeTruthy();
  });

  it("filters on the server: the tile keeps its whole-period number and the list holds what it counts", async () => {
    const seen: string[] = [];
    server.use(http.get("/v1/console/plugins/corvin-knowledge/recent", ({ request }) => { const u = new URL(request.url); seen.push(u.search); return HttpResponse.json(answer(u.searchParams)); }));
    const { onOpen } = mount();
    await screen.findByTestId("recent-timeline");
    fireEvent.click(screen.getByTestId("recent-tile-updated"));
    await waitFor(() => expect(screen.getAllByTestId("recent-item")).toHaveLength(1));
    expect(seen.some((s) => s.includes("kind=updated"))).toBe(true);
    expect(screen.getByTestId("recent-tile-all").textContent).toMatch(/3/);          // the tiles did not shrink with the filter
    const row = screen.getAllByTestId("recent-item")[0];
    fireEvent.click(within(row).getByRole("button", { name: /Open ADR-0001/ }));
    expect(onOpen).toHaveBeenCalledWith("01BBB");
  });

  it("says so when a filter matches nothing, and shows the type row from the server's counts", async () => {
    server.use(http.get("/v1/console/plugins/corvin-knowledge/recent", ({ request }) => HttpResponse.json(answer(new URL(request.url).searchParams))));
    mount();
    await screen.findByTestId("recent-timeline");
    fireEvent.click(screen.getByTestId("recent-tile-updated"));
    await waitFor(() => expect(screen.getAllByTestId("recent-item")).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: /^Task/ }));
    expect((await screen.findByTestId("recent-empty")).textContent).toMatch(/Nothing matches these filters/);
  });

  it("restores a filter from the URL (Back from an opened entry) and drops a type the period does not have", async () => {
    const seen: string[] = [];
    server.use(http.get("/v1/console/plugins/corvin-knowledge/recent", ({ request }) => { const u = new URL(request.url); seen.push(u.search); return HttpResponse.json(answer(u.searchParams)); }));
    mount({}, "/?days=7&kind=new");
    await screen.findByTestId("recent-timeline");
    expect(seen[0]).toBe("?days=7&kind=new&limit=80");
    cleanup();
    mount({}, "/?type=widget");
    await waitFor(() => expect(screen.getByTestId("where").textContent).not.toContain("type=widget"));
    cleanup();
    mount({}, "/?type=constructor");                                   // a name on Object.prototype is not a type of the period
    await waitFor(() => expect(screen.getByTestId("where").textContent).not.toContain("type=constructor"));
  });

  it("leaves out a commit line that only repeats the title", async () => {
    server.use(http.get("/v1/console/plugins/corvin-knowledge/recent", ({ request }) => HttpResponse.json(answer(new URL(request.url).searchParams))));
    mount();
    const items = await screen.findAllByTestId("recent-item");
    expect(items[0].textContent).not.toMatch(/kb: new task/);
    expect(items[1].textContent).toMatch(/docs: link the plan/);
    expect(items[1].textContent).toMatch(/2 changes/);
  });

  it("says what is going on instead of an empty list", async () => {
    server.use(http.get("/v1/console/plugins/corvin-knowledge/recent", () => HttpResponse.json(answer(new URLSearchParams(), { state: "no_commits", items: [], total: 0, new_count: 0, updated_count: 0, matching: 0, type_counts: {} }))));
    mount();
    expect((await screen.findByTestId("recent-empty")).textContent).toMatch(/No commits yet/);
  });

  it("shows a retry when the request fails and asks for one knowledge base when several are selected", async () => {
    server.use(http.get("/v1/console/plugins/corvin-knowledge/recent", () => HttpResponse.json({ detail: "x" }, { status: 500 })));
    mount();
    expect((await screen.findByRole("alert")).textContent).toMatch(/could not be loaded/);
    cleanup();
    mount({ joined: true });
    expect(screen.getByTestId("recent-joined-note").textContent).toMatch(/Choose one knowledge base/);
  });

  it("does not poll: one request is made and no interval follows", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let hits = 0;
    server.use(http.get("/v1/console/plugins/corvin-knowledge/recent", ({ request }) => { hits++; return HttpResponse.json(answer(new URL(request.url).searchParams)); }));
    mount();
    await screen.findByTestId("recent-timeline");
    await vi.advanceTimersByTimeAsync(61_000);
    expect(hits).toBe(1);
    vi.useRealTimers();
  });

  it("names an entry's kind, status, time and commit line for a screen reader", async () => {
    server.use(http.get("/v1/console/plugins/corvin-knowledge/recent", ({ request }) => HttpResponse.json(answer(new URL(request.url).searchParams))));
    mount();
    const b = await screen.findByRole("button", { name: /Open ADR-0001/ });
    expect(b.textContent).toMatch(/Changed/);
    expect(b.textContent).toMatch(/proposed/);
    expect(b.textContent).toMatch(/docs: link the plan/);
    expect(b.getAttribute("aria-label")).toBeNull();
  });

  it("says when the history was cut short and how many entries match beyond the list", async () => {
    server.use(http.get("/v1/console/plugins/corvin-knowledge/recent", ({ request }) => HttpResponse.json(answer(new URL(request.url).searchParams, { truncated: true, matching: 99 }))));
    mount();
    const footer = (await screen.findByTestId("recent-footer")).textContent ?? "";
    expect(footer).toMatch(/very long history/);
    expect(footer).toMatch(/Showing the newest 3 of 99/);
  });

  it("looks again while the graph lags, at most four times, and tells the reader", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let hits = 0;
    server.use(http.get("/v1/console/plugins/corvin-knowledge/recent", ({ request }) => { hits++; return HttpResponse.json(answer(new URL(request.url).searchParams, { graph_stale: true, unindexed: 2 })); }));
    mount();
    expect((await screen.findByTestId("recent-graph-lag")).textContent).toMatch(/2 newer entries are not in the graph yet/);
    await vi.advanceTimersByTimeAsync(8_000 * 8);
    expect(hits).toBeGreaterThanOrEqual(2);
    expect(hits).toBeLessThanOrEqual(5);                                 // the first request plus at most four looks
    vi.useRealTimers();
  });

  it("serves the list with a warning when the graph could not be re-indexed, and does not keep polling", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let hits = 0;
    server.use(http.get("/v1/console/plugins/corvin-knowledge/recent", ({ request }) => { hits++; return HttpResponse.json(answer(new URL(request.url).searchParams, { graph_stale: true, index_error: "kb/graph is not ignored by git" })); }));
    mount();
    expect((await screen.findByTestId("recent-index-error")).textContent).toMatch(/not ignored by git/);
    expect(screen.getAllByTestId("recent-item")).toHaveLength(3);
    await vi.advanceTimersByTimeAsync(40_000);
    expect(hits).toBe(1);
    vi.useRealTimers();
  });

  it("refreshes the page's graph too when Refresh is pressed", async () => {
    server.use(http.get("/v1/console/plugins/corvin-knowledge/recent", ({ request }) => HttpResponse.json(answer(new URL(request.url).searchParams))));
    const onRefresh = vi.fn();
    mount({ onRefresh });
    await screen.findByTestId("recent-timeline");
    fireEvent.click(screen.getByRole("button", { name: "Refresh the list" }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("stops looking again once a request fails (a failed request keeps the last payload)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let hits = 0;
    server.use(http.get("/v1/console/plugins/corvin-knowledge/recent", ({ request }) => {
      hits++;
      return hits === 1 ? HttpResponse.json(answer(new URL(request.url).searchParams, { graph_stale: true, unindexed: 1 })) : HttpResponse.json({ detail: "down" }, { status: 503 });
    }));
    mount();
    await screen.findByTestId("recent-graph-lag");
    await vi.advanceTimersByTimeAsync(8_000 * 3);
    await vi.advanceTimersByTimeAsync(8_000 * 20);
    expect(hits).toBeLessThanOrEqual(3);                                 // the first answer, the one look that failed, nothing after
    vi.useRealTimers();
  });

  it("shows a busy graph as such and looks again, then gives up", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let hits = 0;
    server.use(http.get("/v1/console/plugins/corvin-knowledge/recent", ({ request }) => { hits++; return HttpResponse.json(answer(new URL(request.url).searchParams, { state: "busy", items: [], total: 0, new_count: 0, updated_count: 0, matching: 0, type_counts: {}, graph_stale: false })); }));
    mount();
    expect((await screen.findByTestId("recent-empty")).textContent).toMatch(/being rebuilt/);
    await vi.advanceTimersByTimeAsync(8_000 * 12);
    expect(hits).toBeGreaterThanOrEqual(2);
    expect(hits).toBeLessThanOrEqual(5);
    vi.useRealTimers();
  });

  it("keeps one live region mounted so the notice is announced, and shows no lag note beside a busy graph", async () => {
    server.use(http.get("/v1/console/plugins/corvin-knowledge/recent", ({ request }) => HttpResponse.json(answer(new URL(request.url).searchParams))));
    mount();
    const live = await screen.findByTestId("recent-live");
    expect(live.getAttribute("role")).toBe("status");
    expect(live.getAttribute("aria-live")).toBe("polite");
    expect(live.textContent).toBe("");                                     // mounted and empty: nothing to say
  });

  it("announces a busy graph in the live region and shows no lag note beside it", async () => {
    server.use(http.get("/v1/console/plugins/corvin-knowledge/recent", ({ request }) => HttpResponse.json(answer(new URL(request.url).searchParams, { state: "busy", items: [], total: 0, new_count: 0, updated_count: 0, matching: 0, type_counts: {}, graph_stale: true }))));
    mount();
    await screen.findByTestId("recent-empty");
    expect(screen.getByTestId("recent-live").textContent).toMatch(/being rebuilt/);
    expect(screen.queryByTestId("recent-graph-lag")).toBeNull();
  });

  it("keeps the list that is on screen when a refetch answers busy, and says why", async () => {
    let n = 0;
    server.use(http.get("/v1/console/plugins/corvin-knowledge/recent", ({ request }) => {
      n++;
      return HttpResponse.json(n === 1 ? answer(new URL(request.url).searchParams)
        : answer(new URL(request.url).searchParams, { state: "busy", items: [], total: 0, new_count: 0, updated_count: 0, matching: 0, type_counts: {} }));
    }));
    mount();
    await screen.findByTestId("recent-timeline");
    fireEvent.click(screen.getByRole("button", { name: "Refresh the list" }));
    expect((await screen.findByTestId("recent-busy-note")).textContent).toMatch(/last one that was read/);
    expect(screen.getAllByTestId("recent-item")).toHaveLength(3);          // the list did not vanish
  });

  it("never shows the previous filter's list as 'the last one read' when the new filter answers busy", async () => {
    server.use(http.get("/v1/console/plugins/corvin-knowledge/recent", async ({ request }) => {
      const u = new URL(request.url);
      if (u.searchParams.get("kind") === "updated") {
        await new Promise((r) => setTimeout(r, 80));                         // long enough for a render with the old list as placeholder
        return HttpResponse.json(answer(u.searchParams, { state: "busy", items: [], total: 0, new_count: 0, updated_count: 0, matching: 0, type_counts: {} }));
      }
      return HttpResponse.json(answer(u.searchParams));
    }));
    mount();
    await screen.findByTestId("recent-timeline");
    fireEvent.click(screen.getByTestId("recent-tile-updated"));
    expect((await screen.findByTestId("recent-empty")).textContent).toMatch(/being rebuilt/);   // not the unfiltered list
    expect(screen.queryByTestId("recent-busy-note")).toBeNull();
    expect(screen.queryAllByTestId("recent-item")).toHaveLength(0);
  });

  it("gives a new filter its own looks after the old ones are used up", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const seen: string[] = [];
    server.use(http.get("/v1/console/plugins/corvin-knowledge/recent", ({ request }) => { const u = new URL(request.url); seen.push(u.search); return HttpResponse.json(answer(u.searchParams, { graph_stale: true, unindexed: 1 })); }));
    mount();
    await screen.findByTestId("recent-graph-lag");
    await vi.advanceTimersByTimeAsync(8_000 * 8);                          // the four looks of the first list are used up
    const before = seen.length;
    await vi.advanceTimersByTimeAsync(8_000 * 3);
    expect(seen.length).toBe(before);                                      // and it stays quiet
    fireEvent.click(screen.getByTestId("recent-tile-new"));                // a new list: its own looks
    await vi.advanceTimersByTimeAsync(8_000 * 3);
    expect(seen.filter((s) => s.includes("kind=new")).length).toBeGreaterThanOrEqual(2);
    vi.useRealTimers();
  });

  it("words a capped count without a double negative, and says 'at least' with one entry", async () => {
    server.use(http.get("/v1/console/plugins/corvin-knowledge/recent", ({ request }) => HttpResponse.json(answer(new URL(request.url).searchParams, { graph_stale: true, unindexed: 0, unindexed_capped: true }))));
    mount();
    const txt = (await screen.findByTestId("recent-graph-lag")).textContent ?? "";
    expect(txt).toMatch(/Some newer files may not be in the graph yet/);
    expect(txt).not.toMatch(/not not/);
  });

  it("uses the plural for a capped count of one", async () => {
    server.use(http.get("/v1/console/plugins/corvin-knowledge/recent", ({ request }) => HttpResponse.json(answer(new URL(request.url).searchParams, { graph_stale: true, unindexed: 1, unindexed_capped: true }))));
    mount();
    const txt = (await screen.findByTestId("recent-graph-lag")).textContent ?? "";
    expect(txt).toMatch(/At least 1 newer entry is not in the graph yet,\s*so they are missing/);
  });

  it("offers to show more when the list is capped, and asks for a larger limit", async () => {
    const seen: string[] = [];
    server.use(http.get("/v1/console/plugins/corvin-knowledge/recent", ({ request }) => {
      const u = new URL(request.url); seen.push(u.searchParams.get("limit") ?? "");
      return HttpResponse.json(answer(u.searchParams, { matching: 200, total: 200, new_count: 200, updated_count: 0 }));
    }));
    mount();
    fireEvent.click(await screen.findByTestId("recent-show-more"));
    await waitFor(() => expect(seen).toContain("160"));
  });

  it("keeps a fractional or odd limit in the URL a whole number the server accepts", async () => {
    const seen: string[] = [];
    server.use(http.get("/v1/console/plugins/corvin-knowledge/recent", ({ request }) => { seen.push(new URL(request.url).searchParams.get("limit") ?? ""); return HttpResponse.json(answer(new URL(request.url).searchParams)); }));
    mount({}, "/?limit=100.5");
    await screen.findByTestId("recent-timeline");
    expect(seen[0]).toBe("100");
  });

  it("does not promise more than the cap allows", async () => {
    server.use(http.get("/v1/console/plugins/corvin-knowledge/recent", ({ request }) => HttpResponse.json(answer(new URL(request.url).searchParams, { matching: 600, total: 600, new_count: 600, updated_count: 0 }))));
    mount({}, "/?limit=480");
    const btn = await screen.findByTestId("recent-show-more");
    expect(btn.textContent).toMatch(/Show 20 more/);
  });

  it("does not describe the old list while a larger limit loads", async () => {
    let n = 0;
    server.use(http.get("/v1/console/plugins/corvin-knowledge/recent", async ({ request }) => {
      const u = new URL(request.url); n++;
      if (u.searchParams.get("limit") === "500") await new Promise((r) => setTimeout(r, 120));
      return HttpResponse.json(answer(u.searchParams, { matching: 490, total: 490, items: ITEMS, new_count: 3, updated_count: 0 }));
    }));
    mount({}, "/?limit=480");
    await screen.findByTestId("recent-show-more");
    fireEvent.click(screen.getByTestId("recent-show-more"));
    expect(screen.queryByText(/The list stops at 500/)).toBeNull();
    expect(screen.queryByTestId("recent-show-more")).toBeNull();                 // nothing offered while the new list loads
    expect(await screen.findByText(/The list stops at 500/)).toBeTruthy();       // and the footer comes back once it has arrived
  });

  it("keeps the smaller list on screen when Show more is answered busy", async () => {
    server.use(http.get("/v1/console/plugins/corvin-knowledge/recent", ({ request }) => {
      const u = new URL(request.url);
      if (u.searchParams.get("limit") === "160") return HttpResponse.json(answer(u.searchParams, { state: "busy", items: [], total: 0, new_count: 0, updated_count: 0, matching: 0, type_counts: {} }));
      return HttpResponse.json(answer(u.searchParams, { matching: 200, total: 200, new_count: 200, updated_count: 0 }));
    }));
    mount();
    fireEvent.click(await screen.findByTestId("recent-show-more"));
    expect((await screen.findByTestId("recent-busy-note")).textContent).toMatch(/last one that was read/);
    expect(screen.getAllByTestId("recent-item")).toHaveLength(3);
  });
});
