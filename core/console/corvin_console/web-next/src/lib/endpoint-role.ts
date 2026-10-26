/**
 * Organisation and GDPR role of a paired endpoint — the operator's own statement.
 *
 * The role is never inferred from the partner (name, URL, label): `unset` is the default and is shown as a
 * pending decision. Errors from the save route map to FIXED copy; the server's sentence is never rendered.
 * Pure functions, so the mapping is testable without a DOM.
 */
import { ApiError } from "@/lib/api/client";

export const GDPR_ROLES = ["controller", "processor", "joint", "unset"] as const;
export type GdprRole = (typeof GDPR_ROLES)[number];

export const ORGANISATION_MAX = 80;

export const ROLE_LABELS: Record<GdprRole, string> = {
  controller: "Controller",
  processor: "Processor",
  joint: "Joint controller",
  unset: "Not set",
};

export const ROLE_UNSET_NOTICE =
  "Role not set: classified data to this partner is refused until you set one. Public data is not affected.";

export const ROLE_HELP =
  "This is your own statement about the partner, recorded in the audit trail. It is not checked against a contract.";

export function normaliseRole(value: unknown): GdprRole {
  return typeof value === "string" && (GDPR_ROLES as readonly string[]).includes(value)
    ? (value as GdprRole)
    : "unset";
}

const MARKUP = /[<>`[\]{}|\\]/;
// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u001f\u007f-\u009f​-‏‪-‮⁦-⁩﻿]/;

/** Client-side mirror of the server rule; the server stays the authority. Returns fixed copy or null. */
export function organisationProblem(value: string): string | null {
  const text = value.trim();
  if (text.length === 0) return "Enter the partner's organisation.";
  if (text.length > ORGANISATION_MAX) return `The organisation can have at most ${ORGANISATION_MAX} characters.`;
  if (MARKUP.test(text) || CONTROL.test(text)) {
    return "The organisation must be plain text (no markup characters or control characters).";
  }
  return null;
}

/** Fixed copy for a failed save. Never the server's sentence. */
export function saveErrorCopy(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 422) return "The organisation or role is not valid. Use plain text of up to 80 characters and one of the listed roles.";
    if (err.status === 404) return "This connection no longer exists.";
    if (err.status === 403) return "Your session expired. Reload the page and try again.";
    if (err.status === 503) return "The change could not be recorded in the audit trail, so nothing was saved. Try again.";
  }
  return "Could not save the role. Try again.";
}
