/**
 * The console's status-icon table is a mirror of the Python vocabulary
 * (corvin_operator/bridges/shared/status_markers.py). Its generated export,
 * status_markers.json, is the comparison target — the same file the JavaScript
 * bridges read — so the mirror cannot drift unnoticed.
 */
import { describe, expect, it } from "vitest";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { icon, STATUS_ICONS, STATUS_MARKERS, type StatusMarkerName } from "@/lib/status-markers";

const JSON_PATH = resolve(
  __dirname,
  "../../../../../../corvin_operator/bridges/shared/status_markers.json",
);

describe("status markers mirror", () => {
  it("positive control: the vocabulary export is reachable from this test", () => {
    expect(existsSync(JSON_PATH)).toBe(true);
    const src = JSON.parse(readFileSync(JSON_PATH, "utf8")) as { icons: Record<string, string> };
    expect(Object.keys(src.icons).length).toBeGreaterThanOrEqual(8);
  });

  it("carries exactly the icons and meanings of the Python vocabulary", () => {
    const src = JSON.parse(readFileSync(JSON_PATH, "utf8")) as {
      icons: Record<string, string>;
      markers: [string, string][];
    };
    expect({ ...STATUS_ICONS }).toEqual(src.icons);
    expect(STATUS_MARKERS.map((m) => [...m])).toEqual(src.markers);
  });

  it("icon() returns by name and throws on an unknown name", () => {
    expect(icon("running")).toBe("⏳");
    expect(() => icon("finished" as StatusMarkerName)).toThrow(/unknown status marker/);
  });
});
