/**
 * Markdown cost while an answer streams in.
 *
 * Counts the characters handed to react-markdown's parser across a series of
 * streamed deltas. Before block-splitting every delta re-parses the whole
 * answer, so the cost grows with the answer; with the streaming flag only the
 * open block is parsed per delta.
 */
import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import { Markdown, splitStableMarkdown } from "@/components/markdown";

const parsed = vi.hoisted(() => ({ chars: [] as number[] }));

vi.mock("react-markdown", async (importOriginal) => {
  const mod = await importOriginal<typeof import("react-markdown")>();
  const { createElement } = await import("react");
  const Real = mod.default;
  const Counting = (props: { children?: unknown }) => {
    parsed.chars.push(String(props.children ?? "").length);
    return createElement(Real, props as never);
  };
  return { ...mod, default: Counting };
});

const ANSWER = Array.from(
  { length: 60 },
  (_, i) => `Paragraph ${i} with **bold** and \`code\` text that runs on for a while.`,
).join("\n\n");
const DELTAS = 100;
const DELTA = "more words here ";

describe("markdown parse cost while streaming", () => {
  it("parses far fewer characters per delta than a full re-parse", () => {
    let naive = 0;
    let text = ANSWER;
    for (let i = 0; i < DELTAS; i++) {
      text += DELTA;
      naive += text.length;
    }

    parsed.chars.length = 0;
    const { rerender } = render(<Markdown text={ANSWER} streaming />);
    text = ANSWER;
    for (let i = 0; i < DELTAS; i++) {
      text += DELTA;
      rerender(<Markdown text={text} streaming />);
    }
    const chars = parsed.chars.reduce((n, c) => n + c, 0);

    console.info(
      `[perf-measure] markdown chars parsed across ${DELTAS} streamed deltas ` +
        `on a ${ANSWER.length}-char answer: parsed=${chars}, full-reparse baseline=${naive}`,
    );
    expect(chars).toBeLessThan(naive * 0.5);
  });

  it("does not split an open code fence at a blank line", () => {
    const split = splitStableMarkdown("intro\n\n```ts\nconst a = 1;\n\nconst b = 2;\n");
    expect(split.stable).toBe("intro\n\n");
    expect(split.tail).toBe("```ts\nconst a = 1;\n\nconst b = 2;\n");
  });

  it("renders the finished answer identically to a non-streaming render", () => {
    const answer =
      "# Title\n\nPara with [a link](https://example.com).\n\n" +
      "| a | b |\n|---|---|\n| 1 | 2 |\n\n" +
      "```ts\nconst x = 1;\n\nconst y = 2;\n```\n\n- item one\n- item two\n";
    const streamed = render(<Markdown text={answer.slice(0, 40)} streaming />);
    streamed.rerender(<Markdown text={answer} streaming />);
    streamed.rerender(<Markdown text={answer} />);
    const fresh = render(<Markdown text={answer} />);
    expect(streamed.container.innerHTML).toBe(fresh.container.innerHTML);
  });
});
