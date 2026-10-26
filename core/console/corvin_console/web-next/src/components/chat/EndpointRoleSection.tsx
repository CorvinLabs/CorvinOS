/**
 * Organisation and GDPR role of one outbound endpoint: shown on the peer row, editable by the operator.
 *
 * The role is the operator's own statement. `unset` (the default) is shown as a pending decision with the
 * consequence spelled out; nothing is inferred from the partner. Saving is a CSRF PUT; a failure shows fixed
 * copy (see lib/endpoint-role.ts), never the server's sentence.
 */
import * as React from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { putA2AEndpointGdprRole } from "@/lib/api/a2a";
import {
  GDPR_ROLES, ORGANISATION_MAX, ROLE_HELP, ROLE_LABELS, ROLE_UNSET_NOTICE, normaliseRole,
  organisationProblem, saveErrorCopy, type GdprRole,
} from "@/lib/endpoint-role";

export function EndpointRoleSection({
  endpointId, organisation, gdprRole, csrf, onSaved,
}: {
  endpointId: string;
  organisation?: string;
  gdprRole?: string;
  csrf: string;
  onSaved?: (v: { organisation: string; gdpr_role: GdprRole }) => void;
}) {
  const [org, setOrg] = React.useState(organisation ?? "");
  const [role, setRole] = React.useState<GdprRole>(normaliseRole(gdprRole));
  const [editing, setEditing] = React.useState(false);
  const [orgInput, setOrgInput] = React.useState(organisation ?? "");
  const [roleInput, setRoleInput] = React.useState<GdprRole>(normaliseRole(gdprRole));
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState("");

  const idBase = `role-${endpointId}`;

  function startEdit() {
    setOrgInput(org);
    setRoleInput(role);
    setError("");
    setEditing(true);
  }

  async function save() {
    const problem = organisationProblem(orgInput);
    if (problem) {
      setError(problem);
      return;
    }
    setSaving(true);
    setError("");
    const body = { organisation: orgInput.trim(), gdpr_role: roleInput };
    try {
      const res = await putA2AEndpointGdprRole(endpointId, body, csrf);
      setOrg(res.organisation);
      setRole(normaliseRole(res.gdpr_role));
      setEditing(false);
      onSaved?.({ organisation: res.organisation, gdpr_role: normaliseRole(res.gdpr_role) });
    } catch (err: unknown) {
      setError(saveErrorCopy(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-2 border-t border-border/50 pt-2" data-testid="endpoint-role">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">Role</span>
        {org && <span className="text-xs font-medium text-foreground">{org}</span>}
        <Badge variant="outline" className="text-[10px]">{ROLE_LABELS[role]}</Badge>
        {!editing && (
          <Button size="sm" variant="ghost" className="ml-auto h-6 px-2 text-[10px]" onClick={startEdit}>
            {role === "unset" ? "Set role" : "Edit role"}
          </Button>
        )}
      </div>

      {role === "unset" && (
        <p className="flex items-start gap-1.5 rounded bg-amber-500/10 px-2 py-1 text-[11px] text-amber-700 dark:text-amber-400">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
          <span>{ROLE_UNSET_NOTICE}</span>
        </p>
      )}

      {editing && (
        <div className="rounded-md border border-border bg-muted/30 p-3 space-y-2.5">
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <div className="space-y-1">
              <label htmlFor={`${idBase}-org`} className="text-xs text-muted-foreground">Organisation</label>
              <Input
                id={`${idBase}-org`}
                value={orgInput}
                maxLength={ORGANISATION_MAX + 20}
                onChange={(e) => setOrgInput(e.target.value)}
                placeholder="e.g. Acme Analytics GmbH"
                className="h-8 text-xs"
              />
            </div>
            <div className="space-y-1">
              <label htmlFor={`${idBase}-role`} className="text-xs text-muted-foreground">GDPR role</label>
              <select
                id={`${idBase}-role`}
                value={roleInput}
                onChange={(e) => setRoleInput(normaliseRole(e.target.value))}
                className="h-8 w-full rounded-md border border-input bg-background px-2 text-xs"
              >
                {GDPR_ROLES.map((r) => (
                  <option key={r} value={r}>{ROLE_LABELS[r]}</option>
                ))}
              </select>
            </div>
          </div>
          <p className="text-[11px] text-muted-foreground">{ROLE_HELP}</p>
          {error && <p className="rounded bg-destructive/10 px-2 py-1 text-xs text-destructive">{error}</p>}
          <div className="flex gap-2">
            <Button size="sm" disabled={saving} className="h-7 px-3 text-xs" onClick={() => void save()}>
              {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : "Save role"}
            </Button>
            <Button size="sm" variant="ghost" className="h-7 px-3 text-xs" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
