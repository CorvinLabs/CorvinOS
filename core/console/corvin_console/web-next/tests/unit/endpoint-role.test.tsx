/**
 * The peers dialog shows and edits an endpoint's organisation and GDPR role.
 *
 * Renders the real EndpointRoleSection. Only the network function is mocked, so the path under test is
 * the operator's: see the pending notice, pick a role, save (CSRF PUT), see the result; a 422 shows fixed copy.
 * The pure mapping (normalisation, validation, fixed error copy) is tested without a DOM.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { EndpointRoleSection } from "@/components/chat/EndpointRoleSection";
import { putA2AEndpointGdprRole } from "@/lib/api/a2a";
import { ApiError } from "@/lib/api/client";
import { normaliseRole, organisationProblem, saveErrorCopy, ROLE_UNSET_NOTICE } from "@/lib/endpoint-role";

vi.mock("@/lib/api/a2a", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/a2a")>("@/lib/api/a2a");
  return { ...actual, putA2AEndpointGdprRole: vi.fn() };
});

describe("endpoint role helpers", () => {
  it("anything but a listed role is unset", () => {
    expect(normaliseRole(undefined)).toBe("unset");
    expect(normaliseRole("Processor")).toBe("unset");
    expect(normaliseRole("processor")).toBe("processor");
  });

  it("organisation rules mirror the server", () => {
    expect(organisationProblem("Acme GmbH")).toBeNull();
    expect(organisationProblem("AT&T Labs")).toBeNull();
    expect(organisationProblem("   ")).not.toBeNull();
    expect(organisationProblem("x".repeat(81))).not.toBeNull();
    expect(organisationProblem("<b>Acme</b>")).not.toBeNull();
    expect(organisationProblem("Acme‮")).not.toBeNull();
  });

  it("a failed save maps to fixed copy, never the server sentence", () => {
    const e422 = new ApiError(422, { detail: "organisation must not contain markup characters SERVER-SENTENCE" });
    const copy = saveErrorCopy(e422);
    expect(copy).not.toContain("SERVER-SENTENCE");
    expect(copy).toMatch(/not valid/);
    expect(saveErrorCopy(new ApiError(503, { detail: "x" }))).toMatch(/audit trail/);
    expect(saveErrorCopy(new Error("boom"))).toBe("Could not save the role. Try again.");
  });
});

describe("EndpointRoleSection", () => {
  beforeEach(() => {
    vi.mocked(putA2AEndpointGdprRole).mockReset();
  });

  it("an unset role shows the pending notice and no organisation", () => {
    render(<EndpointRoleSection endpointId="peer-a" organisation="" gdprRole="unset" csrf="csrf-token" />);
    expect(screen.getByText(ROLE_UNSET_NOTICE)).toBeTruthy();
    expect(screen.getByText("Set role")).toBeTruthy();
  });

  it("a stated role shows organisation and role and no notice", () => {
    render(<EndpointRoleSection endpointId="peer-a" organisation="Acme GmbH" gdprRole="processor" csrf="c" />);
    expect(screen.getByText("Acme GmbH")).toBeTruthy();
    expect(screen.getByText("Processor")).toBeTruthy();
    expect(screen.queryByText(ROLE_UNSET_NOTICE)).toBeNull();
    expect(screen.getByText("Edit role")).toBeTruthy();
  });

  it("saving sends the CSRF PUT with both fields and shows the stated result", async () => {
    vi.mocked(putA2AEndpointGdprRole).mockResolvedValue({
      ok: true, endpoint_id: "peer-a", organisation: "Acme GmbH", gdpr_role: "joint",
    });
    const onSaved = vi.fn();
    render(<EndpointRoleSection endpointId="peer-a" organisation="" gdprRole="unset" csrf="csrf-token" onSaved={onSaved} />);
    fireEvent.click(screen.getByText("Set role"));
    fireEvent.change(screen.getByLabelText("Organisation"), { target: { value: "  Acme GmbH " } });
    fireEvent.change(screen.getByLabelText("GDPR role"), { target: { value: "joint" } });
    fireEvent.click(screen.getByText("Save role"));
    await waitFor(() => expect(putA2AEndpointGdprRole).toHaveBeenCalledTimes(1));
    expect(putA2AEndpointGdprRole).toHaveBeenCalledWith(
      "peer-a", { organisation: "Acme GmbH", gdpr_role: "joint" }, "csrf-token");
    await waitFor(() => expect(screen.getByText("Joint controller")).toBeTruthy());
    expect(screen.queryByText(ROLE_UNSET_NOTICE)).toBeNull();
    expect(onSaved).toHaveBeenCalledWith({ organisation: "Acme GmbH", gdpr_role: "joint" });
  });

  it("an invalid organisation is refused locally without a request", () => {
    render(<EndpointRoleSection endpointId="peer-a" organisation="" gdprRole="unset" csrf="c" />);
    fireEvent.click(screen.getByText("Set role"));
    fireEvent.change(screen.getByLabelText("Organisation"), { target: { value: "<b>x</b>" } });
    fireEvent.change(screen.getByLabelText("GDPR role"), { target: { value: "processor" } });
    fireEvent.click(screen.getByText("Save role"));
    expect(putA2AEndpointGdprRole).not.toHaveBeenCalled();
    expect(screen.getByText(/plain text/)).toBeTruthy();
  });

  it("a 422 from the server shows fixed copy, not the server's sentence", async () => {
    vi.mocked(putA2AEndpointGdprRole).mockRejectedValue(new ApiError(422, { detail: "SERVER-SENTENCE" }));
    render(<EndpointRoleSection endpointId="peer-a" organisation="" gdprRole="unset" csrf="c" />);
    fireEvent.click(screen.getByText("Set role"));
    fireEvent.change(screen.getByLabelText("Organisation"), { target: { value: "Acme" } });
    fireEvent.change(screen.getByLabelText("GDPR role"), { target: { value: "processor" } });
    fireEvent.click(screen.getByText("Save role"));
    await waitFor(() => expect(screen.getByText(/not valid/)).toBeTruthy());
    expect(screen.queryByText(/SERVER-SENTENCE/)).toBeNull();
    expect(screen.getByText(ROLE_UNSET_NOTICE)).toBeTruthy();
  });

  it("withdrawing a role sends unset to the server", () => {
    render(<EndpointRoleSection endpointId="peer-a" organisation="Acme" gdprRole="processor" csrf="c" />);
    fireEvent.click(screen.getByText("Edit role"));
    fireEvent.change(screen.getByLabelText("GDPR role"), { target: { value: "unset" } });
    fireEvent.click(screen.getByText("Save role"));
    // withdrawing a role is allowed and goes to the server as "unset"
    expect(putA2AEndpointGdprRole).toHaveBeenCalledWith("peer-a", { organisation: "Acme", gdpr_role: "unset" }, "c");
  });
});
