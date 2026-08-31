/**
 * Tests for the detectTtsLang heuristic in chat.tsx.
 *
 * The function is not exported, so we test it via a minimal re-implementation
 * here — keeping the same logic to serve as a specification.
 */

import { describe, it, expect } from "vitest";

// ── Re-implement detectTtsLang here so we can test it as a specification ──────
// (exact copy of the function in chat.tsx — keep in sync)
function detectTtsLang(text: string, fallback: string): string {
  if (!text) return fallback;

  // Script-level signals
  if (/[一-鿿㐀-䶿豈-﫿]/.test(text)) {
    if (/[぀-ヿ]/.test(text)) return "ja";
    if (/[가-힯]/.test(text)) return "ko";
    return "zh";
  }
  if (/[가-힯]/.test(text)) return "ko";
  if (/[぀-ゟ]/.test(text)) return "ja";
  if (/[゠-ヿ]/.test(text)) return "ja";
  if (/[؀-ۿ]/.test(text)) return "ar";
  if (/[֐-׿]/.test(text)) return "he";
  if (/[Ѐ-ӿ]/.test(text)) return "ru";
  if (/[ऀ-ॿ]/.test(text)) return "hi";

  // German-specific characters
  if (/[äöüßÄÖÜ]/.test(text)) return "de";

  // Word-level scoring
  const sample = text.slice(0, 300).toLowerCase();
  const deScore = (sample.match(
    /\b(der|die|das|und|ist|ich|sie|wir|nicht|mit|auf|für|von|ein|eine|dem|den|auch|aber|oder|wenn|dann|noch|schon|so|wie|was|wo|warum|dass|damit|durch|nach|vor|beim|vom|zum|zur|haben|sein|werden|können|müssen|sollen|dürfen|wollen|machen|sagen|gehen|kommen|sehen|wissen)\b/g
  ) || []).length;
  const enScore = (sample.match(
    /\b(the|is|are|was|were|have|has|had|will|would|could|should|can|may|might|must|of|in|to|for|on|at|by|with|from|about|into|through|this|that|these|those|it|its|we|our|they|their|you|your|do|does|did|be|been|being|make|go|come|see|know|think|say|get|use)\b/g
  ) || []).length;
  if (deScore > enScore) return "de";
  if (enScore > deScore) return "en";
  return fallback;
}

// ── Tests ──────────────────────────────────────────────────────────────────────

describe("detectTtsLang", () => {
  // German — character-level signals
  it("detects German by umlaut ä", () => {
    expect(detectTtsLang("Das Wetter ist schön heute.", "en")).toBe("de");
  });
  it("detects German by umlaut ö", () => {
    expect(detectTtsLang("Ich möchte gerne Kaffee.", "en")).toBe("de");
  });
  it("detects German by umlaut ü", () => {
    expect(detectTtsLang("Das ist natürlich richtig.", "en")).toBe("de");
  });
  it("detects German by ß", () => {
    expect(detectTtsLang("Es macht Spaß.", "en")).toBe("de");
  });
  it("detects German by uppercase umlaut Ä", () => {
    expect(detectTtsLang("Ärger ist nicht nötig.", "en")).toBe("de");
  });

  // German — word-level signals (no umlauts)
  it("detects German function words without umlauts", () => {
    // "und", "ist", "nicht", "mit" are unambiguous German
    expect(detectTtsLang("Das ist nicht richtig und das wissen wir.", "en")).toBe("de");
  });
  it("detects German from 'ich', 'wir', 'dann'", () => {
    expect(detectTtsLang("Ich denke, wir sollten dann losgehen.", "en")).toBe("de");
  });

  // English — word-level signals
  it("detects English from common function words", () => {
    expect(detectTtsLang("The quick brown fox jumps over the lazy dog.", "de")).toBe("en");
  });
  it("detects English from a typical response", () => {
    expect(detectTtsLang(
      "Sure, I can help you with that. The solution is straightforward: you need to configure the settings.",
      "de"
    )).toBe("en");
  });
  it("detects English when no German signals present", () => {
    expect(detectTtsLang("Could you please explain what this function does?", "de")).toBe("en");
  });

  // Mixed/ambiguous — fallback
  it("returns fallback for empty string", () => {
    expect(detectTtsLang("", "de")).toBe("de");
    expect(detectTtsLang("", "en")).toBe("en");
  });
  it("returns fallback for a single neutral word", () => {
    // "OK" is not in either word list → equal scores → fallback
    expect(detectTtsLang("OK", "de")).toBe("de");
    expect(detectTtsLang("OK", "en")).toBe("en");
  });

  // Longer realistic responses
  it("detects German in a multi-sentence response", () => {
    const text = `Das Universum ist etwa 13,8 Milliarden Jahre alt.
      Es entstand durch den Urknall und dehnt sich seitdem kontinuierlich aus.
      Die Galaxien bewegen sich voneinander weg, was durch die Rotverschiebung belegt wird.`;
    expect(detectTtsLang(text, "en")).toBe("de");
  });

  it("detects English in a multi-sentence response", () => {
    const text = `The universe is approximately 13.8 billion years old.
      It was created by the Big Bang and has been expanding ever since.
      Galaxies are moving away from each other, which is confirmed by the red shift.`;
    expect(detectTtsLang(text, "de")).toBe("en");
  });

  it("umlaut signal beats any word score (definitive override)", () => {
    // Even if English words are present, a single umlaut means German.
    expect(detectTtsLang(
      "The Straße is very nice. I like walking here.",
      "en"
    )).toBe("de");
  });

  // Profile fallback is the tiebreaker
  it("uses profile fallback when scores are equal", () => {
    // Neutral text with equal signals → fallback wins
    const neutral = "Paris London Berlin Tokyo";
    expect(detectTtsLang(neutral, "de")).toBe("de");
    expect(detectTtsLang(neutral, "en")).toBe("en");
  });
  it("honours arbitrary BCP-47 fallback codes", () => {
    expect(detectTtsLang("OK", "fr")).toBe("fr");
    expect(detectTtsLang("OK", "zh")).toBe("zh");
  });
});

// ── CJK and other script-based languages ─────────────────────────────────────

describe("detectTtsLang — CJK and non-Latin scripts", () => {
  it("detects Simplified Chinese", () => {
    expect(detectTtsLang("你好，世界！这是一段中文文本。", "en")).toBe("zh");
  });
  it("detects Traditional Chinese", () => {
    expect(detectTtsLang("你好，世界！這是一段中文文本。", "en")).toBe("zh");
  });
  it("detects Japanese (hiragana present)", () => {
    expect(detectTtsLang("こんにちは、世界！これは日本語のテキストです。", "en")).toBe("ja");
  });
  it("detects Japanese (katakana only)", () => {
    expect(detectTtsLang("コンピュータープログラミング", "en")).toBe("ja");
  });
  it("detects Korean (Hangul)", () => {
    expect(detectTtsLang("안녕하세요! 이것은 한국어 텍스트입니다.", "en")).toBe("ko");
  });
  it("detects Arabic script", () => {
    expect(detectTtsLang("مرحبا بالعالم! هذا نص عربي.", "en")).toBe("ar");
  });
  it("detects Hebrew script", () => {
    expect(detectTtsLang("שלום עולם! זהו טקסט בעברית.", "en")).toBe("he");
  });
  it("detects Cyrillic (Russian)", () => {
    expect(detectTtsLang("Привет мир! Это русский текст.", "en")).toBe("ru");
  });
  it("detects Devanagari (Hindi)", () => {
    expect(detectTtsLang("नमस्ते दुनिया! यह हिंदी पाठ है।", "en")).toBe("hi");
  });

  it("CJK beats German umlaut signal (mixed text — script takes priority)", () => {
    // If someone writes Chinese with a quoted German word like "Straße"
    expect(detectTtsLang("这是Straße的中文描述。", "en")).toBe("zh");
  });

  it("single CJK character is sufficient for detection", () => {
    expect(detectTtsLang("中", "en")).toBe("zh");
  });

  it("pure ASCII next to CJK remains CJK", () => {
    expect(detectTtsLang("Hello 世界", "en")).toBe("zh");
  });

  it("Japanese wins over Chinese when hiragana present", () => {
    // Kanji + hiragana → ja (not zh)
    expect(detectTtsLang("日本語のテキストです", "en")).toBe("ja");
  });
});

// ── French and Spanish (word-level, no special chars) ─────────────────────────

describe("detectTtsLang — Romance languages (fallback path)", () => {
  it("falls back to profile for French (not enough French-specific signals in current scorer)", () => {
    // The scorer only knows DE/EN words. French falls back to profile.
    expect(detectTtsLang("Bonjour tout le monde, comment allez-vous aujourd'hui?", "fr")).toBe("fr");
  });
  it("falls back to profile for Spanish", () => {
    expect(detectTtsLang("Hola mundo, ¿cómo estás?", "es")).toBe("es");
  });
});
