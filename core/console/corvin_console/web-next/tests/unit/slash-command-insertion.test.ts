/**
 * The palette's Enter/Tab handler used to fall through to "send as-is" on
 * Enter (only Tab inserted) and discarded the argument placeholder on
 * insert (`cmd + " "`, never the `<arg>` text) — so there was nothing to
 * select and an operator had to type the whole placeholder by hand.
 * `buildCommandInsertion` is the pure function behind both keys now;
 * covered separately from the keyboard-interaction E2E test so the
 * placeholder-selection math is pinned without re-rendering a component
 * for every case.
 */
import { describe, expect, it } from "vitest";
import { buildCommandInsertion, SLASH_COMMANDS, type SlashCommand } from "@/components/chat/SlashCommandPalette";

// Peer-thread commands are no longer a client-side constant (ADR-2235
// Alternatives (e) — PeerConversation fetches them from
// GET /v1/console/peer-thread/commands). Shaped the same way the server
// response is, to pin the insertion math independently of that fetch.
const ASK_MINE: SlashCommand = { cmd: "/ask @mine", args: "<task>", desc: "" };
const TALK: SlashCommand = { cmd: "/talk", args: "<topic>", desc: "" };

describe("buildCommandInsertion", () => {
  it("appends a trailing space and places the cursor after it when the command takes no args", () => {
    const help = SLASH_COMMANDS.find((c) => c.cmd === "/help")!;
    const { text, selStart, selEnd } = buildCommandInsertion(help);
    expect(text).toBe("/help ");
    expect(selStart).toBe(text.length);
    expect(selEnd).toBe(text.length); // empty selection = just a cursor position
  });

  it("inserts the argument placeholder and selects exactly it, not the command", () => {
    const persona = SLASH_COMMANDS.find((c) => c.cmd === "/persona")!;
    const { text, selStart, selEnd } = buildCommandInsertion(persona);
    expect(text).toBe("/persona <name>");
    expect(text.slice(selStart, selEnd)).toBe("<name>");
  });

  it("selects the placeholder for a server-supplied peer-thread command (e.g. /ask @mine)", () => {
    const { text, selStart, selEnd } = buildCommandInsertion(ASK_MINE);
    expect(text).toBe("/ask @mine <task>");
    expect(text.slice(selStart, selEnd)).toBe("<task>");

    const talkInsertion = buildCommandInsertion(TALK);
    expect(talkInsertion.text).toBe("/talk <topic>");
    expect(talkInsertion.text.slice(talkInsertion.selStart, talkInsertion.selEnd)).toBe("<topic>");
  });
});
