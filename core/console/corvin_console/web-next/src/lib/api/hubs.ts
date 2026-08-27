/**
 * api/hubs — extracted from the former monolithic lib/api.ts.
 * Public surface is unchanged; re-exported via the ../api.ts barrel.
 */

import { ApiError, api } from "./client";

// ── File Hub ───────────────────────────────────────────────────────────

export interface FileEntry {
  name: string;
  rel_path: string;
  is_dir: boolean;
  size_bytes: number;
  mtime: number | null;
  access: "full" | "read" | "none";
  children?: FileEntry[];
}

export interface FileTreeResponse {
  tenant_id: string;
  root: string;
  path: string;
  is_dir: boolean;
  size_bytes: number;
  mtime: number | null;
  access: "full" | "read" | "none";
  ts: number;
  children: FileEntry[];
  quota: {
    used_bytes: number;
    limit_bytes: number;
    used_pct: number;
  };
}

export async function listFilesTree(
  path = "",
  depth = 2,
  signal?: AbortSignal,
): Promise<FileTreeResponse> {
  const q = new URLSearchParams({ depth: String(depth) });
  if (path) q.set("path", path);
  return api<FileTreeResponse>(`/files/tree?${q}`, { signal });
}

export interface FileContentResponse {
  path: string;
  name: string;
  size_bytes: number;
  mtime: number | null;
  mime: string;
  kind: "text" | "image" | "binary";
  content?: string | null;
  content_b64?: string;
  truncated?: boolean;
}

export async function getFileContent(
  path: string,
  signal?: AbortSignal,
): Promise<FileContentResponse> {
  return api<FileContentResponse>(
    `/files/content?path=${encodeURIComponent(path)}`,
    { signal },
  );
}

export function fileDownloadUrl(path: string): string {
  return `/v1/console/files/download?path=${encodeURIComponent(path)}`;
}

export async function uploadFile(
  dir: string,
  file: File,
  csrf: string,
): Promise<{ ok: true; path: string; name: string; size_bytes: number }> {
  const form = new FormData();
  form.append("file", file, file.name);
  const res = await fetch(
    `/v1/console/files/upload?dir=${encodeURIComponent(dir)}`,
    {
      method: "POST",
      credentials: "include",
      headers: { "X-CSRF-Token": csrf },
      body: form,
    },
  );
  if (!res.ok) {
    const text = await res.text();
    throw new ApiError(res.status, text);
  }
  return res.json();
}

export async function deleteFile(
  path: string,
  csrf: string,
): Promise<{ ok: true; path: string; deleted: true }> {
  return api(`/files?path=${encodeURIComponent(path)}`, { method: "DELETE", csrf });
}

export async function createDir(
  path: string,
  csrf: string,
): Promise<{ ok: true; path: string }> {
  return api("/files/mkdir", { method: "POST", csrf, body: { path } });
}

// ── CorvinSpace (Layer 40) ─────────────────────────────────────────

export interface SpaceProfile {
  display_name: string;
  bio: string;
  contact_handle: string;
  website: string;
  location: string;
  created_at: number;
  updated_at: number;
}

export interface SpaceDomain {
  slug: string;
  name: string;
  description: string;
  visibility: "public" | "followers" | "private";
  created_at: number;
  updated_at: number;
  post_count: number;
}

export interface SocialStatus {
  tenant_id: string;
  status: {
    is_enabled: boolean;
    consented_at: number | null;
    actor_id: string | null;
  } | null;
  follower_count: number | null;
  following_count: number | null;
  ts: number;
}

export interface SocialActor {
  actor_id: string;
  display_name: string | null;
  inbox_url: string;
  compliance_zone: string | null;
  is_ai: boolean;
  relationship: string;
}

export async function getSpaceProfile(signal?: AbortSignal) {
  return api<{ profile: SpaceProfile | null; social_actor_id: string | null; tenant_id: string }>("/space/profile", { signal });
}

export async function updateSpaceProfile(csrf: string, data: Partial<SpaceProfile>, signal?: AbortSignal) {
  return api<{ profile: SpaceProfile }>("/space/profile", { method: "PUT", csrf, body: data, signal });
}

export async function getSpaceDomains(signal?: AbortSignal) {
  return api<{ domains: SpaceDomain[]; max_domains: number; license_unlimited: boolean }>("/space/domains", { signal });
}

export async function createSpaceDomain(csrf: string, data: { slug: string; name: string; description?: string; visibility?: string }, signal?: AbortSignal) {
  return api<SpaceDomain>("/space/domains", { method: "POST", csrf, body: data, signal });
}

export async function deleteSpaceDomain(csrf: string, slug: string, signal?: AbortSignal) {
  return api<{ ok: boolean }>(`/space/domains/${slug}`, { method: "DELETE", csrf, signal });
}

export async function publishToDomain(csrf: string, slug: string, data: { content: string; tags?: string[]; visibility?: string }, signal?: AbortSignal) {
  return api<{ ok: boolean; post_id: string }>(`/space/domains/${slug}/publish`, { method: "POST", csrf, body: data, signal });
}

export async function getSocialStatus(signal?: AbortSignal) {
  return api<SocialStatus>("/space/social/status", { signal });
}

export async function joinSocial(csrf: string, data: { display_name: string; host: string; compliance_zone: string }, signal?: AbortSignal) {
  return api<{ status: string; actor_id?: string }>("/space/social/join", { method: "POST", csrf, body: data, signal });
}

export async function leaveSocial(csrf: string, signal?: AbortSignal) {
  return api<{ status: string }>("/space/social/leave", { method: "POST", csrf, signal });
}

export async function followActor(csrf: string, data: { actor_id: string; inbox_url: string; public_key_hex: string; display_name?: string; compliance_zone?: string }, signal?: AbortSignal) {
  return api<{ ok: boolean }>("/space/social/follow", { method: "POST", csrf, body: data, signal });
}

export async function getSocialFollowing(signal?: AbortSignal) {
  return api<{ actors: SocialActor[] }>("/space/social/following", { signal });
}

export async function getSocialFollowers(signal?: AbortSignal) {
  return api<{ actors: SocialActor[] }>("/space/social/followers", { signal });
}

// ── Social Capability Grants (Layer 41) ────────────────────────────────

export interface Grant {
  grant_id: string;
  grantee_actor: string;
  grantor_actor: string;
  capabilities: string[];
  conditions: Record<string, unknown>;
  issued_at: number | null;
  revoked_at: number | null;
}

export interface GrantTemplate {
  id: string;
  label: string;
  description: string;
  capabilities: string[];
  conditions: Record<string, unknown>;
  requires_confirmation?: boolean;
}

export interface GrantListResponse {
  local_actor_id: string;
  grants: Grant[];
  ts: number;
}

export interface GrantTemplatesResponse {
  templates: GrantTemplate[];
}

export async function listGrantTemplates(signal?: AbortSignal): Promise<GrantTemplatesResponse> {
  return api<GrantTemplatesResponse>("/grants/templates", { signal });
}

export async function listGrants(
  params: { grantee_actor?: string; include_revoked?: boolean } = {},
  signal?: AbortSignal,
): Promise<GrantListResponse> {
  const q = new URLSearchParams();
  if (params.grantee_actor) q.set("grantee_actor", params.grantee_actor);
  if (params.include_revoked) q.set("include_revoked", "true");
  const qs = q.toString();
  return api<GrantListResponse>(`/grants${qs ? `?${qs}` : ""}`, { signal });
}

export async function createGrant(
  body: { grantee_actor: string; capabilities: string[]; conditions?: Record<string, unknown> },
  csrf: string,
): Promise<{ ok: true; grant: Grant; ts: number }> {
  return api("/grants", { method: "POST", csrf, body });
}

export async function revokeGrant(
  grant_id: string,
  csrf: string,
): Promise<{ ok: true; ts: number }> {
  return api(`/grants/${encodeURIComponent(grant_id)}`, { method: "DELETE", csrf });
}

// ── CorvinOrg — Organisations (Layer 42) ──────────────────────────────

export interface OrgSummary {
  handle: string;
  actor_id: string;
  display_name: string;
  summary: string;
  verified_domain: string | null;
  member_count: number;
  agent_count: number;
}

export interface OrgMember {
  actor_id: string;
  role: "owner" | "admin" | "editor" | "agent";
  added_at?: number | null;
}

export interface OrgEndorsement {
  endorsement_id: string;
  agent_actor_id: string;
  org_actor_id: string;
  scope: string[];
  issued_at: number | null;
  expires_at: number | null;
  revoked_at: number | null;
}

export interface OrgDetail {
  handle: string;
  actor: {
    id: string | null;
    display_name: string | null;
    summary: string | null;
    public_key_hex: string;
    verified_domain: string | null;
    affiliated_actors: string[];
    created_at: number | null;
  };
  config: {
    responsible_party: string | null;
    policy: Record<string, unknown>;
  };
  members: OrgMember[];
  agents: OrgEndorsement[];
  grants: Grant[];
  ts: number;
}

export interface OrgListResponse {
  orgs: OrgSummary[];
  ts: number;
}

export async function listOrgs(signal?: AbortSignal): Promise<OrgListResponse> {
  return api<OrgListResponse>("/orgs", { signal });
}

export async function createOrg(
  body: { handle: string; display_name: string; summary?: string; host?: string },
  csrf: string,
): Promise<{ ok: true; org: OrgSummary; ts: number }> {
  return api("/orgs", { method: "POST", csrf, body });
}

export async function getOrg(handle: string, signal?: AbortSignal): Promise<OrgDetail> {
  return api<OrgDetail>(`/orgs/${encodeURIComponent(handle)}`, { signal });
}

export async function dissolveOrg(
  handle: string,
  csrf: string,
): Promise<{ ok: true; ts: number }> {
  return api(`/orgs/${encodeURIComponent(handle)}`, { method: "DELETE", csrf });
}

export async function addOrgMember(
  handle: string,
  body: { actor_id: string; role: "owner" | "admin" | "editor" | "agent" },
  csrf: string,
): Promise<{ ok: true; members: OrgMember[]; ts: number }> {
  return api(`/orgs/${encodeURIComponent(handle)}/members`, { method: "POST", csrf, body });
}

export async function removeOrgMember(
  handle: string,
  actor_id: string,
  csrf: string,
): Promise<{ ok: true; members: OrgMember[]; ts: number }> {
  return api(
    `/orgs/${encodeURIComponent(handle)}/members?actor_id=${encodeURIComponent(actor_id)}`,
    { method: "DELETE", csrf },
  );
}

export async function updateOrgMemberRole(
  handle: string,
  actor_id: string,
  role: "owner" | "admin" | "editor" | "agent",
  csrf: string,
): Promise<{ ok: true; members: OrgMember[]; ts: number }> {
  return api(`/orgs/${encodeURIComponent(handle)}/members`, {
    method: "PATCH",
    csrf,
    body: { actor_id, role },
  });
}

export async function affiliateOrgAgent(
  handle: string,
  body: { agent_actor_id: string; scope?: string[]; ttl_days?: number },
  csrf: string,
): Promise<{ ok: true; endorsement: OrgEndorsement; ts: number }> {
  return api(`/orgs/${encodeURIComponent(handle)}/agents`, { method: "POST", csrf, body });
}

export async function deaffiliateOrgAgent(
  handle: string,
  endorsement_id: string,
  csrf: string,
): Promise<{ ok: true; ts: number }> {
  return api(
    `/orgs/${encodeURIComponent(handle)}/agents/${encodeURIComponent(endorsement_id)}`,
    { method: "DELETE", csrf },
  );
}

export async function listOrgGrants(
  handle: string,
  include_revoked = false,
  signal?: AbortSignal,
): Promise<{ grants: Grant[]; ts: number }> {
  const q = include_revoked ? "?include_revoked=true" : "";
  return api(`/orgs/${encodeURIComponent(handle)}/grants${q}`, { signal });
}

export async function createOrgGrant(
  handle: string,
  body: { grantee_actor: string; capabilities: string[]; conditions?: Record<string, unknown> },
  csrf: string,
): Promise<{ ok: true; grant: Grant; ts: number }> {
  return api(`/orgs/${encodeURIComponent(handle)}/grants`, { method: "POST", csrf, body });
}

export async function revokeOrgGrant(
  handle: string,
  grant_id: string,
  csrf: string,
): Promise<{ ok: true; ts: number }> {
  return api(
    `/orgs/${encodeURIComponent(handle)}/grants/${encodeURIComponent(grant_id)}`,
    { method: "DELETE", csrf },
  );
}

export interface OrgNetworkNode {
  id: string;
  label: string;
  type: "human" | "agent" | "org";
  role?: string | null;
}

export interface OrgNetworkEdge {
  from: string;
  to: string;
  type: "member" | "agent" | "grant";
  caps: string[];
}

export async function getOrgNetwork(
  handle: string,
  signal?: AbortSignal,
): Promise<{ org_handle: string; nodes: OrgNetworkNode[]; edges: OrgNetworkEdge[] }> {
  return api(`/orgs/${encodeURIComponent(handle)}/network`, { signal });
}
