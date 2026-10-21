/**
 * The console speaks a reply in the operator's PIN; the installer's locale seed
 * is a default (lib/tts-language.ts, 2026-10-21 round 2).
 */
import { describe, expect, it } from "vitest";

import { ttsFallbackLanguage, ttsLanguageChoice } from "@/lib/tts-language";

describe("ttsLanguageChoice", () => {
  it("an operator pin is the spoken language", () => {
    const c = ttsLanguageChoice({ identity: { display_language: "de" }, audience: {}, extra: {}, display_language_source: "operator" });
    expect(c).toEqual({ pin: "de", configured: "de" });
  });

  it("the installer's seed is a default, never a pin (a German answer keeps its German voice)", () => {
    const c = ttsLanguageChoice({ identity: { display_language: "en" }, audience: {}, extra: {}, display_language_source: "installer" });
    expect(c.pin).toBe("");
    expect(c.configured).toBe("en");
  });

  it("a backend without provenance keeps the old pin behaviour", () => {
    const c = ttsLanguageChoice({ identity: { display_language: "en" }, audience: {}, extra: {} });
    expect(c.pin).toBe("en");
  });

  it("no language → nothing pinned, nothing configured", () => {
    expect(ttsLanguageChoice({ identity: { display_language: " " }, audience: {}, extra: {}, display_language_source: "" })).toEqual({ pin: "", configured: "" });
    expect(ttsLanguageChoice(undefined)).toEqual({ pin: "", configured: "" });
  });
});

describe("ttsFallbackLanguage", () => {
  it("prefers the configured language (seed or pin) over the browser", () => {
    expect(ttsFallbackLanguage({ identity: { display_language: "en" }, audience: {}, extra: {}, display_language_source: "installer" }, "de-DE")).toBe("en");
  });
  it("falls back to the browser's base language, then en", () => {
    expect(ttsFallbackLanguage(undefined, "de-DE")).toBe("de");
    expect(ttsFallbackLanguage(undefined, "")).toBe("en");
  });
});
