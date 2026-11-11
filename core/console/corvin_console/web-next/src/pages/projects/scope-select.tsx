/**
 * The knowledge-base switcher of the Tasks board and the Knowledge Graph: one component, one set of labels, the
 * choice in ?scope= (written by its owner). Every project is listed; one that cannot be shown is disabled with
 * its reason, and the line under the select links to where it is fixed.
 */
import { Link } from "react-router-dom";
import type { ProjectSummary } from "@/lib/api/projects";
import { scopeOptions, type ScopeOpts } from "./scope";

/** Deploy marker of the switcher (a string literal). */
export const MARKER_SCOPE_SWITCH = "Not available here:";

export function ScopeSelect({ value, projects, onChange, note, label = "Knowledge base", opts }: {
  value: string;
  projects: ProjectSummary[] | undefined;
  onChange: (v: string) => void;
  /** Why the URL's choice was not honoured (unknown or unavailable project). */
  note?: string | null;
  label?: string;
  /** The graph page names its own repository when it is not Corvin's (scope.ts `boundRepoLabel`). */
  opts?: ScopeOpts;
}) {
  const options = scopeOptions(projects, opts);
  const blocked = options.filter((o) => o.disabled && o.projectId);
  return (
    <div className="flex flex-col gap-1 min-w-0">
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        {label}
        <select data-testid="scope-select" aria-label={label} value={value} onChange={(e) => onChange(e.target.value)}
          className="h-9 max-w-full rounded-md border border-input bg-background px-2 text-sm text-foreground">
          {options.map((o) => <option key={o.value} value={o.value} disabled={o.disabled} title={o.reason ?? undefined}>{o.label}</option>)}
        </select>
      </label>
      {note && <p role="status" data-testid="scope-note" className="text-xs text-amber-700 dark:text-amber-400">{note}</p>}
      {blocked.length > 0 && (
        <p data-testid="scope-unavailable" className="text-xs text-muted-foreground">
          {MARKER_SCOPE_SWITCH}{" "}
          {blocked.slice(0, 3).map((o, i) => (
            <span key={o.value}>
              {i > 0 && " · "}
              <Link to={o.fixHref ?? "/app/projects"} className="underline underline-offset-2 hover:text-foreground">{o.title}</Link>
              {" "}({o.reason})
            </span>
          ))}
          {blocked.length > 3 && <> · +{blocked.length - 3} more on the Projects page</>}
        </p>
      )}
    </div>
  );
}
