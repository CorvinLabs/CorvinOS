/**
 * The page-level wiring of the "New" tab (index.tsx), pinned at the source: the pieces a component test of the feed cannot see.
 * Opening an entry must PUSH a history entry (so Back returns to the list), refresh the graph (an entry just listed may be newer
 * than the loaded graph), and drop the feed's own controls from the Graph URL; a pull and Refresh refresh the feed / the graph.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const src = readFileSync(resolve(__dirname, "../../src/pages/corvin-knowledge/index.tsx"), "utf8");

describe("Knowledge Graph page wiring for the New tab", () => {
  it("mounts the feed under a tab that the URL controls", () => {
    expect(src).toContain('<TabsTrigger value="new" data-testid="knowledge-tab-new">New</TabsTrigger>');
    expect(src).toContain('params.get("view") === "new"');
    expect(src).toMatch(/<RecentFeed key=\{scopeKey\}/);
    expect(src).toContain("ready={!scopePending && !config.isLoading}");
  });
  it("opens an entry with a PUSHED history entry, refreshing the graph and dropping the feed's controls", () => {
    const m = src.match(/const openFromRecent = useCallback\(([\s\S]*?)\}, \[setParams, qc\]\);/);
    expect(m).not.toBeNull();
    const body = m![1];
    expect(body).toContain("invalidateQueries({ queryKey: [...KEY_GRAPH] })");
    expect(body).toContain('n.set("node", key)');
    for (const k of ["view", "days", "kind", "type"]) expect(body).toContain(`n.delete("${k}")`);
    expect(body).not.toContain("replace: true");                        // Back must return to the list
  });
  it("refreshes the feed after a pull and the graph on Refresh", () => {
    const sync = src.match(/const sync = useMutation\(\{([\s\S]*?)\n  \}\);/);
    expect(sync).not.toBeNull();
    expect(sync![1]).toMatch(/onSuccess:[\s\S]*?invalidateQueries\(\{ queryKey: \[\.\.\.KEY_RECENT\] \}\)[\s\S]*?onError:/);   // inside the pull's success handler
    expect(src).toContain("onRefresh={() => qc.invalidateQueries({ queryKey: [...KEY_GRAPH] })}");
  });
});
