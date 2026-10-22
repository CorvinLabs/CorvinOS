import { describe, expect, it } from "vitest";
import {
  NODE_PALETTE,
  SUPPORTED_NODE_TYPES,
  buildRouteCases,
  missingRequiredFields,
  nextNodeId,
  parseConditionValue,
  routeFallbackId,
  switchRouteMode,
} from "@/lib/workflow-node-types";

describe("workflow node types", () => {
  it("offers a palette entry for every supported type, and nothing else", () => {
    expect(NODE_PALETTE.map((p) => p.type).sort()).toEqual([...SUPPORTED_NODE_TYPES].sort());
  });

  it("every palette node is created with all required fields filled", () => {
    for (const entry of NODE_PALETTE) {
      // A peer node is only valid with a real target; the palette caller supplies one from the catalog.
      const node = entry.create("x", entry.needsPeer ? { endpoint_id: "ep_x", agent_id: "agent_x" } : undefined);
      expect(node.type).toBe(entry.type);
      expect(missingRequiredFields(node), entry.type).toEqual([]);
    }
  });

  it("reports what a half-filled node still needs", () => {
    expect(missingRequiredFields({ type: "merge", id: "m" })).toEqual(["strategy", "inputs", "output"]);
    expect(missingRequiredFields({ type: "route", mode: "condition", cases: [] })).toEqual(["cases", "fallback case"]);
    expect(missingRequiredFields({ type: "trigger" })).toEqual([]); // unsupported: handled elsewhere
  });

  it("route cases always end in exactly one default fallback", () => {
    const cases = buildRouteCases([{ id: "a", when: { selector: "n.x", op: "==", value: 1 } }], "other");
    expect(cases).toHaveLength(2);
    expect(routeFallbackId({ cases })).toBe("other");
  });

  it("switching route mode swaps the mode-specific fields", () => {
    const cond = NODE_PALETTE.find((p) => p.type === "route")!.create("r");
    const cls = switchRouteMode(cond, "classify");
    expect(cls.cases).toBeUndefined();
    expect(missingRequiredFields(cls)).toEqual([]);
    const back = switchRouteMode(cls, "condition");
    expect(back.classes).toBeUndefined();
    expect(missingRequiredFields(back)).toEqual([]);
  });

  it("allocates unused ids and types condition values", () => {
    expect(nextNodeId("agent", ["agent", "agent_2"])).toBe("agent_3");
    expect(parseConditionValue("==", "12")).toBe(12);
    expect(parseConditionValue("==", "true")).toBe(true);
    expect(parseConditionValue("in", "a, b")).toEqual(["a", "b"]);
  });
});
