/**
 * Wiring: the real PeerManagementDialog renders each outbound endpoint with its organisation and GDPR role,
 * an unset role as a pending notice, and saving goes out as the CSRF PUT. Only the network functions are mocked.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PeerManagementDialog } from "@/components/chat/PeerManagementDialog";
import * as api from "@/lib/api";
import { putA2AEndpointGdprRole } from "@/lib/api/a2a";
import { ROLE_UNSET_NOTICE } from "@/lib/endpoint-role";

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ session: { tenant_id: "_default", csrf_token: "csrf-test", tier: "owner" }, loading: false }),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getA2AOrigins: vi.fn(),
    getA2AEndpoints: vi.fn(),
    getLicenseInfo: vi.fn(),
  };
});

// EndpointRoleSection imports the save function from the module itself, not from the barrel.
vi.mock("@/lib/api/a2a", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/a2a")>("@/lib/api/a2a");
  return { ...actual, putA2AEndpointGdprRole: vi.fn() };
});

function endpoint(id: string, extra: Record<string, unknown> = {}) {
  return {
    endpoint_id: id, url: "https://peer.example.com/v1/a2a/receive", instance_id_pin: "iid-" + id,
    enabled: true, default_ttl_s: 60, state: "ACTIVE", label: "Peer " + id, ...extra,
  };
}

function open() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <PeerManagementDialog trigger={<button>Manage peers</button>} />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByText("Manage peers"));
}

describe("PeerManagementDialog endpoint role", () => {
  beforeEach(() => {
    vi.mocked(api.getA2AOrigins).mockResolvedValue({ ts: 1, count: 0, origins: [] });
    vi.mocked(api.getLicenseInfo).mockResolvedValue({ limits: {} } as never);
    vi.mocked(api.getA2AEndpoints).mockResolvedValue({
      ts: 1, count: 2,
      endpoints: [
        endpoint("peer-a", { organisation: "Acme GmbH", gdpr_role: "processor" }),
        // a token pairing with no stated role: editable too
        endpoint("peer-f", { _friendship: true, organisation: "", gdpr_role: "unset" }),
      ],
    } as never);
  });

  it("shows the stated role on one peer and the pending notice on the other", async () => {
    open();
    await waitFor(() => expect(screen.getAllByTestId("endpoint-role")).toHaveLength(2));
    const [a, f] = screen.getAllByTestId("endpoint-role");
    expect(within(a).getByText("Acme GmbH")).toBeTruthy();
    expect(within(a).getByText("Processor")).toBeTruthy();
    expect(within(a).queryByText(ROLE_UNSET_NOTICE)).toBeNull();
    expect(within(f).getByText(ROLE_UNSET_NOTICE)).toBeTruthy();
  });

  it("the operator sets the role of a token pairing through the CSRF PUT", async () => {
    vi.mocked(putA2AEndpointGdprRole).mockResolvedValue({
      ok: true, endpoint_id: "peer-f", organisation: "Quiet AG", gdpr_role: "controller",
    });
    open();
    await waitFor(() => expect(screen.getAllByTestId("endpoint-role")).toHaveLength(2));
    const f = screen.getAllByTestId("endpoint-role")[1];
    fireEvent.click(within(f).getByText("Set role"));
    fireEvent.change(within(f).getByLabelText("Organisation"), { target: { value: "Quiet AG" } });
    fireEvent.change(within(f).getByLabelText("GDPR role"), { target: { value: "controller" } });
    fireEvent.click(within(f).getByText("Save role"));
    await waitFor(() => expect(putA2AEndpointGdprRole).toHaveBeenCalledWith(
      "peer-f", { organisation: "Quiet AG", gdpr_role: "controller" }, "csrf-test"));
    await waitFor(() => expect(within(screen.getAllByTestId("endpoint-role")[1]).getByText("Controller")).toBeTruthy());
  });
});
