/**
 * api/custom_registry — extracted from the former monolithic lib/api.ts.
 * Public surface is unchanged; re-exported via the ../api.ts barrel.
 */

import { api } from "./client";

// ── ADR-0124 M1: Custom Engine Registry ──────────────────────────────────────

export interface CustomEngineModel {
  id: string;
  context_length: number;
}

export interface CustomEngineManifest {
  engine_id: string;
  display_name: string;
  transport: "openai_compat" | "anthropic" | "ollama";
  base_url_hash: string;
  auth_env: string | null;
  locality: "local" | "eu_cloud" | "us_cloud";
  network_egress: "none" | "restricted" | "full";
  models: CustomEngineModel[];
  data_classification: "PUBLIC" | "INTERNAL" | "CONFIDENTIAL";
  created_at: number;
  updated_at: number;
}

export interface CustomEngineListResponse {
  tenant_id: string;
  count: number;
  engines: CustomEngineManifest[];
}

export async function listCustomEngines(signal?: AbortSignal): Promise<CustomEngineListResponse> {
  return api<CustomEngineListResponse>("/engines/custom", { signal });
}

export interface CustomEngineRegisterRequest {
  display_name: string;
  transport: string;
  base_url: string;
  auth_env?: string | null;
  locality?: string;
  network_egress?: string;
  models?: CustomEngineModel[];
  data_classification?: string;
}

export async function registerCustomEngine(
  engine_id: string,
  body: CustomEngineRegisterRequest,
  csrf: string,
): Promise<{ ok: boolean; engine_id: string; updated: boolean }> {
  return api(`/engines/custom/${encodeURIComponent(engine_id)}`, {
    method: "PUT",
    body,
    csrf,
  });
}

export async function removeCustomEngine(
  engine_id: string,
  csrf: string,
): Promise<{ ok: boolean }> {
  return api(`/engines/custom/${encodeURIComponent(engine_id)}`, {
    method: "DELETE",
    csrf,
  });
}

export async function pingCustomEngine(
  engine_id: string,
  csrf: string,
): Promise<{ ok: boolean; reachable: boolean; model_count?: number; error?: string }> {
  return api(`/engines/custom/${encodeURIComponent(engine_id)}/ping`, {
    method: "POST",
    csrf,
  });
}

// ── ADR-0124 M2: Custom Connector Registry ────────────────────────────────────

export interface CustomConnectorManifest {
  connector_id: string;
  display_name: string;
  transport: "stdio" | "sse" | "http";
  command?: string[];
  url?: string;
  env_secrets: string[];
  capabilities: string[];
  locality: string;
  network_egress: string;
  description: string;
  created_at: number;
  updated_at: number;
}

export interface CustomConnectorListResponse {
  tenant_id: string;
  count: number;
  connectors: CustomConnectorManifest[];
}

export async function listCustomConnectors(signal?: AbortSignal): Promise<CustomConnectorListResponse> {
  return api<CustomConnectorListResponse>("/connectors/custom", { signal });
}

export interface CustomConnectorRegisterRequest {
  display_name: string;
  transport: string;
  command?: string[] | null;
  url?: string | null;
  env_secrets?: string[];
  capabilities?: string[];
  locality?: string;
  network_egress?: string;
  description?: string;
}

export async function registerCustomConnector(
  connector_id: string,
  body: CustomConnectorRegisterRequest,
  csrf: string,
): Promise<{ ok: boolean; connector_id: string; updated: boolean }> {
  return api(`/connectors/custom/${encodeURIComponent(connector_id)}`, {
    method: "PUT",
    body,
    csrf,
  });
}

export async function removeCustomConnector(
  connector_id: string,
  csrf: string,
): Promise<{ ok: boolean }> {
  return api(`/connectors/custom/${encodeURIComponent(connector_id)}`, {
    method: "DELETE",
    csrf,
  });
}


// ── ADR-0124 M7: Webhook Bridge ───────────────────────────────────────────────

export interface WebhookChannel {
  channel_id: string;
  display_name: string;
  hmac_secret_env: string | null;
  persona: string;
  rate_limit_per_hour: number;
  description: string;
  inbound_url: string;
  created_at: number;
  updated_at: number;
}

export interface WebhookChannelListResponse {
  tenant_id: string;
  count: number;
  channels: WebhookChannel[];
}

export async function listWebhookChannels(signal?: AbortSignal): Promise<WebhookChannelListResponse> {
  return api<WebhookChannelListResponse>("/bridges/custom", { signal });
}

export interface WebhookChannelRegisterRequest {
  display_name: string;
  hmac_secret_env?: string | null;
  persona?: string;
  rate_limit_per_hour?: number;
  description?: string;
}

export async function registerWebhookChannel(
  channel_id: string,
  body: WebhookChannelRegisterRequest,
  csrf: string,
): Promise<{ ok: boolean; channel_id: string; inbound_url: string; updated: boolean }> {
  return api(`/bridges/custom/${encodeURIComponent(channel_id)}`, {
    method: "PUT",
    body,
    csrf,
  });
}

export async function removeWebhookChannel(
  channel_id: string,
  csrf: string,
): Promise<{ ok: boolean }> {
  return api(`/bridges/custom/${encodeURIComponent(channel_id)}`, {
    method: "DELETE",
    csrf,
  });
}
