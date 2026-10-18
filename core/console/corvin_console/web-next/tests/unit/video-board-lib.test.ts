/** Board steps (ADR-2260): the pure helpers behind the Board steps dialog. */
import { describe, expect, it } from "vitest";
import { stepOfShape, stepsFromGroups, stepsPayload, stepsProblem, toggleShape, unassignedShapes, type BoardDesign } from "@/lib/api/video-assets";

const LIM = { max_steps: 12, max_label: 40, max_shapes: 240 };
const shape = (group: number) => ({ kind: "rect" as const, x: 0, y: 0, w: 1, h: 1, rx: 0, ry: 0, d: "", text: "", fill: "#000000",
  fill_opacity: 1, stroke: null, stroke_width: 0, opacity: 1, font_family: "", font_size: 0, font_weight: 400, text_length: 0,
  baseline: "alphabetic", anchor: "start", matrix: [1, 0, 0, 1, 0, 0], group });

describe("board step helpers", () => {
  it("toggleShape moves a shape into the selected step and out of every other; a second click removes it", () => {
    let st = [{ shapes: [0, 1] }, { shapes: [] as number[] }];
    st = toggleShape(st, 1, 1);
    expect(st).toEqual([{ shapes: [0] }, { shapes: [1] }]);
    expect(toggleShape(st, 1, 1)).toEqual([{ shapes: [0] }, { shapes: [] }]);
  });

  it("unassignedShapes and stepOfShape read the partition", () => {
    const st = [{ shapes: [2] }, { shapes: [0] }];
    expect(unassignedShapes(st, 4)).toEqual([1, 3]);
    expect(stepOfShape(st, 4)).toEqual([1, -1, 0, -1]);
  });

  it("stepsProblem mirrors the server's rules", () => {
    expect(stepsProblem([], 2, LIM)).toMatch(/at least one step/);
    expect(stepsProblem([{ shapes: [0] }], 2, LIM)).toMatch(/1 shape is not in a step/);
    expect(stepsProblem([{ shapes: [0, 1] }, { shapes: [] }], 2, LIM)).toMatch(/at least one shape/);
    expect(stepsProblem([{ shapes: [0, 1], label: "<b>" }], 2, LIM)).toMatch(/plain text/);
    expect(stepsProblem([{ shapes: [0, 1], label: "x".repeat(41) }], 2, LIM)).toMatch(/at most 40/);
    expect(stepsProblem(Array.from({ length: 13 }, (_, i) => ({ shapes: [i] })), 13, LIM)).toMatch(/at most 12 steps/);
    expect(stepsProblem([{ shapes: [0] }], 241, LIM)).toMatch(/241 shapes/);
    expect(stepsProblem([{ shapes: [1] }, { shapes: [0], label: "Logo" }], 2, LIM)).toBeNull();
  });

  it("stepsPayload trims labels and drops empty ones", () => {
    expect(stepsPayload([{ shapes: [0], label: "  a   b " }, { shapes: [1], label: " " }])).toEqual([{ shapes: [0], label: "a b" }, { shapes: [1] }]);
  });

  it("stepsFromGroups gives one step per top-level group and folds the tail into step 12", () => {
    const d: BoardDesign = { width: 10, height: 10, ox: 0, oy: 0, background: null, shapes: [shape(2), shape(1), shape(2)] };
    expect(stepsFromGroups(d, 12)).toEqual([{ shapes: [1] }, { shapes: [0, 2] }]);
    const many: BoardDesign = { ...d, shapes: Array.from({ length: 14 }, (_, i) => shape(i)) };
    const st = stepsFromGroups(many, 12);
    expect(st.length).toBe(12);
    expect(st[11].shapes).toEqual([11, 12, 13]);
  });
});
