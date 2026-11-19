/**
 * Schema-driven settings form for DaddyReady. The plugin serves the option schemas (`/settings` -> `blocks`,
 * `source_types`); every field below is drawn from them, so a setting added to the plugin appears here without a console
 * change. Secrets are references only (`${ENV_VAR}`): the plugin refuses a literal, and this form says so.
 */
import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import type { BlockSchema, SchemaProp, SettingsDoc } from "@/lib/api/daddy-ready";
import { coerce, displayValue, getPath, humanizeKey, newSource, setPath, type Json } from "./model";

const TITLES: Record<string, { title: string; help: string }> = {
  episode: { title: "Episode", help: "How the daily brief is composed." },
  learning: { title: "Learning", help: "How far your feedback may move the topic and method weights." },
  interpreter: { title: "Note interpretation", help: "Optional labelling of your notes by a model. Off by default." },
  video: { title: "Video rendering", help: "Rendering through CorvinTV on this machine. Off by default." },
  delivery: { title: "Delivery", help: "Where the finished episode goes. Off by default." },
  father: { title: "Your own notes", help: "Which destinations your typed notes and feedback may reach." },
  panel: { title: "This panel", help: "How this page lists and plays things." },
};
const BLOCK_ORDER = ["episode", "learning", "interpreter", "video", "delivery", "father", "panel"];

const humanize = humanizeKey;

function Field({ name, prop, value, onChange, disabled }: {
  name: string; prop: SchemaProp; value: unknown; onChange: (v: unknown) => void; disabled?: boolean;
}) {
  const id = `dr-${name}`;
  const hint = [prop.description, prop.secret ? "A reference such as ${ENV_VAR}, never the secret itself." : "",
    prop.minimum !== undefined || prop.maximum !== undefined ? `Range ${prop.minimum ?? "…"} to ${prop.maximum ?? "…"}.` : "",
    prop.default !== undefined && prop.default !== "" ? `Default: ${displayValue(prop, prop.default) || "empty"}.` : ""]
    .filter(Boolean).join(" ");
  let control: React.ReactNode;
  if (prop.type === "boolean") {
    control = <Switch id={id} aria-label={humanize(name)} checked={Boolean(value ?? prop.default)} disabled={disabled}
      onCheckedChange={(v) => onChange(v)} />;
  } else if (prop.enum && prop.type === "string") {
    control = (
      <Select id={id} value={String(value ?? prop.default ?? "")} disabled={disabled}
        onChange={(e) => onChange(e.target.value)}>
        {prop.enum.map((o) => <option key={o} value={o}>{o}</option>)}
      </Select>
    );
  } else if (prop.type === "array" && prop.items?.enum) {
    const sel = new Set(((value ?? prop.default ?? []) as string[]));
    control = (
      <div className="flex flex-wrap gap-3" role="group" aria-label={humanize(name)}>
        {prop.items.enum.map((o) => (
          <label key={o} className="flex items-center gap-1.5 text-sm">
            <input type="checkbox" checked={sel.has(o)} disabled={disabled}
              onChange={(e) => { const n = new Set(sel); if (e.target.checked) n.add(o); else n.delete(o); onChange([...n]); }} />
            {o}
          </label>
        ))}
      </div>
    );
  } else {
    const numeric = prop.type === "integer" || prop.type === "number";
    control = (
      <Input id={id} type={numeric ? "number" : "text"} step={prop.type === "number" ? "any" : undefined}
        value={displayValue(prop, value)} disabled={disabled} min={prop.minimum} max={prop.maximum}
        placeholder={prop.default !== undefined && prop.default !== "" ? displayValue(prop, prop.default) : prop.secret ? "${ENV_VAR}" : ""}
        onChange={(e) => onChange(coerce(prop, e.target.value))} />
    );
  }
  return (
    <div className="grid gap-1 sm:grid-cols-[14rem_1fr] sm:items-start sm:gap-4">
      <label htmlFor={id} className="pt-2 text-sm font-medium">{humanize(name)}</label>
      <div className="min-w-0">
        {control}
        {hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}
      </div>
    </div>
  );
}

function Block({ schema, value, onChange, path, locked }: {
  schema: BlockSchema; value: Json; onChange: (p: string[], v: unknown) => void; path: string[]; locked: string[];
}) {
  return (
    <div className="space-y-3">
      {Object.entries(schema.properties ?? {}).map(([k, prop]) => {
        const full = [...path, k].join(".");
        return (
          <Field key={full} name={k} prop={prop} value={value?.[k]} disabled={locked.includes(full)}
            onChange={(v) => onChange([...path, k], v)} />
        );
      })}
    </div>
  );
}

function Section({ title, help, children, testid }: { title: string; help?: string; children: React.ReactNode; testid?: string }) {
  return (
    <Card data-testid={testid}>
      <CardContent className="space-y-4 p-5">
        <div>
          <h3 className="text-base font-semibold">{title}</h3>
          {help ? <p className="text-sm text-muted-foreground">{help}</p> : null}
        </div>
        {children}
      </CardContent>
    </Card>
  );
}

export function SettingsForm({ doc, draft, onChange }: { doc: SettingsDoc; draft: Json; onChange: (next: Json) => void }) {
  const set = (path: string[], v: unknown) => onChange(setPath(draft, path, v));
  const sources = (draft.sources as Json[]) ?? [];
  const taken = sources.map((s) => String(s.key));
  const approvals = (draft.approvals as Record<string, string>) ?? {};
  return (
    <div className="space-y-4" data-testid="dr-settings-form">
      <Section title="Child" help="Used for ages and for the name in the briefs." testid="dr-sec-child">
        <Field name="name" prop={{ type: "string" }} value={getPath(draft, ["child", "name"])} onChange={(v) => set(["child", "name"], v)} />
        <div className="grid gap-1 sm:grid-cols-[14rem_1fr] sm:items-start sm:gap-4">
          <label htmlFor="dr-birth" className="pt-2 text-sm font-medium">Birth date</label>
          <Input id="dr-birth" type="date" value={String(getPath(draft, ["child", "birth_date"]) ?? "")}
            onChange={(e) => set(["child", "birth_date"], e.target.value || undefined)} />
        </div>
      </Section>

      {BLOCK_ORDER.filter((b) => doc.blocks[b]).map((b) => (
        <Section key={b} title={TITLES[b]?.title ?? humanize(b)} help={TITLES[b]?.help} testid={`dr-sec-${b}`}>
          <Block schema={doc.blocks[b]} value={(draft[b] as Json) ?? {}} path={[b]} onChange={set} locked={doc.locked} />
        </Section>
      ))}

      <Section title="Approvals" help="A destination outside this machine is used only with a dated approval here AND the source allowing it. Leave a date empty to withhold the approval." testid="dr-sec-approvals">
        {doc.destinations.filter((d) => d !== "local").map((d) => (
          <div key={d} className="grid gap-1 sm:grid-cols-[14rem_1fr] sm:items-center sm:gap-4">
            <label htmlFor={`dr-appr-${d}`} className="text-sm font-medium">{d}</label>
            <Input id={`dr-appr-${d}`} type="date" value={String(approvals[d] ?? "")}
              onChange={(e) => set(["approvals", d], e.target.value || undefined)} />
          </div>
        ))}
      </Section>

      <Section title="Sources" help="Everything the plugin reads from. Each source has its own options and the destinations it may be sent to." testid="dr-sec-sources">
        {sources.map((s, i) => {
          const type = doc.source_types[String(s.type)];
          const opts = (s.options as Json) ?? {};
          const upd = (field: string, v: unknown) => {
            const next = sources.map((x, j) => (j === i ? (v === undefined ? Object.fromEntries(Object.entries(x).filter(([k]) => k !== field)) : { ...x, [field]: v }) : x));
            set(["sources"], next);
          };
          return (
            <div key={`${String(s.key)}-${i}`} className="space-y-3 rounded-md border p-4" data-testid={`dr-source-${String(s.key)}`}>
              <div className="flex items-center justify-between gap-2">
                <div>
                  <span className="font-mono text-sm">{String(s.key)}</span>
                  <span className="ml-2 text-xs text-muted-foreground">{type?.display_name ?? String(s.type)}</span>
                </div>
                <div className="flex items-center gap-3">
                  <label className="flex items-center gap-2 text-sm">Enabled
                    <Switch aria-label={`Enable source ${String(s.key)}`} checked={s.enabled !== false} onCheckedChange={(v) => upd("enabled", v)} />
                  </label>
                  <Button type="button" size="sm" variant="outline" aria-label={`Remove source ${String(s.key)}`}
                    onClick={() => set(["sources"], sources.filter((_, j) => j !== i))}><Trash2 className="h-4 w-4" /></Button>
                </div>
              </div>
              {type ? Object.entries(type.schema.properties ?? {}).map(([k, prop]) => (
                <Field key={k} name={k} prop={prop} value={opts[k]} onChange={(v) => {
                  const next: Json = { ...opts };
                  if (v === undefined) delete next[k];
                  else next[k] = v;
                  upd("options", next);
                }} />
              )) : <p className="text-sm text-destructive">Unknown source type {String(s.type)}.</p>}
              <Field name="since" prop={{ type: "string", description: "ISO date, or @birth for the birth date. Empty = from the start." }}
                value={s.since} onChange={(v) => upd("since", v)} />
              <Field name="until" prop={{ type: "string", description: "ISO date. Empty = open end." }} value={s.until} onChange={(v) => upd("until", v)} />
              <Field name="allowed_destinations" prop={{ type: "array", items: { type: "string", enum: doc.destinations }, default: ["local"] }}
                value={s.allowed_destinations} onChange={(v) => upd("allowed_destinations", v)} />
            </div>
          );
        })}
        <div className="flex flex-wrap items-center gap-2">
          <Select aria-label="New source type" id="dr-new-source" className="max-w-xs" defaultValue="" placeholder="Add a source…"
            onChange={(e) => { if (e.target.value) { set(["sources"], [...sources, newSource(e.target.value, taken)]); e.target.value = ""; } }}>
            {Object.entries(doc.source_types).map(([id, t]) => <option key={id} value={id}>{t.display_name}</option>)}
          </Select>
          <Plus className="h-4 w-4 text-muted-foreground" aria-hidden />
        </div>
      </Section>

      <Section title="Storage" help="Where the child's data lives on this machine. Never inside a git repository." testid="dr-sec-storage">
        <Field name="data_dir" prop={{ type: "string", description: `Empty = ${doc.defaults.data_dir}` }}
          value={draft.data_dir} onChange={(v) => set(["data_dir"], v)} />
        <Field name="methods_dirs" prop={{ type: "array", items: { type: "string" }, description: "Extra folders of method files, comma separated." }}
          value={draft.methods_dirs} onChange={(v) => set(["methods_dirs"], v)} />
      </Section>

      <Section title="Locked settings" help="These start programs or load code, so they are changed in the config file only." testid="dr-sec-locked">
        {doc.locked.map((l) => (
          <div key={l} className="grid gap-1 sm:grid-cols-[14rem_1fr] sm:gap-4">
            <span className="text-sm font-medium">{l}</span>
            <code className="break-all rounded bg-muted px-2 py-1 text-xs">{JSON.stringify(getPath(draft, l.split(".")) ?? null)}</code>
          </div>
        ))}
      </Section>
    </div>
  );
}
