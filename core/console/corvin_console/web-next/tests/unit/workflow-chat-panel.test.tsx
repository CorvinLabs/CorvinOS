/**
 * CONCEPT-0115 P4: the run-reply panel (ask_human) uses the shared composer.
 * A reply the server refuses (pre-spawn gate, 403) is put back into the field.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const resume = vi.fn();
vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  resumeWorkflowRun: (...a: unknown[]) => resume(...a),
}));
vi.mock("@/hooks/use-voice-input", () => ({
  useVoiceInput: () => ({ recording: false, startRecording: vi.fn(async () => {}), stopRecording: vi.fn() }),
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ refresh: vi.fn(), csrf: "c" }) }));

import { WorkflowChatPanel } from "@/pages/workflows";

afterEach(() => { cleanup(); resume.mockReset(); });

function mount(onResumed = vi.fn()) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <WorkflowChatPanel wid="w" rid="r" nodeId="ask" prompt="Confirm?" history={[]} csrf="c" onResumed={onResumed} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { onResumed, ta: screen.getByTestId("workflow-composer-textarea") as HTMLTextAreaElement };
}

describe("WorkflowChatPanel reply composer", () => {
  it("sends the reply through resume and reports the outcome", async () => {
    resume.mockResolvedValue({ ok: true, status: "complete", confirmed: true });
    const { onResumed, ta } = mount();
    fireEvent.change(ta, { target: { value: "yes" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    await waitFor(() => expect(onResumed).toHaveBeenCalledWith("confirmed"));
    expect(resume).toHaveBeenCalledWith("w", "r", "yes", "c");
    expect(screen.queryByTestId("workflow-file-input")).toBeNull(); // text-only context
  });

  it("a refused reply shows the server's message and keeps the text", async () => {
    resume.mockRejectedValue(new Error("Refused by the house rules."));
    const { onResumed, ta } = mount();
    fireEvent.change(ta, { target: { value: "my answer" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    await waitFor(() => expect(screen.getByText("Refused by the house rules.")).toBeTruthy());
    expect(onResumed).not.toHaveBeenCalled();
    await waitFor(() => expect(ta.value).toBe("my answer"));
  });
});
