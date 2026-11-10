/**
 * New project - a four-step dialog: template, name and location, features, review.
 * The review IS the server's plan (nothing is written until "Create"). Every decision lives in wizard.ts.
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/lib/auth";
import { createProject, listTemplates, planProject, type ProjectPlan, type TemplateInfo } from "@/lib/api/projects";
import { featureLabel } from "./encodings";
import { AccessBadge, ClassBadge } from "./badges";
import { ACCESS_META, CLASS_META, CLASS_ORDER, badgeLine, groupTemplates } from "./roles";
import { INITIAL, canAdvance, choosable, chooseTemplate, explain, featureNames, planRequest, templateById, toggleFeature,
  type WizardState } from "./wizard";

const TEMPLATES_KEY = ["projects", "templates"] as const;
const PROJECTS_KEY = ["projects", "list"] as const;

export function NewProjectDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();
  const [s, setS] = useState<WizardState>(INITIAL);
  const [plan, setPlan] = useState<ProjectPlan | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  const cat = useQuery({ queryKey: TEMPLATES_KEY, queryFn: ({ signal }) => listTemplates(signal), enabled: open });
  const tpl: TemplateInfo | undefined = useMemo(() => templateById(cat.data, s.templateId), [cat.data, s.templateId]);

  const planMut = useMutation({
    mutationFn: (req: ReturnType<typeof planRequest>) => planProject(req, csrf),
    onSuccess: (p) => { setPlan(p); setError(null); setS((x) => ({ ...x, step: 4 })); },
    onError: (e) => setError(explain(e, "wizard")),
  });
  // while a plan or a create is on its way the dialog stays open: its answer would otherwise land in a reset wizard
  const busy = () => planMut.isPending || createMut.isPending;
  const close = (o: boolean) => {
    if (!o && busy()) return;
    if (!o) { setS(INITIAL); setPlan(undefined); setError(null); }
    onOpenChange(o);
  };
  const createMut = useMutation({
    mutationFn: () => createProject(plan!.plan_id, csrf),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: PROJECTS_KEY });
      setS(INITIAL); setPlan(undefined); setError(null);
      onOpenChange(false);
    },
    onError: (e) => setError(explain(e, "wizard")),
  });

  const set = (patch: Partial<WizardState>) => { setError(null); setS((x) => ({ ...x, ...patch })); };
  const next = () => {
    if (s.step === 3) { setPlan(undefined); planMut.mutate(planRequest(s)); return; }
    if (canAdvance(s, plan)) setS((x) => ({ ...x, step: (x.step + 1) as WizardState["step"] }));
  };
  const back = () => { setError(null); setS((x) => ({ ...x, step: (x.step - 1) as WizardState["step"] })); };

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl" data-testid="new-project-wizard">
        <DialogHeader>
          <DialogTitle>New project</DialogTitle>
          <DialogDescription data-testid="wizard-step">Step {s.step} of 4</DialogDescription>
        </DialogHeader>

        {s.step === 1 && (
          <section className="space-y-3" aria-label="Choose a template">
            {cat.isLoading && <p className="text-sm text-muted-foreground">Loading templates…</p>}
            {cat.error && !cat.data && (
              <div className="space-y-2" data-testid="wizard-templates-error">
                <p role="alert" className="text-sm text-red-700 dark:text-red-300">{explain(cat.error, "wizard")}</p>
                <Button size="sm" variant="outline" onClick={() => void cat.refetch()} disabled={cat.isFetching}>Try again</Button>
              </div>
            )}
            {cat.data && !cat.data.available && (
              <p className="text-sm text-red-700 dark:text-red-300" data-testid="wizard-unavailable">{cat.data.reason}</p>
            )}
            {groupTemplates(cat.data?.templates ?? []).map((g) => (
              <div key={g.id} className="space-y-2" data-testid="template-group" data-family={g.id}>
                <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{g.label}</h3>
                <div className="grid gap-2 sm:grid-cols-3">
                  {g.items.map((t) => (
                    <button key={t.id} type="button" data-testid="template-option" data-template={t.id} data-role={t.role ?? ""}
                      aria-pressed={s.templateId === t.id}
                      onClick={() => set(chooseTemplate(s, cat.data, t.id))}
                      className={`flex flex-col gap-1 rounded-lg border p-3 text-left transition-colors ${s.templateId === t.id ? "border-primary bg-primary/5" : "border-border hover:border-primary/40"}`}>
                      <span className="font-medium">{t.title}</span>
                      <span className="text-sm text-muted-foreground" data-testid="template-outcome">{t.outcome || t.description}</span>
                      <span className="mt-1 flex flex-wrap gap-1">
                        <ClassBadge cls={t.classification ?? "internal"} />
                        <AccessBadge access={t.assistant_access ?? "read"} />
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </section>
        )}

        {s.step === 2 && (
          <section className="space-y-3" aria-label="Name and location">
            <label className="block space-y-1 text-sm">
              <span>Project name</span>
              <Input data-testid="wizard-title" value={s.title} maxLength={120} autoFocus
                onChange={(e) => set({ title: e.target.value })} placeholder="e.g. Plumbline explainer" />
            </label>
            <label className="block space-y-1 text-sm">
              <span>Folder (optional)</span>
              <Input data-testid="wizard-location" value={s.location} onChange={(e) => set({ location: e.target.value })}
                placeholder={cat.data?.projects_root ? `${cat.data.projects_root}${cat.data.projects_root.includes("\\") ? "\\" : "/"}<name>` : "inside your projects folder"} />
              <span className="block text-xs text-muted-foreground">Empty = a new folder named after the project, inside your projects folder.</span>
            </label>
          </section>
        )}

        {s.step === 3 && tpl && (
          <section className="space-y-3" aria-label="Features">
            <fieldset className="space-y-2 rounded-lg border p-3" data-testid="wizard-protection">
              <legend className="px-1 text-sm font-medium">Protection</legend>
              <p className="text-xs text-muted-foreground">The role proposes these; you decide. You can change them later in the project's settings.</p>
              <div className="grid gap-1 sm:grid-cols-2" role="radiogroup" aria-label="Data class">
                {CLASS_ORDER.map((c) => (
                  <label key={c} className="flex items-start gap-2 rounded border p-2 text-sm">
                    <input type="radio" name="wizard-class" className="mt-1" data-testid="wizard-class" data-class={c}
                      checked={s.classification === c} onChange={() => set({ classification: c })} />
                    <span><span className="block font-medium">{CLASS_META[c].label}</span><span className="block text-xs text-muted-foreground">{CLASS_META[c].hint}</span></span>
                  </label>
                ))}
              </div>
              <div className="flex flex-wrap gap-3" role="radiogroup" aria-label="What the assistant may do">
                {(["read", "write"] as const).map((a) => (
                  <label key={a} className="flex items-center gap-2 text-sm">
                    <input type="radio" name="wizard-access" data-testid="wizard-access" data-access={a}
                      checked={s.access === a} onChange={() => set({ access: a })} />
                    {ACCESS_META[a].label}
                  </label>
                ))}
              </div>
            </fieldset>
            <p className="text-sm text-muted-foreground">Switch features on or off. Each one can be changed later; switching off never deletes a file.</p>
            <ul className="space-y-2">
              {choosable(tpl).map((id) => {
                const on = s.features.includes(id);
                const f = cat.data?.features.find((x) => x.id === id);
                return (
                  <li key={id}>
                    <label className="flex items-start gap-3 rounded-lg border p-3">
                      <input type="checkbox" className="mt-1" data-testid="feature-toggle" data-feature={id}
                        checked={on} onChange={() => set(toggleFeature(s, cat.data, id))} />
                      <span className="min-w-0">
                        <span className="block font-medium">{featureLabel(id)}</span>
                        {f?.description && <span className="block text-xs text-muted-foreground">{f.description}</span>}
                        {f && f.requires.filter((r) => r !== "core").length > 0 && (
                          <span className="block text-xs text-muted-foreground" data-testid="feature-requires">
                            Needs: {f.requires.filter((r) => r !== "core").map(featureLabel).join(", ")}
                          </span>
                        )}
                        {f?.plugins.filter((p) => p.state !== "enabled").map((p) => (
                          <span key={p.plugin} className="block text-xs text-amber-700 dark:text-amber-300" data-testid="feature-plugin-missing">
                            Needs the {p.plugin.replace(/_/g, " ")} plugin, which is {p.state} for this tenant.
                          </span>
                        ))}
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" data-testid="docs-separate" checked={s.docsMode === "nested"}
                onChange={(e) => set({ docsMode: e.target.checked ? "nested" : "plain" })} />
              Keep the knowledge base in its own git repository (recommended)
            </label>
          </section>
        )}

        {s.step === 4 && plan && (
          <section className="space-y-3 text-sm" aria-label="Review" data-testid="wizard-review">
            <p>Nothing is written until you press Create.</p>
            <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
              <dt className="text-muted-foreground">Project</dt><dd className="break-words [overflow-wrap:anywhere]" data-testid="review-title">{plan.title} <span className="font-mono text-xs">({plan.project_id})</span></dd>
              <dt className="text-muted-foreground">Folder</dt><dd className="break-all font-mono text-xs" data-testid="review-location">{plan.location}</dd>
              <dt className="text-muted-foreground">Protection</dt><dd data-testid="review-protection">{badgeLine({ classification: plan.classification, assistant_access: plan.assistant_access })}</dd>
              <dt className="text-muted-foreground">Features</dt><dd data-testid="review-features">{featureNames(plan.features).join(", ") || "none"}</dd>
              <dt className="text-muted-foreground">Files</dt><dd data-testid="review-files">{plan.files.length} to write</dd>
              <dt className="text-muted-foreground">Git</dt><dd>{plan.docs_mode === "nested" ? "knowledge base in its own repository" : "one repository"}</dd>
            </dl>
            {plan.pulled.length > 0 && (
              <p data-testid="review-pulled" className="text-muted-foreground">
                Added because they are needed: {plan.pulled.map(featureLabel).join(", ")}.
              </p>
            )}
            {plan.plugins.filter((p) => p.state !== "enabled").map((p) => (
              <p key={p.plugin} className="text-red-700 dark:text-red-300" data-testid="review-plugin-missing">
                The {p.plugin.replace(/_/g, " ")} plugin is {p.state}. Enable it in the Marketplace first.
              </p>
            ))}
            {plan.conflicts.map((c) => (
              <p key={c} className="text-red-700 dark:text-red-300" data-testid="review-conflict">{c}</p>
            ))}
            {plan.warnings.map((w) => (
              <p key={w} className="text-amber-700 dark:text-amber-300" data-testid="review-warning">{w}</p>
            ))}
          </section>
        )}

        {error && <p role="alert" className="text-sm text-red-700 dark:text-red-300" data-testid="wizard-error">{error}</p>}

        <div className="flex items-center justify-between gap-2 pt-2">
          <Button variant="ghost" onClick={back} disabled={s.step === 1 || planMut.isPending || createMut.isPending}>Back</Button>
          {s.step < 4 && (
            <Button onClick={next} disabled={!canAdvance(s, plan) || planMut.isPending} data-testid="wizard-next">
              {s.step === 3 ? (planMut.isPending ? "Planning…" : "Review") : "Next"}
            </Button>
          )}
          {s.step === 4 && (
            <Button onClick={() => createMut.mutate()} disabled={!plan?.ready || createMut.isPending} data-testid="wizard-create">
              {createMut.isPending ? "Creating…" : "Create project"}
            </Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
