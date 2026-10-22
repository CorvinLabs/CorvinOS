/**
 * Inspector form for one workflow node: every field the backend validator requires for
 * the node's type (fieldsFor), the route condition table, dependencies, save and delete.
 * Edits go through the node routes, which validate the WHOLE document before writing,
 * so the form can never leave an invalid YAML on disk.
 */
import * as React from "react";
import { Loader2, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  ROUTE_MODES,
  ROUTE_OPS,
  buildRouteCases,
  fieldsFor,
  getPath,
  isSupportedNodeType,
  missingRequiredFields,
  parseConditionValue,
  routeConditionCases,
  routeFallbackId,
  setPath,
  switchRouteMode,
  type FieldSpec,
  type RouteCase,
} from "@/lib/workflow-node-types";
import { PeerAgentPicker } from "@/components/workflows/PeerAgentPicker";
import { deleteWorkflowNode, putWorkflowNode, type GraphNode, type WorkflowNodeResponse } from "@/lib/api/workflows";

function listToText(value: unknown): string {
  return Array.isArray(value) ? value.map(String).join("\n") : "";
}

function pairsToText(value: unknown): string {
  if (!value || typeof value !== "object" || Array.isArray(value)) return "";
  return Object.entries(value as Record<string, unknown>)
    .map(([k, v]) => `${k}=${String(v)}`)
    .join("\n");
}

function textToList(text: string): string[] {
  return text.split("\n").map((l) => l.trim()).filter(Boolean);
}

function textToPairs(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of textToList(text)) {
    const i = line.indexOf("=");
    if (i > 0) out[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return out;
}

/**
 * Textarea that keeps what the user typed. The parsed value (list / pairs) drops blank lines
 * and whitespace, so rendering it back on every keystroke would eat the Enter key and spaces.
 * The raw text is the state; the parsed value is pushed up on every change.
 */
function RawTextArea({
  id,
  initial,
  onText,
}: {
  id: string;
  initial: string;
  onText: (text: string) => void;
}) {
  const [text, setText] = React.useState(initial);
  return (
    <Textarea
      id={id}
      data-testid={id}
      className="min-h-[56px] font-mono text-xs"
      value={text}
      onChange={(e) => {
        setText(e.target.value);
        onText(e.target.value);
      }}
    />
  );
}

/** Condition value: free text while typing, typed (number/bool/list) when committed on blur. */
function ConditionValueInput({
  label,
  testId,
  value,
  op,
  onCommit,
}: {
  label: string;
  testId: string;
  value: unknown;
  op: string;
  onCommit: (v: unknown) => void;
}) {
  const shown = Array.isArray(value) ? value.join(",") : String(value ?? "");
  const [text, setText] = React.useState(shown);
  React.useEffect(() => setText(shown), [shown]);
  return (
    <Input
      aria-label={label}
      data-testid={testId}
      className="h-7 text-xs"
      placeholder="value"
      value={text}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => {
        if (text !== shown) onCommit(parseConditionValue(op, text));
      }}
    />
  );
}

function FieldRow({
  field,
  spec,
  onChange,
}: {
  field: FieldSpec;
  spec: Record<string, unknown>;
  onChange: (path: string, value: unknown) => void;
}) {
  const raw = getPath(spec, field.path);
  const id = `node-field-${field.path}`;
  let control: React.ReactNode;
  switch (field.kind) {
    case "textarea":
      control = (
        <Textarea
          id={id}
          data-testid={id}
          className="min-h-[72px] font-mono text-xs"
          value={typeof raw === "string" ? raw : ""}
          onChange={(e) => onChange(field.path, e.target.value)}
        />
      );
      break;
    case "number":
      control = (
        <Input
          id={id}
          data-testid={id}
          type="number"
          className="h-8 text-xs"
          value={typeof raw === "number" ? raw : ""}
          onChange={(e) => onChange(field.path, e.target.value === "" ? undefined : Number(e.target.value))}
        />
      );
      break;
    case "select":
      control = (
        <Select
          id={id}
          data-testid={id}
          className="h-8 text-xs"
          value={typeof raw === "string" ? raw : ""}
          onChange={(e) => onChange(field.path, e.target.value || undefined)}
        >
          {!field.required && <option value="">(default)</option>}
          {field.required && typeof raw !== "string" && <option value="">Select…</option>}
          {(field.options ?? []).map((o) => (
            <option key={o} value={o}>{o}</option>
          ))}
        </Select>
      );
      break;
    case "boolean":
      control = (
        <input
          id={id}
          data-testid={id}
          type="checkbox"
          checked={raw === true}
          onChange={(e) => onChange(field.path, e.target.checked ? true : undefined)}
        />
      );
      break;
    case "list":
      control = (
        <RawTextArea id={id} initial={listToText(raw)} onText={(t) => onChange(field.path, textToList(t))} />
      );
      break;
    case "pairs":
      control = (
        <RawTextArea id={id} initial={pairsToText(raw)} onText={(t) => onChange(field.path, textToPairs(t))} />
      );
      break;
    default:
      control = (
        <Input
          id={id}
          data-testid={id}
          className="h-8 text-xs"
          value={typeof raw === "string" ? raw : ""}
          onChange={(e) => onChange(field.path, e.target.value)}
        />
      );
  }
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
        {field.label}
        {field.required && <span className="text-destructive"> *</span>}
      </label>
      {control}
      {field.hint && <p className="text-[10px] text-muted-foreground">{field.hint}</p>}
    </div>
  );
}

function RouteConditionEditor({
  spec,
  onChange,
}: {
  spec: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
}) {
  const rows = routeConditionCases(spec);
  const fallback = routeFallbackId(spec);
  const commit = (r: RouteCase[], f: string) => onChange({ ...spec, cases: buildRouteCases(r, f) });
  const patchRow = (i: number, patch: { id?: string; when?: Partial<RouteCase["when"]> }) => {
    const next = rows.map((r, idx) =>
      idx === i ? { ...r, ...patch, when: { ...r.when, ...(patch.when ?? {}) } } : r,
    );
    commit(next, fallback);
  };
  return (
    <div className="space-y-2" data-testid="route-cases">
      <div className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
        cases <span className="text-destructive">*</span>
      </div>
      {rows.map((r, i) => (
        <div key={i} className="grid grid-cols-[1fr_1fr_64px_1fr_24px] gap-1">
          <Input
            aria-label={`case ${i + 1} id`}
            data-testid={`route-case-id-${i}`}
            className="h-7 text-xs"
            placeholder="case id"
            value={r.id}
            onChange={(e) => patchRow(i, { id: e.target.value })}
          />
          <Input
            aria-label={`case ${i + 1} selector`}
            data-testid={`route-case-selector-${i}`}
            className="h-7 text-xs"
            placeholder="node.field"
            value={r.when.selector}
            onChange={(e) => patchRow(i, { when: { selector: e.target.value } })}
          />
          <Select
            aria-label={`case ${i + 1} operator`}
            data-testid={`route-case-op-${i}`}
            className="h-7 px-1 text-xs"
            value={r.when.op}
            onChange={(e) => patchRow(i, { when: { op: e.target.value } })}
          >
            {ROUTE_OPS.map((o) => <option key={o} value={o}>{o}</option>)}
          </Select>
          <ConditionValueInput
            label={`case ${i + 1} value`}
            testId={`route-case-value-${i}`}
            value={r.when.value}
            op={r.when.op}
            onCommit={(v) => patchRow(i, { when: { value: v } })}
          />
          <button
            type="button"
            aria-label={`remove case ${i + 1}`}
            className="text-muted-foreground hover:text-destructive"
            onClick={() => commit(rows.filter((_, idx) => idx !== i), fallback)}
          >
            ×
          </button>
        </div>
      ))}
      <div className="flex items-center gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-7 text-xs"
          data-testid="route-add-case"
          onClick={() => commit([...rows, { id: `case_${rows.length + 1}`, when: { selector: "", op: "==", value: "" } }], fallback)}
        >
          Add case
        </Button>
        <label className="text-[10px] uppercase tracking-widest text-muted-foreground" htmlFor="route-fallback">
          fallback id <span className="text-destructive">*</span>
        </label>
        <Input
          id="route-fallback"
          data-testid="route-fallback"
          className="h-7 flex-1 text-xs"
          value={fallback}
          onChange={(e) => commit(rows, e.target.value)}
        />
      </div>
    </div>
  );
}

export function NodeSpecEditor({
  node,
  allNodeIds,
  wid,
  csrf,
  onChanged,
}: {
  node: GraphNode;
  allNodeIds: string[];
  wid: string;
  csrf: string;
  onChanged: (res: WorkflowNodeResponse, deletedId?: string) => void;
}) {
  const [draft, setDraft] = React.useState<Record<string, unknown>>(node.spec ?? {});
  const [busy, setBusy] = React.useState<"save" | "delete" | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  // A new node id always resets the form. A fresh spec for the SAME node (graph refetch,
  // design-chat update, the response to our own save) only replaces the draft when the user
  // has no unsaved edits: an unrelated graph update must not wipe typing.
  const lastSpecRef = React.useRef<string>(JSON.stringify(node.spec ?? {}));
  const lastIdRef = React.useRef(node.id);
  React.useEffect(() => {
    const incoming = JSON.stringify(node.spec ?? {});
    const idChanged = lastIdRef.current !== node.id;
    setDraft((d) => (idChanged || JSON.stringify(d) === lastSpecRef.current ? (node.spec ?? {}) : d));
    if (idChanged) setError(null);
    lastIdRef.current = node.id;
    lastSpecRef.current = incoming;
  }, [node.id, node.spec]);

  if (!isSupportedNodeType(String(draft.type ?? node.type))) {
    return (
      <div
        className="rounded-lg border border-amber-400/40 bg-amber-50/50 p-2 text-xs text-amber-700 dark:bg-amber-950/20 dark:text-amber-400"
        data-testid="node-unsupported"
      >
        Node type <code className="font-mono">{node.type}</code> is not an executable node type, so
        this workflow cannot run until the node is replaced.
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="mt-2 h-7 text-xs"
          disabled={busy !== null}
          data-testid="node-delete"
          onClick={async () => {
            setBusy("delete");
            setError(null);
            try {
              onChanged(await deleteWorkflowNode(wid, node.id, csrf), node.id);
            } catch (e) {
              setError(e instanceof Error ? e.message : String(e));
            } finally {
              setBusy(null);
            }
          }}
        >
          Remove node
        </Button>
        {error && <p className="mt-1 text-destructive">{error}</p>}
      </div>
    );
  }

  const onField = (path: string, value: unknown) => setDraft((d) => setPath(d, path, value));
  const missing = missingRequiredFields(draft);
  const dirty = JSON.stringify(draft) !== JSON.stringify(node.spec ?? {});
  const others = allNodeIds.filter((id) => id !== node.id);
  const deps = Array.isArray(draft.depends_on) ? (draft.depends_on as string[]) : [];

  const run = async (kind: "save" | "delete") => {
    setBusy(kind);
    setError(null);
    try {
      if (kind === "save") onChanged(await putWorkflowNode(wid, node.id, draft, csrf));
      else onChanged(await deleteWorkflowNode(wid, node.id, csrf), node.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-3" data-testid="node-spec-editor">
      {String(draft.type) === "route" && (
        <div className="space-y-1">
          <label htmlFor="node-field-mode" className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
            mode <span className="text-destructive">*</span>
          </label>
          <Select
            id="node-field-mode"
            data-testid="node-field-mode"
            className="h-8 text-xs"
            value={String(draft.mode ?? "condition")}
            onChange={(e) => setDraft((d) => switchRouteMode(d, e.target.value as (typeof ROUTE_MODES)[number]))}
          >
            {ROUTE_MODES.map((m) => <option key={m} value={m}>{m}</option>)}
          </Select>
        </div>
      )}
      {String(draft.type) === "peer_agent" && (
        <PeerAgentPicker
          endpointId={typeof draft.endpoint_id === "string" ? draft.endpoint_id : ""}
          agentId={typeof draft.agent_id === "string" ? draft.agent_id : ""}
          csrf={csrf}
          onChange={(endpointId, agentId) =>
            setDraft((d) => setPath(setPath(d, "endpoint_id", endpointId), "agent_id", agentId))
          }
        />
      )}
      {fieldsFor(draft)
        .filter((f) => f.path !== "mode" && f.kind !== "peer")
        .map((f) => (
          <FieldRow key={f.path} field={f} spec={draft} onChange={onField} />
        ))}
      {String(draft.type) === "route" && draft.mode !== "classify" && (
        <RouteConditionEditor spec={draft} onChange={setDraft} />
      )}
      {others.length > 0 && (
        <div className="space-y-1">
          <div className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground">depends on</div>
          <div className="flex flex-wrap gap-x-3 gap-y-1">
            {others.map((id) => (
              <label key={id} className="flex items-center gap-1 text-xs">
                <input
                  type="checkbox"
                  data-testid={`node-dep-${id}`}
                  checked={deps.includes(id)}
                  onChange={(e) =>
                    onField("depends_on", e.target.checked ? [...deps, id] : deps.filter((d) => d !== id))
                  }
                />
                <span className="font-mono">{id}</span>
              </label>
            ))}
          </div>
        </div>
      )}
      {missing.length > 0 && (
        <p className="text-[11px] text-amber-600 dark:text-amber-400" data-testid="node-missing">
          Required: {missing.join(", ")}
        </p>
      )}
      {error && (
        <p className="rounded-md bg-destructive/10 px-2 py-1 text-[11px] text-destructive" data-testid="node-error">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button
          type="button"
          size="sm"
          className="h-7 flex-1 text-xs"
          data-testid="node-save"
          disabled={busy !== null || missing.length > 0 || !dirty}
          onClick={() => run("save")}
        >
          {busy === "save" && <Loader2 className="h-3 w-3 animate-spin" />}
          Save node
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-7 text-xs text-destructive"
          data-testid="node-delete"
          disabled={busy !== null}
          onClick={() => run("delete")}
        >
          <Trash2 className="h-3 w-3" />
          Delete
        </Button>
      </div>
    </div>
  );
}
