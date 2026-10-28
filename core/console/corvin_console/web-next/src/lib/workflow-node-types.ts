/**
 * Node types the workflow editor may create and edit.
 *
 * Mirrors the executor registry in core/workflows/corvin_workflows/node_types.py
 * (NODE_TYPES). The backend validator (R7/R10) is the authority: it rejects any
 * node whose type is not in that registry, so this list must never contain a
 * type without an executor. Workflows saved with an older type still load; the
 * editor marks those nodes as unsupported instead of rendering them as valid.
 */

export const SUPPORTED_NODE_TYPES = [
  "agent",
  "fan_out",
  "delegation_loop",
  "deliver",
  "code",
  "merge",
  "route",
  "answer",
  "ask_human",
  "approval",
  "peer_agent",
] as const;

export type NodeTypeId = (typeof SUPPORTED_NODE_TYPES)[number];

export const DELIVER_CHANNELS = ["discord", "telegram", "slack", "whatsapp", "email", "signal", "teams"] as const;
export const DELIVER_FORMATS = ["markdown", "text"] as const;
export const CODE_LANGUAGES = ["python3"] as const;
export const MERGE_STRATEGIES = ["concat_list", "first_non_empty", "dict_union"] as const;
export const ROUTE_MODES = ["condition", "classify"] as const;
export const ROUTE_OPS = ["==", "!=", ">", ">=", "<", "<=", "contains", "in"] as const;
/** ADR-2308: the tier names a node may name; a full registry model id is also accepted by the
 * backend validator and is shown as-is when a node already carries one. */
export const NODE_MODEL_TIERS = ["haiku", "sonnet", "opus"] as const;

export function isSupportedNodeType(type: string): type is NodeTypeId {
  return (SUPPORTED_NODE_TYPES as readonly string[]).includes(type);
}

export type FieldKind = "text" | "textarea" | "number" | "select" | "boolean" | "list" | "pairs" | "peer";

export interface FieldSpec {
  /** Dotted path into the node, e.g. "config.budget.max_loops". */
  path: string;
  label: string;
  kind: FieldKind;
  required?: boolean;
  options?: readonly string[];
  hint?: string;
}

const AGENT_FIELD: FieldSpec = { path: "agent", label: "agent", kind: "text", required: true, hint: "engine agent name" };
const INSTRUCTIONS_FIELD: FieldSpec = { path: "instructions", label: "instructions", kind: "textarea" };
/** ADR-2308: only the node types that call a model carry it. Unset = the path default; an
 * operator pin on the Models page overrides it. */
const MODEL_FIELD: FieldSpec = {
  path: "model",
  label: "model",
  kind: "select",
  options: NODE_MODEL_TIERS,
  hint: "unset = path default; an operator pin on the Models page overrides it",
};
const CHANNEL_FIELD: FieldSpec = { path: "channel", label: "channel", kind: "select", required: true, options: DELIVER_CHANNELS };
const CHAT_ID_FIELD: FieldSpec = {
  path: "chat_id",
  label: "chat_id",
  kind: "text",
  hint: "channel ID, or 'auto' for the triggering chat",
};

const CLASSIFY_FIELDS: FieldSpec[] = [
  AGENT_FIELD,
  { path: "classes", label: "classes", kind: "list", required: true, hint: "one class per line" },
  { path: "input", label: "input", kind: "text", required: true, hint: "selector, e.g. trigger.message" },
  MODEL_FIELD,
];

/** Inspector fields for a supported node type, in display order. */
export function fieldsFor(spec: Record<string, unknown>): FieldSpec[] {
  switch (spec.type) {
    case "agent":
      return [AGENT_FIELD, INSTRUCTIONS_FIELD, MODEL_FIELD];
    case "fan_out":
      return [
        AGENT_FIELD,
        { path: "items_from", label: "items_from", kind: "text", required: true, hint: "state field, e.g. node.items" },
        INSTRUCTIONS_FIELD,
        MODEL_FIELD,
      ];
    case "delegation_loop":
      return [
        { path: "config.manager", label: "manager", kind: "text", required: true, hint: "manager agent name" },
        { path: "config.budget.max_loops", label: "max_loops", kind: "number", required: true },
        { path: "config.budget.max_total_workers", label: "max_total_workers", kind: "number", required: true },
        INSTRUCTIONS_FIELD,
      ];
    case "deliver":
      return [
        { ...CHANNEL_FIELD, path: "config.channel" },
        { ...CHAT_ID_FIELD, path: "config.chat_id", required: true },
        { path: "config.format", label: "format", kind: "select", options: DELIVER_FORMATS },
        { path: "config.voice", label: "voice", kind: "boolean" },
      ];
    case "code":
      return [
        { path: "language", label: "language", kind: "select", required: true, options: CODE_LANGUAGES },
        { path: "source", label: "source", kind: "textarea", required: true, hint: "must define def main(...)" },
        { path: "outputs", label: "outputs", kind: "list", required: true, hint: "one field name per line" },
        { path: "inputs", label: "inputs", kind: "pairs", hint: "one 'param=selector' per line" },
      ];
    case "merge":
      return [
        { path: "strategy", label: "strategy", kind: "select", required: true, options: MERGE_STRATEGIES },
        { path: "inputs", label: "inputs", kind: "list", required: true, hint: "one selector per line" },
        { path: "output", label: "output", kind: "text", required: true },
      ];
    case "route": {
      const mode = spec.mode === "classify" ? "classify" : "condition";
      const modeField: FieldSpec = { path: "mode", label: "mode", kind: "select", required: true, options: ROUTE_MODES };
      // condition cases are edited by RouteConditionEditor; only classify has plain fields.
      return mode === "classify" ? [modeField, ...CLASSIFY_FIELDS] : [modeField];
    }
    case "answer":
      return [
        CHANNEL_FIELD,
        CHAT_ID_FIELD,
        { path: "text", label: "text", kind: "textarea", required: true, hint: "literal reply text" },
      ];
    case "ask_human":
      return [
        CHANNEL_FIELD,
        CHAT_ID_FIELD,
        { path: "prompt", label: "prompt", kind: "textarea", required: true, hint: "literal question text" },
        { path: "timeout_minutes", label: "timeout_minutes", kind: "number", hint: "optional, positive" },
      ];
    case "approval":
      return [
        { path: "message", label: "message", kind: "textarea", hint: "what the reviewer is asked to decide" },
        { path: "timeout_s", label: "timeout_s", kind: "number", hint: "optional, seconds (max 7 days), default 3600" },
      ];
    case "peer_agent":
      return [
        // Both are set together by the peer picker (kind "peer"), never typed.
        { path: "endpoint_id", label: "peer", kind: "peer", required: true },
        { path: "agent_id", label: "peer agent", kind: "peer", required: true },
        { path: "instruction", label: "instruction", kind: "textarea", required: true, hint: "sent to the peer agent; it leaves this installation" },
        { path: "timeout_s", label: "timeout_s", kind: "number", hint: "optional, 1-900 seconds, default 300" },
      ];
    default:
      return [];
  }
}

export function getPath(obj: Record<string, unknown>, path: string): unknown {
  let cur: unknown = obj;
  for (const key of path.split(".")) {
    if (cur === null || typeof cur !== "object") return undefined;
    cur = (cur as Record<string, unknown>)[key];
  }
  return cur;
}

/** Returns a copy of `obj` with `path` set; `undefined` removes the key. */
export function setPath(obj: Record<string, unknown>, path: string, value: unknown): Record<string, unknown> {
  const [head, ...rest] = path.split(".");
  const copy: Record<string, unknown> = { ...obj };
  if (rest.length === 0) {
    if (value === undefined) delete copy[head];
    else copy[head] = value;
    return copy;
  }
  const child = copy[head];
  const childObj = child && typeof child === "object" && !Array.isArray(child) ? (child as Record<string, unknown>) : {};
  copy[head] = setPath(childObj, rest.join("."), value);
  return copy;
}

function isEmptyValue(value: unknown): boolean {
  if (value === undefined || value === null) return true;
  if (typeof value === "string") return value.trim() === "";
  if (typeof value === "number") return Number.isNaN(value);
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === "object") return Object.keys(value as object).length === 0;
  return false;
}

export interface RouteCase {
  id: string;
  when: { selector: string; op: string; value: unknown };
}

/** Condition cases of a route node (the non-default rows). */
export function routeConditionCases(spec: Record<string, unknown>): RouteCase[] {
  const cases = Array.isArray(spec.cases) ? spec.cases : [];
  return cases.flatMap((c): RouteCase[] => {
    if (!c || typeof c !== "object") return [];
    const row = c as Record<string, unknown>;
    const when = row.when;
    if (!when || typeof when !== "object") return [];
    const w = when as Record<string, unknown>;
    return [{ id: String(row.id ?? ""), when: { selector: String(w.selector ?? ""), op: String(w.op ?? "=="), value: w.value } }];
  });
}

/** Id of the mandatory `when: default` fallback case, or "" if none. */
export function routeFallbackId(spec: Record<string, unknown>): string {
  const cases = Array.isArray(spec.cases) ? spec.cases : [];
  const fallback = cases.find((c) => c && typeof c === "object" && (c as Record<string, unknown>).when === "default");
  return fallback ? String((fallback as Record<string, unknown>).id ?? "") : "";
}

/** Rebuilds the cases list: the given rows, then exactly one default fallback. */
export function buildRouteCases(rows: RouteCase[], fallbackId: string): Record<string, unknown>[] {
  return [
    ...rows.map((r) => ({ id: r.id, when: { selector: r.when.selector, op: r.when.op, value: r.when.value } })),
    { id: fallbackId, when: "default" },
  ];
}

/**
 * Coerces the typed condition value: numbers and booleans stay typed, `in`
 * takes a comma-separated list. Other operators compare strings.
 */
export function parseConditionValue(op: string, raw: string): unknown {
  if (op === "in") return raw.split(",").map((s) => s.trim()).filter(Boolean);
  const trimmed = raw.trim();
  if (trimmed === "true") return true;
  if (trimmed === "false") return false;
  if (/^-?\d+(\.\d+)?$/.test(trimmed)) return Number(trimmed);
  return raw;
}

/** Field labels a node still needs before the backend validator accepts it. */
export function missingRequiredFields(spec: Record<string, unknown>): string[] {
  const type = String(spec.type ?? "");
  if (!isSupportedNodeType(type)) return [];
  const missing = fieldsFor(spec)
    .filter((f) => f.required && isEmptyValue(getPath(spec, f.path)))
    .map((f) => f.label);
  if (type === "route" && spec.mode !== "classify") {
    const rows = routeConditionCases(spec);
    if (rows.length === 0) missing.push("cases");
    if (routeFallbackId(spec) === "") missing.push("fallback case");
    rows.forEach((r, i) => {
      if (!r.id || !r.when.selector) missing.push(`case ${i + 1}`);
    });
  }
  return missing;
}

/** Id not yet used in `existing`, derived from the node type. */
export function nextNodeId(type: NodeTypeId, existing: readonly string[]): string {
  const taken = new Set(existing);
  let candidate: string = type;
  for (let n = 2; taken.has(candidate); n += 1) candidate = `${type}_${n}`;
  return candidate;
}

export interface PaletteEntry {
  type: NodeTypeId;
  label: string;
  emoji: string;
  /** True when the node needs a paired peer agent to be valid: the caller supplies one from the catalog. */
  needsPeer?: boolean;
  /** Builds a node that passes the validator for that type. */
  create: (id: string, peer?: PeerTarget) => Record<string, unknown>;
}

export interface PeerTarget {
  endpoint_id: string;
  agent_id: string;
}

export const NODE_PALETTE: readonly PaletteEntry[] = [
  {
    type: "agent",
    label: "Agent",
    emoji: "🤖",
    create: (id) => ({ id, type: "agent", depends_on: [], agent: "assistant", instructions: "" }),
  },
  {
    type: "fan_out",
    label: "Fan-out",
    emoji: "⤆",
    create: (id) => ({ id, type: "fan_out", depends_on: [], agent: "assistant", items_from: "items", instructions: "" }),
  },
  {
    type: "delegation_loop",
    label: "Delegation loop",
    emoji: "⟳",
    create: (id) => ({
      id,
      type: "delegation_loop",
      depends_on: [],
      config: { manager: "assistant", budget: { max_loops: 3, max_total_workers: 5 } },
    }),
  },
  {
    type: "deliver",
    label: "Send",
    emoji: "📤",
    create: (id) => ({ id, type: "deliver", depends_on: [], config: { channel: "discord", chat_id: "auto", format: "markdown" } }),
  },
  {
    type: "code",
    label: "Code",
    emoji: "λ",
    create: (id) => ({
      id,
      type: "code",
      depends_on: [],
      language: "python3",
      source: 'def main(text=""):\n    return {"result": text}\n',
      inputs: {},
      outputs: ["result"],
    }),
  },
  {
    type: "merge",
    label: "Merge",
    emoji: "⑂",
    create: (id) => ({ id, type: "merge", depends_on: [], strategy: "concat_list", inputs: ["input.text"], output: "merged" }),
  },
  {
    type: "route",
    label: "Route",
    emoji: "⑃",
    create: (id) => ({
      id,
      type: "route",
      depends_on: [],
      mode: "condition",
      cases: [
        { id: "yes", when: { selector: "input.value", op: "==", value: "yes" } },
        { id: "otherwise", when: "default" },
      ],
    }),
  },
  {
    type: "answer",
    label: "Answer",
    emoji: "💬",
    create: (id) => ({
      id,
      type: "answer",
      depends_on: [],
      channel: "discord",
      chat_id: "auto",
      text: "Edit this answer in the inspector.",
    }),
  },
  {
    type: "ask_human",
    label: "Ask human",
    emoji: "🙋",
    create: (id) => ({
      id,
      type: "ask_human",
      depends_on: [],
      channel: "discord",
      chat_id: "auto",
      prompt: "Please confirm.",
    }),
  },
  {
    type: "approval",
    label: "Approval",
    emoji: "👤",
    create: (id) => ({ id, type: "approval", depends_on: [], message: "Please review and approve or reject to continue.", timeout_s: 3600 }),
  },
  {
    type: "peer_agent",
    label: "Peer agent",
    emoji: "🤝",
    needsPeer: true,
    create: (id, peer) => ({
      id,
      type: "peer_agent",
      depends_on: [],
      endpoint_id: peer?.endpoint_id ?? "",
      agent_id: peer?.agent_id ?? "",
      instruction: "Describe what the peer agent should do.",
    }),
  },
];

/** Switches a route node between condition and classify with mode-correct defaults. */
export function switchRouteMode(spec: Record<string, unknown>, mode: (typeof ROUTE_MODES)[number]): Record<string, unknown> {
  const base: Record<string, unknown> = { ...spec, mode };
  if (mode === "classify") {
    const { cases: _cases, ...rest } = base;
    return { agent: "assistant", classes: ["yes", "no"], input: "input.text", ...rest };
  }
  // A condition route never calls a model, and the backend rejects a model on one (ADR-2308).
  const { agent: _agent, classes: _classes, input: _input, model: _model, ...rest } = base;
  return {
    ...rest,
    cases: [
      { id: "yes", when: { selector: "input.value", op: "==", value: "yes" } },
      { id: "otherwise", when: "default" },
    ],
  };
}
