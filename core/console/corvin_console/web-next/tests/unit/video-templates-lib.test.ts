import { describe, expect, it } from "vitest";
import { DEFAULT_FILM_MINUTES, jobBody } from "@/lib/api/video-film";
import { effectiveTemplateId, groupTemplates, lengthLabel, templateFacts, templateName, type VideoTemplate, type VideoTemplateList } from "@/lib/api/video-templates";

const t = (id: string, over: Partial<VideoTemplate> = {}): VideoTemplate => ({
  id, name: { en: id.toUpperCase(), de: id }, summary: { en: "s", de: "s" }, category: "explain", audience: "a", tone: "t", aspect: "16:9",
  available: true, unavailable_reason: "", duration: { target_s: 240, min_s: 120, max_s: 480 }, scenes: { min: 4, max: 8 }, structure: [],
  hook: null, evidence: "none", recommended_model: "sonnet", ...over,
});
const list = (over: Partial<VideoTemplateList> = {}): VideoTemplateList => ({
  templates: [t("standard"), t("short", { category: "format", aspect: "9:16", available: false, unavailable_reason: "needs the vertical renderer", hook: { kind: "question", max_words: 12 }, duration: { target_s: 35, min_s: 25, max_s: 59 } }), t("marketing", { category: "persuade" }), t("faq", { category: "format" })],
  default_template_id: "standard", standard_template_id: "standard", ...over,
});

describe("lengthLabel", () => {
  it("shows seconds under a minute and minutes after", () => {
    expect(lengthLabel(35)).toBe("35 s");
    expect(lengthLabel(75)).toBe("1:15 min");
    expect(lengthLabel(240)).toBe("4 min");
  });
});

describe("effectiveTemplateId", () => {
  it("is null until the list is there", () => expect(effectiveTemplateId(undefined, "marketing")).toBeNull());
  it("an explicit pick wins over the remembered one", () => expect(effectiveTemplateId(list({ default_template_id: "faq" }), "marketing")).toBe("marketing"));
  it("without a pick the remembered one is used", () => expect(effectiveTemplateId(list({ default_template_id: "faq" }), null)).toBe("faq"));
  it("a pick the server cannot produce never survives", () => {
    expect(effectiveTemplateId(list(), "short")).toBe("standard");
    expect(effectiveTemplateId(list(), "gone")).toBe("standard");
  });
  it("a remembered template that became unavailable falls back to the standard one", () => {
    expect(effectiveTemplateId(list({ default_template_id: "short" }), null)).toBe("standard");
  });
});

describe("groupTemplates", () => {
  it("keeps the server order and creates a group only for a category that has a template", () => {
    const g = groupTemplates(list().templates);
    expect(g.map((x) => x.category)).toEqual(["explain", "format", "persuade"]);
    expect(g.find((x) => x.category === "format")!.items.map((x) => x.id)).toEqual(["short", "faq"]);
  });
});

describe("labels", () => {
  it("names the template and falls back to Standard", () => {
    expect(templateName(list(), "marketing")).toBe("MARKETING");
    expect(templateName(list(), "nope")).toBe("Standard");
    expect(templateName(undefined, null)).toBe("Standard");
  });
  it("the facts line says length, format and the question hook", () => {
    expect(templateFacts(list().templates[1])).toBe("about 35 s · vertical 9:16 · opens with a question");
    expect(templateFacts(list().templates[0])).toBe("about 4 min · 16:9");
  });
});

describe("jobBody with a template", () => {
  const base = { task: "x", sources: [], film: false, filmMinutes: DEFAULT_FILM_MINUTES, designs: [] };
  it("a new slide video carries the template", () => expect(jobBody({ ...base, videoTemplate: "research" })).toMatchObject({ video_template: "research" }));
  it("no template, no key", () => expect(jobBody({ ...base, videoTemplate: null })).not.toHaveProperty("video_template"));
  it("a film and a revision never carry one", () => {
    expect(jobBody({ ...base, film: true, videoTemplate: "research" })).not.toHaveProperty("video_template");
    expect(jobBody({ ...base, baseJobId: "job_1", videoTemplate: "research" })).not.toHaveProperty("video_template");
  });
});
