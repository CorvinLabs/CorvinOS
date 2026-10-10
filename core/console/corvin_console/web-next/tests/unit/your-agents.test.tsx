/**
 * "Your agents": the console control that was missing — register the Claude Code agent in one click, and share
 * it with paired peers by an explicit switch. Without it a fresh install could not make `/ask @peer` work.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { YourAgents } from "@/components/agent-conversations/YourAgents";
import { listLocalAgents, registerDefaultAgent, setAgentFederable, type LocalAgent } from "@/lib/api/federation";

vi.mock("@/lib/api/federation", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/federation")>("@/lib/api/federation");
  return { ...actual, listLocalAgents: vi.fn(), registerDefaultAgent: vi.fn(), setAgentFederable: vi.fn() };
});

const agent = (p: Partial<LocalAgent>): LocalAgent => ({
  agent_id: "claude-code", engine_type: "claude_code", model: "claude-code",
  capabilities: ["analysis"], federable: false, ...p,
});

function renderIt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><YourAgents csrf="csrf-1" /></QueryClientProvider>);
}

describe("YourAgents", () => {
  beforeEach(() => vi.resetAllMocks());

  it("a fresh install offers one-click registration and says why it matters", async () => {
    vi.mocked(listLocalAgents).mockResolvedValue({ agents: [] });
    renderIt();
    expect(await screen.findByTestId("your-agents-empty")).toBeTruthy();
    expect(screen.getByText(/nobody to use/)).toBeTruthy();
    expect(screen.queryByTestId("your-agents-list")).toBeNull();
  });

  it("clicking register calls the API with the session's CSRF token and then shows the agent", async () => {
    vi.mocked(listLocalAgents).mockResolvedValueOnce({ agents: [] }).mockResolvedValue({ agents: [agent({})] });
    vi.mocked(registerDefaultAgent).mockResolvedValue(agent({}));
    renderIt();
    fireEvent.click(await screen.findByTestId("register-default-agent"));
    await waitFor(() => expect(registerDefaultAgent).toHaveBeenCalledWith("csrf-1"));
    expect(await screen.findByTestId("your-agent")).toBeTruthy();
    expect(screen.queryByTestId("your-agents-empty")).toBeNull();
  });

  it("a registered agent is NOT shared by default and the switch has an accessible name", async () => {
    vi.mocked(listLocalAgents).mockResolvedValue({ agents: [agent({})] });
    renderIt();
    const sw = await screen.findByRole("switch", { name: "Share claude-code with paired peers" });
    expect(sw.getAttribute("aria-checked")).toBe("false");
    expect(setAgentFederable).not.toHaveBeenCalled();
  });

  it("turning the switch on sends the explicit opt-in, and off sends the opt-out", async () => {
    vi.mocked(listLocalAgents).mockResolvedValueOnce({ agents: [agent({})] })
      .mockResolvedValueOnce({ agents: [agent({ federable: true })] })
      .mockResolvedValue({ agents: [agent({ federable: false })] });
    vi.mocked(setAgentFederable).mockResolvedValue(agent({ federable: true }));
    renderIt();
    fireEvent.click(await screen.findByRole("switch"));
    await waitFor(() => expect(setAgentFederable).toHaveBeenCalledWith("claude-code", true, "csrf-1"));
    await waitFor(() => expect(screen.getByRole("switch").getAttribute("aria-checked")).toBe("true"));
    fireEvent.click(screen.getByRole("switch"));
    await waitFor(() => expect(setAgentFederable).toHaveBeenLastCalledWith("claude-code", false, "csrf-1"));
  });

  it("an agent on another engine cannot be shared and never calls the API", async () => {
    vi.mocked(listLocalAgents).mockResolvedValue({ agents: [agent({ agent_id: "codex-x", engine_type: "codex_cli", federable: true })] });
    renderIt();
    const sw = await screen.findByRole("switch", { name: "Share codex-x with paired peers" });
    expect((sw as HTMLButtonElement).disabled).toBe(true);
    expect(sw.getAttribute("aria-checked")).toBe("false");   // a stale flag must not read as "shared"
    expect(screen.getByText("Not shareable")).toBeTruthy();
    fireEvent.click(sw);
    expect(setAgentFederable).not.toHaveBeenCalled();
  });

  it("a failed change shows the server's reason instead of pretending it worked", async () => {
    vi.mocked(listLocalAgents).mockResolvedValue({ agents: [agent({})] });
    vi.mocked(setAgentFederable).mockRejectedValue(new Error("the change could not be recorded"));
    renderIt();
    fireEvent.click(await screen.findByRole("switch"));
    const alert = await screen.findByTestId("your-agents-error");
    expect(alert.textContent).toMatch(/could not be recorded/);
    expect(screen.getByRole("switch").getAttribute("aria-checked")).toBe("false");
  });

  it("a failed registration is reported and the button comes back", async () => {
    vi.mocked(listLocalAgents).mockResolvedValue({ agents: [] });
    vi.mocked(registerDefaultAgent).mockRejectedValue(new Error("registration could not be recorded"));
    renderIt();
    fireEvent.click(await screen.findByTestId("register-default-agent"));
    expect((await screen.findByTestId("your-agents-error")).textContent).toMatch(/registration could not be recorded/);
    await waitFor(() => expect((screen.getByTestId("register-default-agent") as HTMLButtonElement).disabled).toBe(false));
  });

  it("a list that cannot be loaded says so", async () => {
    vi.mocked(listLocalAgents).mockRejectedValue(new Error("boom"));
    renderIt();
    expect((await screen.findByRole("alert")).textContent).toMatch(/Could not load your agents/);
    expect(screen.queryByTestId("register-default-agent")).toBeNull();
  });

  it("shows no ADR ids or internal jargon in the rendered text", async () => {
    vi.mocked(listLocalAgents).mockResolvedValue({ agents: [] });
    const { container } = renderIt();
    await screen.findByTestId("your-agents-empty");
    expect(container.textContent).not.toMatch(/ADR-\d+/);
  });
});
