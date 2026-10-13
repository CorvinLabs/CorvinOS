/** Pure helpers of the expressive-scene tabs: scenes by rendered template, canvas authoring, hotspots, upload checks. */
import { describe, expect, it } from "vitest";
import { canvasAuthoring, captureBlockedReason, clickToPoint, firstUrlIn, nextSpotName, scenesWithTemplate, uploadProblem } from "@/lib/api/video-assets";

const LIM = { max_assets: 50, max_image_bytes: 2097152, max_upload_bytes: 8388608, max_caption: 160, max_hotspots: 8 };
const png = (name = "a.png", size = 10) => new File([new Uint8Array(size)], name, { type: name.endsWith(".png") ? "image/png" : "image/jpeg" });

describe("video-assets helpers", () => {
  it("scenesWithTemplate reads the RENDERED template from the cue log, deduplicated, sorted, junk ignored", () => {
    const md = { cues: [{ scene: 3, template: "code" }, { scene: 1, template: "code" }, { scene: 3, template: "code" }, { scene: "2", template: "code" }, { scene: 0, template: "code" }, { scene: 4, template: "canvas" }] };
    expect(scenesWithTemplate(md, "code")).toEqual([1, 3]);
    expect(scenesWithTemplate(md, "canvas")).toEqual([4]);
    expect(scenesWithTemplate(null, "code")).toEqual([]);
    expect(scenesWithTemplate({ cues: "x" } as never, "code")).toEqual([]);
  });

  it("canvasAuthoring needs total and first_try; after_repair falls back to first_try", () => {
    expect(canvasAuthoring({ canvas_authoring: { total: 2, first_try: 1, after_repair: 2 } })).toEqual({ total: 2, first_try: 1, after_repair: 2 });
    expect(canvasAuthoring({ canvas_authoring: { total: 2, first_try: 2 } })).toEqual({ total: 2, first_try: 2, after_repair: 2 });
    expect(canvasAuthoring({ canvas_authoring: null })).toBeNull();
    expect(canvasAuthoring({ canvas_authoring: { total: "2", first_try: 1 } })).toBeNull();
  });

  it("clickToPoint gives fractions of the displayed box, clamped, 3 decimals; a zero box gives null", () => {
    expect(clickToPoint({ left: 10, top: 20, width: 300, height: 100 }, 110, 70)).toEqual({ x: 0.333, y: 0.5 });
    expect(clickToPoint({ left: 0, top: 0, width: 100, height: 100 }, -5, 150)).toEqual({ x: 0, y: 1 });
    expect(clickToPoint({ left: 0, top: 0, width: 0, height: 100 }, 1, 1)).toBeNull();
  });

  it("nextSpotName never repeats a used name", () => {
    expect(nextSpotName([])).toBe("spot_1");
    expect(nextSpotName([{ name: "spot_1", x: 0, y: 0, description: "" }, { name: "spot_3", x: 0, y: 0, description: "" }])).toBe("spot_2");
  });

  it("uploadProblem mirrors the store's refusals before anything is sent", () => {
    expect(uploadProblem(null, "c", [], LIM)).toMatch(/Choose a PNG/);
    expect(uploadProblem(png("a.jpg"), "c", [], LIM)).toMatch(/Only PNG/);
    expect(uploadProblem(png("a.png", LIM.max_upload_bytes + 1), "c", [], LIM)).toMatch(/larger than 8 MB/);
    expect(uploadProblem(png(), "  ", [], LIM)).toMatch(/caption is required/);
    expect(uploadProblem(png(), "x".repeat(161), [], LIM)).toMatch(/longer than 160/);
    expect(uploadProblem(png(), "c", [{ name: "A b", x: 0, y: 0, description: "" }], LIM)).toMatch(/2-32 characters/);
    expect(uploadProblem(png(), "c", [{ name: "ab", x: 0, y: 0, description: "" }, { name: "ab", x: 1, y: 1, description: "" }], LIM)).toMatch(/appears twice/);
    expect(uploadProblem(png(), "c", [{ name: "ab", x: 0, y: 0, description: "" }], LIM)).toBeNull();
  });

  it("firstUrlIn finds the first plain http(s) address in a task, without trailing punctuation", () => {
    expect(firstUrlIn("Explain how https://acme.com/pricing works.")).toEqual({ url: "https://acme.com/pricing", host: "acme.com" });
    expect(firstUrlIn("see (http://a.example/x?y=1), then https://b.example")).toEqual({ url: "http://a.example/x?y=1", host: "a.example" });
    expect(firstUrlIn("ftp://x.example and file:///etc/passwd")).toBeNull();
    expect(firstUrlIn("no address here")).toBeNull();
  });

  it("captureBlockedReason names why capture cannot be used: loading, switch off, no egress policy", () => {
    expect(captureBlockedReason(undefined)).toMatch(/Loading/);
    expect(captureBlockedReason({ capture_enabled: false, capture_policy_configured: true })).toMatch(/off for this workspace/);
    expect(captureBlockedReason({ capture_enabled: true, capture_policy_configured: false })).toMatch(/egress policy/);
    expect(captureBlockedReason({ capture_enabled: true, capture_policy_configured: true })).toBeNull();
  });
});
