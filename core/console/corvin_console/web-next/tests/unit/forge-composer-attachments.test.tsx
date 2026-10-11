/**
 * ForgeComposer attachments (CONCEPT-0104 P3, T-0145). The generators do not pass an attachment
 * controller yet (Intake, ADR-2254, is not built), so there is no real page to drive end to end:
 * this exercises the shipped component directly. Until a generator enables it, NO attach control
 * may render - a button whose files never reach the engine would mislead.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import ForgeComposer, { type ComposerAttachment, type ComposerAttachments } from "@/components/forge/ForgeComposer";

vi.mock("@/hooks/use-voice-input", () => ({ useVoiceInput: () => ({ recording: false, startRecording: vi.fn(), stopRecording: vi.fn() }) }));

afterEach(cleanup);

const base = (over: Partial<React.ComponentProps<typeof ForgeComposer>> = {}) => ({
  value: "a request long enough to send", onChange: vi.fn(), onSubmit: vi.fn(), busy: false, submitDisabled: false,
  csrf: "c", onError: vi.fn(), textareaTestId: "t-request", submitTestId: "t-submit", textareaId: "t-request",
  label: "Request", placeholder: "p", submitLabel: "Go", busyLabel: "Going…", ...over,
});
const att = (items: ComposerAttachment[], extra: Partial<ComposerAttachments> = {}): ComposerAttachments =>
  ({ items, addFiles: vi.fn(), remove: vi.fn(), ...extra });
const chip = (id: string, verdict: ComposerAttachment["verdict"], note?: string): ComposerAttachment => ({ id, name: `${id}.csv`, size: 10, verdict, note });
const file = (name: string) => new File(["a,b\n1,2"], name, { type: "text/csv" });

describe("ForgeComposer attachments", () => {
  it("renders no attach control when no controller is passed", () => {
    render(<ForgeComposer {...base()} />);
    expect(screen.queryByTestId("t-request-attach")).toBeNull();
    expect(screen.queryByTestId("t-request-attach-input")).toBeNull();
  });

  it("adds files from the picker through the controller and resets the input", () => {
    const a = att([]);
    render(<ForgeComposer {...base({ attachments: a })} />);
    const input = screen.getByTestId("t-request-attach-input") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file("one.csv"), file("two.csv")] } });
    expect(a.addFiles).toHaveBeenCalledTimes(1);
    expect((a.addFiles as any).mock.calls[0][0].map((f: File) => f.name)).toEqual(["one.csv", "two.csv"]);
  });

  it("adds dropped files", () => {
    const a = att([]);
    render(<ForgeComposer {...base({ attachments: a })} />);
    fireEvent.drop(screen.getByTestId("t-request-composer"), { dataTransfer: { files: [file("dropped.csv")] } });
    expect(a.addFiles).toHaveBeenCalledTimes(1);
  });

  it("takes only the room left and says what it left out (never silently)", () => {
    const a = att([chip("x", "unanalysed")], { maxItems: 2 });
    render(<ForgeComposer {...base({ attachments: a })} />);
    fireEvent.drop(screen.getByTestId("t-request-composer"), { dataTransfer: { files: [file("a.csv"), file("b.csv"), file("c.csv")] } });
    expect((a.addFiles as any).mock.calls[0][0]).toHaveLength(1);
    expect(screen.getByTestId("t-request-attach-note")).toHaveTextContent("2 left out");
  });

  it("shows an honest verdict per chip - 'not analysed' until Intake exists, never an invented one", () => {
    render(<ForgeComposer {...base({ attachments: att([chip("a", "unanalysed"), chip("b", "admitted_redacted", "2 columns hashed")]) })} />);
    const verdicts = screen.getAllByTestId("attachment-verdict").map((e) => e.textContent);
    expect(verdicts).toEqual(["not analysed", "admitted, redacted: 2 columns hashed"]);
  });

  it("removes a chip through the controller", () => {
    const a = att([chip("a", "admitted")]);
    render(<ForgeComposer {...base({ attachments: a })} />);
    fireEvent.click(screen.getByRole("button", { name: "Remove a.csv" }));
    expect(a.remove).toHaveBeenCalledWith("a");
  });

  it("a refused attachment blocks both the button and Enter, and says why", () => {
    const onSubmit = vi.fn();
    render(<ForgeComposer {...base({ onSubmit, attachments: att([chip("bad", "refused", "contains a secret")]) })} />);
    expect(screen.getByTestId("t-submit")).toBeDisabled();
    fireEvent.keyDown(screen.getByTestId("t-request"), { key: "Enter" });
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByTestId("t-request-attach-note")).toHaveTextContent("Remove the refused attachment");
    expect(screen.getByTestId("attachment-chip")).toHaveAttribute("data-verdict", "refused");
  });

  it("an upload in flight blocks Send; with nothing refused Enter sends once (positive control)", () => {
    const onSubmit = vi.fn();
    const { rerender } = render(<ForgeComposer {...base({ onSubmit, attachments: att([], { uploading: true }) })} />);
    fireEvent.keyDown(screen.getByTestId("t-request"), { key: "Enter" });
    expect(onSubmit).not.toHaveBeenCalled();
    rerender(<ForgeComposer {...base({ onSubmit, attachments: att([chip("ok", "admitted")]) })} />);
    fireEvent.keyDown(screen.getByTestId("t-request"), { key: "Enter" });
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });
});
