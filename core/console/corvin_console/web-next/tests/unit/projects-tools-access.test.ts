/**
 * REVIEW-0069 N1: a feature that brings tools (Agentic compute) gives them to an attached chat only when the assistant may edit
 * files (attachment.spawn_parts). The console says so where the user decides - derived from assistant_access, never fixed text.
 */
import { describe, expect, it } from "vitest";
import { TOOLS_NEED_EDIT, chatToolsNote, featureToolsNote, toolsWithheldFeatures } from "@/pages/projects/roles";

const acs = { id: "acs", title: "Agentic compute", tools: true, active: true };
const kb = { id: "kb", title: "Knowledge base", tools: false, active: true };
const detail = (access: "read" | "write" | undefined, rows = [acs, kb]) => ({ assistant_access: access, catalogue: { available: true, features: rows } });

describe("tools of a read-only project", () => {
  it("a tool feature on a read-only project says its tools do not reach a chat", () => {
    expect(featureToolsNote(acs, { assistant_access: "read" })).toBe(`On, but its tools are not given to a chat. ${TOOLS_NEED_EDIT}`);
    expect(featureToolsNote({ ...acs, active: false }, { assistant_access: "read" })).toBe(TOOLS_NEED_EDIT);
    expect(featureToolsNote(acs, {})).toBe(`On, but its tools are not given to a chat. ${TOOLS_NEED_EDIT}`);   // missing = read
  });
  it("says nothing when the assistant may edit, or the feature brings no tools", () => {
    expect(featureToolsNote(acs, { assistant_access: "write" })).toBeNull();
    expect(featureToolsNote(kb, { assistant_access: "read" })).toBeNull();
    expect(featureToolsNote({ id: "x", active: true }, { assistant_access: "read" })).toBeNull();
  });
  it("lists only the ACTIVE tool features of a read-only project", () => {
    expect(toolsWithheldFeatures(detail("read"))).toEqual(["Agentic compute"]);
    expect(toolsWithheldFeatures(detail("write"))).toEqual([]);
    expect(toolsWithheldFeatures(detail("read", [{ ...acs, active: false }, kb]))).toEqual([]);
    expect(toolsWithheldFeatures({ assistant_access: "read" })).toEqual([]);
  });
  it("the Use-in-a-chat sentence names the features and the rule", () => {
    expect(chatToolsNote(detail("read"))).toBe(`The tools of Agentic compute are not given to a chat while the assistant reads only. ${TOOLS_NEED_EDIT}`);
    expect(chatToolsNote(detail("read", [acs, { ...acs, id: "y", title: "Other tools" }])))
      .toBe(`The tools of Agentic compute and Other tools are not given to a chat while the assistant reads only. ${TOOLS_NEED_EDIT}`);
    expect(chatToolsNote(detail("write"))).toBeNull();
    expect(chatToolsNote(detail("read", [kb]))).toBeNull();
  });
});
