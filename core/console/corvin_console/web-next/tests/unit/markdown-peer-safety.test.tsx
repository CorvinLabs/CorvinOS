import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { Markdown } from "@/components/markdown";

// Round 3/4: text authored by an A2A peer must not make the browser fetch
// anything — neither a markdown image nor a mermaid diagram (whose SVG keeps
// <img src> after mermaid's own sanitiser).
describe("Markdown with blockRemoteImages (peer-authored text)", () => {
  it("renders mermaid as a code block, never as an SVG diagram", () => {
    const md = "```mermaid\nflowchart LR\n  A[\"<img src='/v1/console/auth/local-login'>\"]-->B\n```";
    const { container } = render(<Markdown text={md} blockRemoteImages />);
    // Rendered as a highlighted code block (<pre><code>), not handed to mermaid.
    expect(container.querySelector("pre code")).not.toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("flowchart LR");
    // Positive control: without the flag the same block goes to MermaidBlock
    // (no <pre><code> wrapper).
    const plain = render(<Markdown text={md} />).container;
    expect(plain.querySelector("pre code")).toBeNull();
  });

  it("turns same-origin and remote images into links", () => {
    const md = "![a](/v1/console/auth/local-login) ![b](https://t.example/p.png)";
    const { container } = render(<Markdown text={md} blockRemoteImages />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelectorAll("a").length).toBe(2);
  });
});
