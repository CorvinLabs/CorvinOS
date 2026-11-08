import { describe, expect, it } from "vitest";
import type { ProjectSummary, TemplateInfo } from "@/lib/api/projects";
import {
  ACCESS_META, CLASS_META, CLASS_ORDER, FAMILIES, NO_FILTER, ROLE_LABEL, badgeLine, classOf, filterProjects, groupTemplates,
  lowersProtection, presentValues, roleLabel,
} from "@/pages/projects/roles";

const t = (id: string, role: TemplateInfo["role"]): TemplateInfo => ({ id, version: 1, title: id, description: "", features: ["core"], offered: ["core"], docs_mode: "nested", role });
const p = (id: string, role: ProjectSummary["role"], cls?: ProjectSummary["classification"]): ProjectSummary => ({ id, path: "/p/" + id, template: "", state: "ok", role, classification: cls });

describe("role catalogue", () => {
  it("every role is in exactly one family and has a label (nine roles, three families of three)", () => {
    const all = FAMILIES.flatMap((f) => f.roles);
    expect(all.length).toBe(9);
    expect(new Set(all).size).toBe(9);
    for (const r of all) expect(ROLE_LABEL[r]).toBeTruthy();
    expect(FAMILIES.map((f) => f.roles.length)).toEqual([3, 3, 3]);
  });
  it("every class has a label AND a distinct shape, so colour is never the only signal", () => {
    const shapes = CLASS_ORDER.map((c) => CLASS_META[c].shape);
    expect(new Set(shapes).size).toBe(CLASS_ORDER.length);
    for (const c of CLASS_ORDER) expect(CLASS_META[c].label.length).toBeGreaterThan(3);
  });
  it("an unknown role or class degrades to something honest instead of a raw id", () => {
    expect(roleLabel("astronaut")).toBe("");
    expect(roleLabel(undefined)).toBe("");
    expect(classOf({})).toBe("internal");
    expect(classOf({ classification: "bogus" as never })).toBe("internal");
  });
});

describe("grouping templates", () => {
  it("groups by family in the family order, drops empty families and puts unknown roles last", () => {
    const g = groupTemplates([t("a", "legal"), t("b", "software"), t("c", "marketing"), t("d", ""), t("e", "video")]);
    expect(g.map((x) => x.id)).toEqual(["build", "grow", "govern", "other"]);
    expect(g[0].items.map((i) => i.id)).toEqual(["b", "e"]);       // software before video, the family's order
    expect(g[3].items.map((i) => i.id)).toEqual(["d"]);
    expect(groupTemplates([t("x", "sales")]).map((x) => x.id)).toEqual(["grow"]);
  });
});

describe("protection", () => {
  it("lowering the class or letting the assistant write needs the second look; raising does not", () => {
    expect(lowersProtection("confidential", "internal", "read", "read")).toBe(true);
    expect(lowersProtection("internal", "internal", "read", "write")).toBe(true);
    expect(lowersProtection("internal", "confidential", "read", "read")).toBe(false);
    expect(lowersProtection("internal", "internal", "write", "read")).toBe(false);
  });
  it("the line for a tight place says class and access in plain words", () => {
    expect(badgeLine({ classification: "confidential", assistant_access: "read" })).toBe("Confidential · read only");
    expect(badgeLine({})).toBe("Internal · read only");
    expect(badgeLine({ assistant_access: "write" })).toBe("Internal · can edit");
    expect(ACCESS_META.read.label).not.toBe(ACCESS_META.write.label);
  });
});

describe("filtering several projects", () => {
  const list = [p("a", "software", "internal"), p("b", "legal", "confidential"), p("c", "software", "confidential"), p("d", "", undefined)];
  it("filters by role and by class, and by both", () => {
    expect(filterProjects(list, NO_FILTER).length).toBe(4);
    expect(filterProjects(list, { role: "software", cls: "all" }).map((x) => x.id)).toEqual(["a", "c"]);
    expect(filterProjects(list, { role: "all", cls: "confidential" }).map((x) => x.id)).toEqual(["b", "c"]);
    expect(filterProjects(list, { role: "software", cls: "confidential" }).map((x) => x.id)).toEqual(["c"]);
    expect(filterProjects(list, { role: "all", cls: "internal" }).map((x) => x.id)).toEqual(["a", "d"]);   // no class = internal
  });
  it("offers only values that exist, in the catalogue's order", () => {
    expect(presentValues(list)).toEqual({ roles: ["software", "legal"], classes: ["internal", "confidential"] });
    expect(presentValues([])).toEqual({ roles: [], classes: [] });
  });
});
