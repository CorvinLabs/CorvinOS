/**
 * JsonSchemaForm — render a settings form from a plugin's JSON Schema.
 *
 * ADR-0233 Phase 4. The point is that no plugin ships UI code: the Console
 * derives the form from the schema the plugin declares, and the backend
 * re-validates against the same schema before persisting. Client-side
 * validation here is a convenience, never the gate.
 *
 * Supported shapes (draft-07 subset): string (text / enum select / textarea),
 * integer + number (number input, slider when both bounds are given), boolean
 * (checkbox), and one level of nested object. Anything else falls back to a
 * read-only JSON view rather than silently dropping the field — a setting the
 * form cannot render must still be visible.
 */
import { useMemo } from "react";

export interface JsonSchemaFormProps {
  schema: Record<string, unknown>;
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
  disabled?: boolean;
  /** Per-key validation messages from the server (422 detail). */
  errors?: Record<string, string>;
}

interface PropSpec {
  type?: string;
  title?: string;
  description?: string;
  enum?: unknown[];
  default?: unknown;
  minimum?: number;
  maximum?: number;
  properties?: Record<string, unknown>;
  ui?: { component?: string; monospace?: boolean; warning?: string };
}

function labelFor(key: string, spec: PropSpec): string {
  if (spec.title) return spec.title;
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function JsonSchemaForm({
  schema,
  values,
  onChange,
  disabled = false,
  errors = {},
}: JsonSchemaFormProps) {
  const properties = useMemo(
    () => (schema?.properties as Record<string, PropSpec> | undefined) ?? {},
    [schema],
  );
  const required = useMemo(
    () => new Set(((schema?.required as string[] | undefined) ?? [])),
    [schema],
  );

  const entries = Object.entries(properties);
  if (entries.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        This plugin declares no settings.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      {entries.map(([key, spec]) => (
        <Field
          key={key}
          name={key}
          spec={spec}
          value={values[key]}
          required={required.has(key)}
          disabled={disabled}
          error={errors[key]}
          onChange={onChange}
        />
      ))}
    </div>
  );
}

interface FieldProps {
  name: string;
  spec: PropSpec;
  value: unknown;
  required: boolean;
  disabled: boolean;
  error?: string;
  onChange: (key: string, value: unknown) => void;
}

function Field({ name, spec, value, required, disabled, error, onChange }: FieldProps) {
  const label = labelFor(name, spec);
  const id = `plugin-setting-${name}`;
  const current = value ?? spec.default ?? "";

  const describe = (
    <>
      {spec.description && (
        <p className="text-xs text-muted-foreground">{spec.description}</p>
      )}
      {spec.ui?.warning && (
        <p className="text-xs text-amber-600 dark:text-amber-500">{spec.ui.warning}</p>
      )}
      {error && <p className="text-xs text-red-600 dark:text-red-400">{error}</p>}
    </>
  );

  // Enum → select
  if (Array.isArray(spec.enum)) {
    return (
      <div className="space-y-1">
        <label htmlFor={id} className="text-sm font-medium">
          {label}
          {required && <span className="text-red-500"> *</span>}
        </label>
        <select
          id={id}
          className="w-full rounded border bg-background px-2 py-1 text-sm"
          value={String(current)}
          disabled={disabled}
          onChange={(e) => onChange(name, e.target.value)}
        >
          {spec.enum.map((opt) => (
            <option key={String(opt)} value={String(opt)}>
              {String(opt)}
            </option>
          ))}
        </select>
        {describe}
      </div>
    );
  }

  if (spec.type === "boolean") {
    return (
      <div className="space-y-1">
        <label htmlFor={id} className="flex items-center gap-2 text-sm font-medium">
          <input
            id={id}
            type="checkbox"
            checked={Boolean(value ?? spec.default ?? false)}
            disabled={disabled}
            onChange={(e) => onChange(name, e.target.checked)}
          />
          {label}
        </label>
        {describe}
      </div>
    );
  }

  if (spec.type === "integer" || spec.type === "number") {
    const bounded = typeof spec.minimum === "number" && typeof spec.maximum === "number";
    const step = spec.type === "integer" ? 1 : "any";
    return (
      <div className="space-y-1">
        <label htmlFor={id} className="text-sm font-medium">
          {label}
          {required && <span className="text-red-500"> *</span>}
          {bounded && (
            <span className="ml-2 text-xs text-muted-foreground">
              {String(current)} ({spec.minimum}–{spec.maximum})
            </span>
          )}
        </label>
        <input
          id={id}
          type={bounded ? "range" : "number"}
          className="w-full rounded border bg-background px-2 py-1 text-sm"
          value={Number(current) || spec.minimum || 0}
          min={spec.minimum}
          max={spec.maximum}
          step={step}
          disabled={disabled}
          onChange={(e) => {
            const raw = e.target.value;
            const parsed = spec.type === "integer" ? parseInt(raw, 10) : parseFloat(raw);
            onChange(name, Number.isNaN(parsed) ? raw : parsed);
          }}
        />
        {describe}
      </div>
    );
  }

  if (spec.type === "string") {
    const multiline = spec.ui?.component === "textarea";
    return (
      <div className="space-y-1">
        <label htmlFor={id} className="text-sm font-medium">
          {label}
          {required && <span className="text-red-500"> *</span>}
        </label>
        {multiline ? (
          <textarea
            id={id}
            rows={4}
            className={`w-full rounded border bg-background px-2 py-1 text-sm ${
              spec.ui?.monospace ? "font-mono" : ""
            }`}
            value={String(current)}
            disabled={disabled}
            onChange={(e) => onChange(name, e.target.value)}
          />
        ) : (
          <input
            id={id}
            type="text"
            className="w-full rounded border bg-background px-2 py-1 text-sm"
            value={String(current)}
            disabled={disabled}
            onChange={(e) => onChange(name, e.target.value)}
          />
        )}
        {describe}
      </div>
    );
  }

  if (spec.type === "object" && spec.properties) {
    const nested = (value as Record<string, unknown>) ?? {};
    return (
      <fieldset className="space-y-3 rounded border p-3">
        <legend className="px-1 text-sm font-medium">{label}</legend>
        {describe}
        <JsonSchemaForm
          schema={{ properties: spec.properties }}
          values={nested}
          disabled={disabled}
          onChange={(childKey, childValue) =>
            onChange(name, { ...nested, [childKey]: childValue })
          }
        />
      </fieldset>
    );
  }

  // Unrenderable shape: show it rather than hide it.
  return (
    <div className="space-y-1">
      <label className="text-sm font-medium">{label}</label>
      <pre className="overflow-x-auto rounded border bg-muted p-2 text-xs">
        {JSON.stringify(value ?? spec.default ?? null, null, 2)}
      </pre>
      <p className="text-xs text-muted-foreground">
        This setting has a shape the form cannot edit ({spec.type ?? "unknown type"}).
        Edit it in the tenant registry.
      </p>
      {describe}
    </div>
  );
}
