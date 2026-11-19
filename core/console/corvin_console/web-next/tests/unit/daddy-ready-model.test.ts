import { describe, expect, it } from "vitest";
import { coerce, displayValue, formatAge, formatBytes, formatDuration, getPath, isDirty, newSource, pageLabel, setPath, stepTone } from "@/pages/daddy-ready/model";

describe("daddy-ready model", () => {
  it("setPath is immutable and removes a cleared key and its empty parents", () => {
    const a = { episode: { target_minutes: 5 }, child: { name: "x" } };
    const b = setPath(a, ["episode", "target_minutes"], undefined);
    expect(b).toEqual({ child: { name: "x" } });
    expect(a.episode.target_minutes).toBe(5);
    expect(setPath(a, ["panel", "video_autoplay"], true)).toEqual({ ...a, panel: { video_autoplay: true } });
  });
  it("getPath reads nested values and tolerates gaps", () => {
    expect(getPath({ a: { b: 1 } }, ["a", "b"])).toBe(1);
    expect(getPath({ a: null }, ["a", "b"])).toBeUndefined();
  });
  it("coerce parses numbers, lists and treats empty as default", () => {
    expect(coerce({ type: "integer" }, "12")).toBe(12);
    expect(coerce({ type: "integer" }, "")).toBeUndefined();
    expect(coerce({ type: "integer" }, "1.5")).toBe("1.5");                 // invalid stays text, the plugin names the error
    expect(coerce({ type: "number" }, "0.25")).toBe(0.25);
    expect(coerce({ type: "array" }, " a, b ,,c")).toEqual(["a", "b", "c"]);
    expect(displayValue({ type: "array" }, ["a", "b"])).toBe("a, b");
  });
  it("formats ages, durations, sizes, pages", () => {
    expect(formatAge(null)).toBe("—");
    expect(formatAge(12)).toBe("12 d");
    expect(formatAge(87)).toBe("2 mo");
    expect(formatAge(610)).toBe("20 mo");
    expect(formatAge(800)).toBe("2 y 2 mo");
    expect(formatDuration(305.4)).toBe("5:05");
    expect(formatBytes(2_621_440)).toBe("2.5 MB");
    expect(formatBytes(2048)).toBe("2 KB");
    expect(pageLabel(0, 25, 0)).toBe("0 entries");
    expect(pageLabel(25, 25, 60)).toBe("26–50 of 60");
    expect(pageLabel(50, 25, 60)).toBe("51–60 of 60");
  });
  it("stepTone and newSource", () => {
    expect(stepTone("done")).toBe("ok");
    expect(stepTone("failed")).toBe("bad");
    expect(stepTone("waiting")).toBe("wait");
    expect(stepTone(undefined)).toBe("none");
    expect(newSource("local_folder", ["local-folder"]).key).toBe("local-folder-2");
    expect(isDirty({ a: 1 }, { a: 1 })).toBe(false);
  });
});

import { briefBlocks, humanizeKey, plural } from "@/pages/daddy-ready/model";
describe("daddy-ready readable text", () => {
  it("humanizes config keys", () => {
    expect(humanizeKey("http_timeout_s")).toBe("HTTP timeout (seconds)");
    expect(humanizeKey("kb_page_size")).toBe("KB page size");
    expect(humanizeKey("methods_dirs")).toBe("Methods folders");
    expect(humanizeKey("target_minutes")).toBe("Target minutes");
  });
  it("pluralizes", () => {
    expect(plural(1, "milestone")).toBe("1 milestone");
    expect(plural(0, "note")).toBe("0 notes");
  });
  it("parses a brief into text blocks and never into HTML", () => {
    const b = briefBlocks("# Title\n\n- one **bold** two\n- <b>x</b>\n\n<!-- baseline day -->\n## Part\nplain");
    expect(b.map((x) => x.kind)).toEqual(["h1", "li", "li", "h2", "p"]);
    expect(b[1].parts).toEqual([{ text: "one ", bold: false }, { text: "bold", bold: true }, { text: " two", bold: false }]);
    expect(b[2].parts[0].text).toBe("<b>x</b>");                 // stays text; React escapes it
  });
});
