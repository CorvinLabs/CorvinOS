/**
 * Template composer — the structured half of the unified Skill Forge tab.
 *
 * This is what /app/skill-forge-generator was (2026-09-20 merge): name,
 * title, description, a skill-type template, and a manifest you could copy
 * or download. That page POSTed to `/v1/skill-forge/generate`, a FLASK
 * blueprint (`routes/skill_forge_api.py`) that a FastAPI app never mounted
 * and nothing ever imported — the endpoint answered 404 on the live host,
 * so every "Generate Skill" click ended in `API error: 404`. The form was
 * real; its backend was not.
 *
 * So the fields moved here and were wired to the route that does exist:
 * `POST /v1/console/skills/manual`, which writes THROUGH the canonical
 * SkillForge registry (linter, content-hash, hash-chained `skill.create`
 * audit, plugin-slot mirror) — the same registry the orchestrated tab's
 * library lists, so a skill created here appears in that list immediately.
 *
 * Two fields of the old form deliberately did NOT survive, because the
 * route has no argument for either and rendering a control the server
 * ignores is fabricated UI (CLAUDE.md § Console is a Production Surface):
 *
 *   scope    — every manual skill is written at scope "user" (MANUAL_SCOPE
 *              in routes/skills_manual.py). Scope is changed afterwards by
 *              promotion, in Forge's Skills tab, under real grade gates.
 *   use_llm  — the LLM path IS the orchestrated mode next to this one.
 *
 * The templates are ports of `core/skill_forge/generators/skeleton.py`'s
 * SKELETON_TEMPLATES. They are prefill for an editable textarea, not a
 * server contract: the operator sees and edits the exact Markdown that
 * gets stored, which is why duplicating the strings here is honest rather
 * than a second source of truth.
 */

import React, { useMemo, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Copy, Download, FileText, Loader2, RotateCcw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Link } from 'react-router-dom';
import { createManualSkill } from '@/lib/api';
import { useAuth } from '@/lib/auth';

/** Layer 9 namespace gate: the console mints as the `assistant` persona, so
 *  a manual skill MUST be named `assistant.<name>` or the registry refuses it
 *  with 422 (routes/skills_manual.py § Namespace contract). The prefix is
 *  shown as a fixed adornment instead of being left for the operator to
 *  guess — the old form's `my_skill (snake_case)` placeholder would have
 *  traded the generator's 404 for a 422. */
const NAMESPACE = 'assistant.';

/** Registry name shape: `[a-z0-9][a-z0-9_.]*`, no `-`. Validated here so the
 *  operator is told before the round-trip, and again by the route. */
const LOCAL_NAME_RE = /^[a-z0-9][a-z0-9_.]*$/;

export type TemplateId = 'learned-experience' | 'reasoning' | 'reference' | 'automation' | 'blank';

const TEMPLATE_LABELS: Record<TemplateId, string> = {
  'learned-experience': 'Learned Experience',
  reasoning: 'Reasoning',
  reference: 'Reference',
  automation: 'Automation',
  blank: 'Blank (write it yourself)',
};

function renderTemplate(id: TemplateId, title: string, description: string): string {
  const t = title.trim() || 'Untitled Skill';
  const d = description.trim() || '[What this skill is for]';
  const stamp = new Date().toISOString();

  switch (id) {
    case 'reasoning':
      return `# ${t}

**Type:** Reasoning Skill
**Status:** DRAFT (created ${stamp})

## Context

${d}

## The Thesis

[Initial proposal or framing]

## The Antithesis

[Counter-arguments and challenges]

## The Synthesis

[Reconciled position]

## How to Apply

[Step-by-step application]
`;
    case 'reference':
      return `# ${t}

**Type:** Reference
**Status:** DRAFT (created ${stamp})

## Overview

${d}

## API / Interface

| Field | Type | Required | Description |
|---|---|---|---|
| | | | |

## See Also

[Related resources]
`;
    case 'automation':
      return `# ${t}

**Type:** Automation Skill
**Status:** DRAFT (created ${stamp})

## Context

${d}

## Algorithm

[High-level algorithm or decision tree]

## Input Contract

[What this skill expects as input]

## Output Contract

[What this skill produces]

## Error Handling

[Failure modes and recovery]
`;
    case 'blank':
      return `# ${t}

${d}
`;
    case 'learned-experience':
    default:
      return `# ${t}

**Type:** Learned Experience
**Status:** DRAFT (created ${stamp})

## Context

${d}

## The Pattern

[How-to section — describe the working method]

## When to Use / When NOT to Use

[Boundary section — when this pattern applies and when it doesn't]

## Examples

[Concrete examples and case studies]
`;
  }
}

interface TemplateSkillFormProps {
  /** Fired after a successful create, with the fully-qualified skill name.
   *  The parent refreshes its library — the new skill is in the same
   *  registry the orchestrated mode lists. */
  onCreated: (name: string) => void;
  /** True while an orchestrated run is in flight; both composers write to the
   *  same registry, so this one is held back rather than racing it. */
  disabled?: boolean;
}

export default function TemplateSkillForm({ onCreated, disabled = false }: TemplateSkillFormProps) {
  const { session } = useAuth();
  const qc = useQueryClient();

  const [localName, setLocalName] = useState('');
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [template, setTemplate] = useState<TemplateId>('learned-experience');
  const [body, setBody] = useState('');
  const [bodyTouched, setBodyTouched] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fullName = NAMESPACE + localName.trim();

  // The body follows the template/title/description until the operator edits
  // it; from then on it is theirs and a field change must not overwrite it.
  const effectiveBody = useMemo(
    () => (bodyTouched ? body : renderTemplate(template, title, description)),
    [bodyTouched, body, template, title, description],
  );

  const create = useMutation({
    mutationFn: async () => createManualSkill(fullName, effectiveBody, session!.csrf_token),
    onSuccess: async () => {
      setError(null);
      await qc.invalidateQueries({ queryKey: ['skill-creator', 'skills'] });
      await qc.invalidateQueries({ queryKey: ['skills'] });
      onCreated(fullName);
      setLocalName('');
      setTitle('');
      setDescription('');
      setBody('');
      setBodyTouched(false);
    },
    onError: (e: Error) => setError(e.message),
  });

  const nameProblem = !localName.trim()
    ? 'Name is required.'
    : !LOCAL_NAME_RE.test(localName.trim())
      ? 'Lowercase letters, digits, "_" and "." only — no "-", and it must start with a letter or digit.'
      : null;

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (nameProblem) {
      setError(nameProblem);
      return;
    }
    if (!effectiveBody.trim()) {
      setError('Skill body is required.');
      return;
    }
    create.mutate();
  }

  const busy = disabled || create.isPending;
  const licenseDenied = !!error && error.includes('license_required');

  return (
    <form onSubmit={submit} className="space-y-3" data-testid="template-skill-form">
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="tpl-name" className="text-sm font-medium">
            Skill name
          </Label>
          <div className="flex items-center rounded-md border border-input bg-background focus-within:ring-2 focus-within:ring-ring">
            <span className="shrink-0 pl-3 font-mono text-xs text-muted-foreground">
              {NAMESPACE}
            </span>
            <Input
              id="tpl-name"
              data-testid="tpl-name"
              value={localName}
              onChange={(e) => setLocalName(e.target.value)}
              placeholder="json_validator"
              disabled={busy}
              className="border-0 font-mono text-xs shadow-none focus-visible:ring-0"
            />
          </div>
          <p className="text-[10px] text-muted-foreground">
            The console mints as the <code className="font-mono">assistant</code> persona, so
            every skill it creates lives in that namespace.
          </p>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="tpl-title" className="text-sm font-medium">
            Title
          </Label>
          <Input
            id="tpl-title"
            data-testid="tpl-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="JSON Validator"
            disabled={busy}
            className="text-xs"
          />
          <p className="text-[10px] text-muted-foreground">
            Heading of the generated body. Optional.
          </p>
        </div>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="tpl-description" className="text-sm font-medium">
          Description
        </Label>
        <Textarea
          id="tpl-description"
          data-testid="tpl-description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="What does this skill do?"
          rows={2}
          disabled={busy}
          className="text-xs"
        />
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="tpl-template" className="text-sm font-medium">
          Template
        </Label>
        <select
          id="tpl-template"
          data-testid="tpl-template"
          value={template}
          onChange={(e) => {
            setTemplate(e.target.value as TemplateId);
            // A template change is an explicit request for that scaffold, so
            // it reclaims the body even after manual edits.
            setBodyTouched(false);
          }}
          disabled={busy}
          className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm"
        >
          {(Object.keys(TEMPLATE_LABELS) as TemplateId[]).map((id) => (
            <option key={id} value={id}>
              {TEMPLATE_LABELS[id]}
            </option>
          ))}
        </select>
        <p className="text-[10px] text-muted-foreground">
          Prefills the body below. Everything you see there is what gets stored — edit it freely.
        </p>
      </div>

      <div className="space-y-1.5">
        <div className="flex items-center justify-between gap-2">
          <Label htmlFor="tpl-body" className="flex items-center gap-1.5 text-sm font-medium">
            <FileText className="h-3.5 w-3.5" />
            Skill body (Markdown)
          </Label>
          <div className="flex items-center gap-1">
            {bodyTouched && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-6 px-2 text-[10px]"
                onClick={() => {
                  setBodyTouched(false);
                  setBody('');
                }}
                disabled={busy}
              >
                <RotateCcw className="mr-1 h-3 w-3" />
                Reset to template
              </Button>
            )}
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="h-6 px-2 text-[10px]"
              onClick={() => void navigator.clipboard?.writeText(effectiveBody)}
              disabled={busy}
              data-testid="tpl-copy"
            >
              <Copy className="mr-1 h-3 w-3" />
              Copy
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="h-6 px-2 text-[10px]"
              onClick={() => {
                const blob = new Blob([effectiveBody], { type: 'text/markdown' });
                const url = URL.createObjectURL(blob);
                const link = document.createElement('a');
                link.href = url;
                link.download = `${localName.trim() || 'skill'}.md`;
                link.click();
                URL.revokeObjectURL(url);
              }}
              disabled={busy}
              data-testid="tpl-download"
            >
              <Download className="mr-1 h-3 w-3" />
              Download
            </Button>
          </div>
        </div>
        <Textarea
          id="tpl-body"
          data-testid="tpl-body"
          value={effectiveBody}
          onChange={(e) => {
            setBodyTouched(true);
            setBody(e.target.value);
          }}
          rows={12}
          disabled={busy}
          className="font-mono text-xs"
        />
        <p className="text-[10px] text-muted-foreground">
          The registry linter rejects bodies over 8 KB, embedded secrets and prompt-injection
          patterns — a rejection comes back here with the exact reason.
        </p>
      </div>

      {error &&
        (licenseDenied ? (
          /* Creating a skill is gated on the `forge.create` capability, and the
             registry refuses it for a free tier BEFORE anything is written
             (skill_forge/registry.py raises `license_required`, which arrives
             here as a 400). The raw capability string is accurate but reads
             like a crash; the operator's actual next step is the License
             panel. Same gate covers the orchestrated composer next to this
             one — it is not a limitation of the template path. */
          <div
            className="space-y-2 rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-xs"
            data-testid="tpl-license-denied"
          >
            <p className="font-medium">Creating skills is not available on this licence tier.</p>
            <p className="text-muted-foreground">
              Nothing was written. The same gate applies to the orchestrated composer.
            </p>
            <Link to="/app/license" className="inline-block underline underline-offset-2">
              Open the License panel
            </Link>
            <details className="text-muted-foreground">
              <summary className="cursor-pointer">Server response</summary>
              <pre className="mt-1 whitespace-pre-wrap break-all font-mono text-[10px]">
                {error}
              </pre>
            </details>
          </div>
        ) : (
          <div
            className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive"
            data-testid="tpl-error"
          >
            {error}
          </div>
        ))}

      <Button
        type="submit"
        className="w-full"
        disabled={busy || !localName.trim()}
        data-testid="tpl-submit"
      >
        {create.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
        {create.isPending ? 'Creating…' : 'Create skill'}
      </Button>
      <p className="text-[10px] text-muted-foreground">
        Written straight to the registry — no engine run, no subscription cost. It starts{' '}
        <strong>inert</strong>: a skill with no grade sits below the injection gate and is never
        injected into a turn.
      </p>
    </form>
  );
}
