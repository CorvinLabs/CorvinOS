/**
 * The generic plugin inspector renders only state it was given. With none
 * (what the manifest route passes today) it must not show a working-looking
 * "Disable" button, "No configuration options" or "Audit Trail (0)".
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { GenericPluginInspector } from "@/components/GenericPluginInspector";

afterEach(() => cleanup());

function mount(props: Partial<React.ComponentProps<typeof GenericPluginInspector>> = {}) {
  return render(
    <MemoryRouter>
      <GenericPluginInspector pluginId="demo" title="Demo" version="1.0.0" {...props} />
    </MemoryRouter>,
  );
}

describe("GenericPluginInspector", () => {
  it("without loaded state: no toggle, no invented empty values", () => {
    mount();
    expect(screen.queryByRole("button", { name: /^(Disable|Enable)$/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Manage in Marketplace/ })).toHaveAttribute(
      "href",
      "/app/marketplace?tab=installed",
    );
    expect(screen.getByRole("status")).toHaveTextContent(/not available on this build/i);
    expect(screen.queryByText("No configuration options")).not.toBeInTheDocument();
    expect(screen.queryByText(/Audit Trail \(0\)/)).not.toBeInTheDocument();
    expect(screen.getByText("Not available on this build.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Audit Trail" }));
    expect(screen.queryByText("No audit events")).not.toBeInTheDocument();
    expect(screen.getByText("Not available on this build.")).toBeInTheDocument();
  });

  it("with real state: the toggle calls through and counts are shown", () => {
    const onToggle = vi.fn();
    mount({ enabled: true, onToggleEnabled: onToggle, config: {}, auditEvents: [{ a: 1 }] });
    fireEvent.click(screen.getByRole("button", { name: "Disable" }));
    expect(onToggle).toHaveBeenCalledWith(false);
    expect(screen.getByText("No configuration options")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Audit Trail (1)" })).toBeInTheDocument();
  });
});
