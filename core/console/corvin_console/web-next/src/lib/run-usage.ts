/**
 * ADR-2308: the per-node usage badge of a workflow run event. Kept out of the page so the
 * wording is unit-testable: the model is the one the CLI REPORTED (not the one requested), and
 * the token figure is the sum of input, output and cache tokens over every attempt that reached
 * the CLI (an attempt that timed out reported nothing).
 */
import type { WorkflowRunEvent } from "@/lib/api/workflows";

export function runUsageLabel(evt: Pick<WorkflowRunEvent, "model_id" | "tokens" | "attempts">): string {
  const parts: string[] = [];
  if (evt.model_id) parts.push(evt.model_id);
  if (evt.tokens) parts.push(`${evt.tokens.toLocaleString("en-US")} tok`);
  if (evt.attempts && evt.attempts > 1) parts.push(`${evt.attempts} attempts`);
  return parts.join(" · ");
}

export function runUsageTitle(evt: Pick<WorkflowRunEvent, "model_id" | "tokens" | "attempts">): string {
  const lines = [
    evt.model_id ? "Model reported by the CLI for this node" : "The CLI reported no model for this node",
  ];
  if (evt.tokens) lines.push("Tokens: input + output + cache read + cache write");
  if (evt.attempts && evt.attempts > 1) {
    lines.push(`${evt.attempts} attempts; the tokens are summed over the attempts that reached the CLI`);
  }
  return lines.join("\n");
}
