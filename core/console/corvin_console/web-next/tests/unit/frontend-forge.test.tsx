/** Proof for FrontendForge (ADR-0364 P6): editor renders, srcDoc live preview
 *  mounts through PanelHost, downloadPanel produces the authored HTML. */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import FrontendForgePage, { STARTER_PANEL, downloadPanel } from "@/pages/frontend-forge";

afterEach(() => vi.restoreAllMocks());

describe("FrontendForgePage", () => {
  it("renders the editor seeded with the starter panel and a sandboxed srcDoc preview", () => {
    const { container, getByLabelText } = render(
      <MemoryRouter><FrontendForgePage /></MemoryRouter>,
    );
    const ta = getByLabelText("Panel HTML source") as HTMLTextAreaElement;
    expect(ta.value).toBe(STARTER_PANEL);

    const iframe = container.querySelector("iframe") as HTMLIFrameElement;
    expect(iframe).toBeTruthy();
    // live preview is srcDoc (not src) and allow-scripts only (no same-origin)
    expect(iframe.getAttribute("srcdoc")).toContain("corvin:panel:ready");
    expect(iframe.getAttribute("sandbox")).toBe("allow-scripts");
  });

  it("editing the textarea updates the live preview srcDoc", () => {
    const { container, getByLabelText } = render(
      <MemoryRouter><FrontendForgePage /></MemoryRouter>,
    );
    const ta = getByLabelText("Panel HTML source") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "<h1>edited</h1>" } });
    const iframe = container.querySelector("iframe") as HTMLIFrameElement;
    expect(iframe.getAttribute("srcdoc")).toBe("<h1>edited</h1>");
  });

  it("downloadPanel writes the authored HTML via a blob url", () => {
    const createURL = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:x");
    const revokeURL = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    downloadPanel("<h1>hi</h1>", "my-panel.html");

    expect(createURL).toHaveBeenCalledOnce();
    const blob = createURL.mock.calls[0][0] as Blob;
    expect(blob.type).toBe("text/html");
    expect(click).toHaveBeenCalledOnce();
    expect(revokeURL).toHaveBeenCalledWith("blob:x");
  });
});
