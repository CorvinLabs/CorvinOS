/**
 * Public barrel for the console REST API client.
 *
 * The former ~6200-line monolith was split into cohesive domain modules under
 * ./api/. This file re-exports the identical public surface so every existing
 * `import { X } from "@/lib/api"` keeps resolving unchanged.
 *
 * NOTE (file-shadows-directory): this api.ts FILE wins @/lib/api resolution over
 * the api/ directory, so it is deliberately the single barrel entry point.
 */

// Core client — re-export exactly the names the monolith exported (BASE stays
// internal to the api/ modules, as it was before the split).
export {
  ApiError,
  api,
  setOn401Handler,
  setOnCsrfErrorHandler,
  isCsrfError,
  notifyCsrfError,
} from "./api/client";

// Domain modules (whole public surface each).
export * from "./api/personas";
export * from "./api/bridges";
export * from "./api/profile";
export * from "./api/skills";
export * from "./api/ldd_quality";
export * from "./api/audit";
export * from "./api/sessions";
export * from "./api/license";
export * from "./api/chat";
export * from "./api/workflows";
export * from "./api/compute";
export * from "./api/connectors";
export * from "./api/engines";
export * from "./api/settings";
export * from "./api/a2a";
export * from "./api/hubs";
export * from "./api/onboarding";
export * from "./api/memory";
export * from "./api/data";
export * from "./api/mcp";
export * from "./api/acs";
export * from "./api/datasources";
export * from "./api/extensions";
export * from "./api/custom_registry";
export * from "./api/agents";
export * from "./api/browser";
export * from "./api/plugins";
