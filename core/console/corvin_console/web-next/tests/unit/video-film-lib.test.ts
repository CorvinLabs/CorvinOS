import { describe, expect, it } from "vitest";
import { DEFAULT_FILM_MINUTES, FILM_MINUTES, designName, jobBody } from "@/lib/api/video-film";

describe("designName", () => {
  it("keeps a clean name and strips the extension", () => {
    expect(designName("Home Screen.svg", [])).toBe("Home-Screen");
    expect(designName("board_v2.SVG", [])).toBe("board_v2");
  });
  it("never starts with punctuation and never exceeds 36 characters", () => {
    expect(designName("---weird!!.svg", [])).toBe("weird-");
    expect(designName("a".repeat(80) + ".svg", []).length).toBe(36);
  });
  it("falls back for a name with nothing usable and numbers duplicates", () => {
    expect(designName("???.svg", [])).toBe("design");
    expect(designName("home.svg", ["home", "home-2"])).toBe("home-3");
  });
  it("only produces names the server accepts", () => {
    for (const f of ["Ünïcode name.svg", "../../etc/passwd.svg", "a b/c.svg", ".hidden.svg"]) {
      expect(designName(f, [])).toMatch(/^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$/);
    }
  });
});

describe("jobBody", () => {
  const base = { task: "Explain it", sources: [{ name: "n.md", text: "t" }], film: false, filmMinutes: DEFAULT_FILM_MINUTES, designs: [] };
  it("a slide video carries no film key", () => {
    const b = jobBody(base);
    expect(Object.keys(b).filter((k) => k === "mode" || k.startsWith("cinematic") || k === "designs")).toEqual([]);
  });
  it("a film carries its mode, length and designs", () => {
    const b = jobBody({ ...base, film: true, filmMinutes: 6, designs: [{ name: "home", svg: "<svg/>" }] });
    expect(b).toMatchObject({ mode: "cinematic", cinematic_minutes: 6, designs: [{ name: "home", svg: "<svg/>" }] });
  });
  it("a revision is always a slide video, even with the film switch on", () => {
    expect(jobBody({ ...base, film: true, baseJobId: "job_1" })).not.toHaveProperty("mode");
  });
  it("offers lengths the server accepts and a default among them", () => {
    expect(FILM_MINUTES).toContain(DEFAULT_FILM_MINUTES);
    expect(Math.max(...FILM_MINUTES)).toBeLessThanOrEqual(30);
  });
});
