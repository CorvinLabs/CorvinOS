/**
 * Review round 2 (2026-10-05) regressions in pure chat helpers.
 */
import { describe, it, expect } from "vitest";
import { attachmentNames } from "@/components/chat/GroupConversation";
import { encodeFilesForA2A } from "@/lib/api/a2a";

describe("group attachment header", () => {
  it("keeps names with spaces and every file after them", () => {
    const text = [
      "[Attached files — stored with this group]",
      "- attachments/my report.pdf (1.2 KB, application/pdf)",
      "- attachments/b.png (0.1 KB, image/png)",
      "",
      "see files",
    ].join("\n");
    expect(attachmentNames(text)).toEqual(["my report.pdf", "b.png"]);
  });
  it("ignores text that does not start with the header", () => {
    expect(attachmentNames("- attachments/x.txt (1 KB, text/plain)")).toEqual([]);
  });
});

describe("peer attachment names match the receiver's rules", () => {
  it("prefixes Windows device names, drops trailing dots, dedupes case-insensitively", async () => {
    const f = (n: string) => new File(["x"], n, { type: "text/plain" });
    const out = await encodeFilesForA2A([f("CON.txt"), f("a.txt"), f("A.TXT"), f("note.")]);
    expect(out.map((a) => a.name)).toEqual(["_CON.txt", "a.txt", "A_2.TXT", "note"]);
  });
  it("dedupes against names already staged", async () => {
    const out = await encodeFilesForA2A([new File(["x"], "a.txt")], ["A.txt"]);
    expect(out[0].name).toBe("a_2.txt");
  });
});
