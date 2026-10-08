import { describe, expect, it } from "vitest";
import { buildLinkIndex, linkifyMarkdown, nodeHref, parseNodeHref } from "@/pages/corvin-knowledge/link-resolver";
import { neighbourhood, newestDecisionKey, searchNodes } from "@/pages/corvin-knowledge/graph-model";

const index = buildLinkIndex([
  { id: "UID-A", label: "ADR-2206", file: "ADR-2206-x.md" },
  { id: "ADR-0264", label: "ADR-0264", file: "ADR-0264-y.md" },
  { id: "UID-T", label: "T-0035", file: "T-0035-z.md" },
  { id: "UID-D1", label: "ADR-0001", file: "a.md" },
  { id: "UID-D2", label: "ADR-0001", file: "b.md" },
]);
const lk = (s: string, self: string | null = null) => linkifyMarkdown(s, index, self);

describe("linkifyMarkdown", () => {
  it("links a known id to its node key", () => {
    expect(lk("see ADR-2206 now")).toBe(`see [ADR-2206](${nodeHref("UID-A")}) now`);
  });
  it("leaves an unknown id as plain text (no dead links)", () => {
    expect(lk("see ADR-9999")).toBe("see ADR-9999");
  });
  it("leaves an id carried by two nodes as plain text (ambiguous)", () => {
    expect(lk("see ADR-0001")).toBe("see ADR-0001");
  });
  it("never links inside inline code or fenced code", () => {
    expect(lk("`ADR-2206` and ADR-0264")).toBe(`\`ADR-2206\` and [ADR-0264](${nodeHref("ADR-0264")})`);
    expect(lk("```\nADR-2206\n```\nADR-2206")).toBe(`\`\`\`\nADR-2206\n\`\`\`\n[ADR-2206](${nodeHref("UID-A")})`);
    expect(lk("~~~\nADR-2206\n~~~")).toBe("~~~\nADR-2206\n~~~");
  });
  it("does not touch existing links, autolinks or URLs", () => {
    expect(lk("[ADR-2206](https://x.org/ADR-2206)")).toBe("[ADR-2206](https://x.org/ADR-2206)");
    expect(lk("https://x.org/ADR-2206")).toBe("https://x.org/ADR-2206");
    expect(lk("<https://x.org/ADR-2206>")).toBe("<https://x.org/ADR-2206>");
  });
  it("does not match inside a longer token", () => {
    expect(lk("XADR-2206 ADR-22060 ADR-2206-b")).toBe("XADR-2206 ADR-22060 ADR-2206-b");
  });
  it("does not link a document to itself", () => {
    expect(lk("ADR-2206 and ADR-0264", "UID-A")).toBe(`ADR-2206 and [ADR-0264](${nodeHref("ADR-0264")})`);
  });
  it("resolves [[wikilinks]] by id and keeps the alias text", () => {
    expect(lk("[[ADR-2206]]")).toBe(`[ADR-2206](${nodeHref("UID-A")})`);
    expect(lk("[[ADR-2206|the panel]]")).toBe(`[the panel](${nodeHref("UID-A")})`);
    expect(lk("[[nope]]")).toBe("[[nope]]");
  });
  it("resolves relative .md links by unique file name, not external ones", () => {
    expect(lk("[x](../decisions/ADR-0264-y.md)")).toBe(`[x](${nodeHref("ADR-0264")})`);
    expect(lk("[x](https://a.org/ADR-0264-y.md)")).toBe("[x](https://a.org/ADR-0264-y.md)");
    expect(lk("[x](missing.md)")).toBe("[x](missing.md)");
  });
  it("links task, epic and initiative ids", () => {
    expect(lk("T-0035")).toBe(`[T-0035](${nodeHref("UID-T")})`);
    expect(lk("E-001 I-01")).toBe("E-001 I-01");
  });
  it("round-trips a key through the href", () => {
    expect(parseNodeHref(nodeHref("a b/c"))).toBe("a b/c");
    expect(parseNodeHref("https://x.org")).toBeNull();
  });
});

describe("graph model", () => {
  const edges = [
    { from_id: "a", to_id: "b", relation: "r" },
    { from_id: "b", to_id: "c", relation: "r" },
    { from_id: "c", to_id: "d", relation: "r" },
  ];
  it("neighbourhood follows edges in both directions up to N hops", () => {
    expect([...neighbourhood(edges, "b", 1)].sort()).toEqual(["a", "b", "c"]);
    expect([...neighbourhood(edges, "b", 2)].sort()).toEqual(["a", "b", "c", "d"]);
    expect([...neighbourhood(edges, "z", 1)]).toEqual(["z"]);
  });
  const nodes = [
    { id: "u1", label: "ADR-0010", title: "Old", type: "decision", status: "accepted", tags: [] },
    { id: "u2", label: "ADR-0200", title: "Newest decision", type: "decision", status: "proposed", tags: ["urgent"] },
    { id: "u3", label: "T-0900", title: "A task", type: "task", status: "open", tags: [] },
  ];
  it("starts at the highest-numbered decision, never a task", () => {
    expect(newestDecisionKey(nodes)).toBe("u2");
    expect(newestDecisionKey([nodes[2]])).toBe("u3");
    expect(newestDecisionKey([])).toBeNull();
  });
  it("searches id first, then title and tag", () => {
    expect(searchNodes(nodes, "adr-0200").map((n) => n.id)).toEqual(["u2"]);
    expect(searchNodes(nodes, "task").map((n) => n.id)).toEqual(["u3"]);
    expect(searchNodes(nodes, "urgent").map((n) => n.id)).toEqual(["u2"]);
    expect(searchNodes(nodes, "  ")).toEqual([]);
  });
});
