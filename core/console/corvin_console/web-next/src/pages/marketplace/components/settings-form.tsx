/**
 * Schema-driven settings form (ADR-0892 amendment, 2026-09-20).
 *
 * A plugin's `settings_schema` (plugin.yaml, JSON-Schema shaped:
 * `properties.<key>.{type, default, description, enum, minimum, maximum}`)
 * renders as typed controls — a boolean is a checkbox, an enum a select, an
 * integer a number input — instead of a JSON textarea the operator has to
 * hand-edit. The backend validates the submitted object against the same
 * schema (SettingsValidator), so this form is a convenience, not the gate.
 * Keys the schema does not describe are kept verbatim and shown as JSON so
 * nothing is silently dropped; a schema without properties falls back to the
 * raw JSON editor.
 */
import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

export interface SchemaProperty {
  type?: string;
  default?: unknown;
  description?: string;
  enum?: Array<string | number>;
  minimum?: number;
  maximum?: number;
}

export interface SettingsSchema {
  type?: string;
  properties?: Record<string, SchemaProperty>;
  required?: string[];
}

export function schemaProperties(schema: unknown): Record<string, SchemaProperty> {
  const props = (schema as SettingsSchema | undefined)?.properties;
  return props && typeof props === "object" ? props : {};
}

/** Merge the stored settings over the schema defaults — what the form starts from. */
export function initialSettings(schema: unknown, stored: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, p] of Object.entries(schemaProperties(schema))) if (p.default !== undefined) out[k] = p.default;
  return { ...out, ...stored };
}

export function SettingsForm({ id, schema, value, onSave, saving }: {
  id: string;
  schema: unknown;
  value: Record<string, unknown>;
  onSave: (settings: Record<string, unknown>) => void;
  saving: boolean;
}) {
  const props = useMemo(() => schemaProperties(schema), [schema]);
  const keys = Object.keys(props);
  const [draft, setDraft] = useState<Record<string, unknown>>(() => initialSettings(schema, value));
  const extraKeys = Object.keys(draft).filter((k) => !props[k]);
  const [extraText, setExtraText] = useState(() => JSON.stringify(Object.fromEntries(extraKeys.map((k) => [k, draft[k]])), null, 2));
  const [extraError, setExtraError] = useState<string | null>(null);
  const set = (k: string, v: unknown) => setDraft((d) => ({ ...d, [k]: v }));

  if (keys.length === 0) {
    // No schema: the raw editor is the only honest control.
    return (
      <div className="space-y-2">
        <label className="text-xs text-muted-foreground" htmlFor={`settings-${id}`}>Settings (JSON — this plugin declares no schema)</label>
        <Textarea id={`settings-${id}`} className="font-mono text-xs min-h-[120px]" value={extraText} onChange={(e) => setExtraText(e.target.value)} />
        {extraError && <p className="text-xs text-destructive">{extraError}</p>}
        <Button size="sm" variant="accent" disabled={saving} onClick={() => {
          try { onSave(JSON.parse(extraText || "{}") as Record<string, unknown>); setExtraError(null); }
          catch { setExtraError("Not saved — the settings are not valid JSON."); }
        }}>Save settings</Button>
      </div>
    );
  }

  return (
    <form className="space-y-3" onSubmit={(e) => {
      e.preventDefault();
      let extra: Record<string, unknown> = {};
      if (extraKeys.length) {
        try { extra = JSON.parse(extraText || "{}") as Record<string, unknown>; setExtraError(null); }
        catch { setExtraError("Not saved — the additional settings are not valid JSON."); return; }
      }
      const typed: Record<string, unknown> = {};
      for (const k of keys) typed[k] = draft[k];
      onSave({ ...extra, ...typed });
    }} data-testid={`settings-form-${id}`}>
      {keys.map((k) => {
        const p = props[k];
        const fid = `settings-${id}-${k}`;
        const v = draft[k];
        let control: JSX.Element;
        if (p.type === "boolean") {
          control = <input id={fid} type="checkbox" checked={Boolean(v)} onChange={(e) => set(k, e.target.checked)} />;
        } else if (p.enum && p.enum.length) {
          control = (
            <select id={fid} className="h-9 rounded-md border border-input bg-background px-2 text-sm text-foreground"
                    value={String(v ?? "")} onChange={(e) => set(k, p.type === "integer" || p.type === "number" ? Number(e.target.value) : e.target.value)}>
              {p.enum.map((o) => <option key={String(o)} value={String(o)}>{String(o)}</option>)}
            </select>
          );
        } else if (p.type === "integer" || p.type === "number") {
          control = <Input id={fid} type="number" className="max-w-[160px]" value={v === undefined || v === null ? "" : String(v)}
                           min={p.minimum} max={p.maximum} step={p.type === "integer" ? 1 : "any"}
                           onChange={(e) => set(k, e.target.value === "" ? undefined : Number(e.target.value))} />;
        } else {
          control = <Input id={fid} value={v === undefined || v === null ? "" : String(v)} onChange={(e) => set(k, e.target.value)} />;
        }
        return (
          <div key={k} className="grid grid-cols-1 md:grid-cols-[220px_1fr] gap-x-4 gap-y-1 items-center">
            <label htmlFor={fid} className="text-sm font-medium font-mono">{k}</label>
            <div className="flex items-center gap-3">
              {control}
              {p.description && <span className="text-xs text-muted-foreground">{p.description}</span>}
            </div>
          </div>
        );
      })}
      {extraKeys.length > 0 && (
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground" htmlFor={`settings-${id}-extra`}>Additional settings the schema does not describe (JSON, kept verbatim)</label>
          <Textarea id={`settings-${id}-extra`} className="font-mono text-xs min-h-[80px]" value={extraText} onChange={(e) => setExtraText(e.target.value)} />
          {extraError && <p className="text-xs text-destructive">{extraError}</p>}
        </div>
      )}
      <Button type="submit" size="sm" variant="accent" disabled={saving}>Save settings</Button>
    </form>
  );
}
