/**
 * Outcome of a chat turn, as the server tags its closing `result` event
 * (ADR-2236 P1, `chat_runtime.stream_turn`; T-0250 made it rendered).
 *
 *   ok           — a clean finish; nothing to show
 *   cut          — the server ended the turn on purpose at a scope limit (child cap /
 *                  wake-up cap); the text says what was cut
 *   process_died — the claude process exited with a background task still open, so the
 *                  text is a failure notice, not an answer
 *
 * The mapping to English lives here, in a testable module, so the chat page renders a
 * banner from a closed enum and never from an interpolated server string.
 */
export type TurnOutcome = "ok" | "cut" | "process_died";

const OUTCOMES: readonly TurnOutcome[] = ["ok", "cut", "process_died"];

/** The outcome on a stream event, or null for anything outside the closed enum. */
export function parseTurnOutcome(value: unknown): TurnOutcome | null {
  return typeof value === "string" && (OUTCOMES as readonly string[]).includes(value)
    ? (value as TurnOutcome)
    : null;
}

/** Banner text for an interrupted / failed turn; null when there is nothing to say. */
export function turnOutcomeText(outcome: TurnOutcome | null | undefined): string | null {
  switch (outcome) {
    case "process_died":
      return "The worker process died while a background task was still running — the text above is its failure notice, not an answer.";
    case "cut":
      return "The turn was cut at the scope limit — its remaining background tasks were stopped; the text above says what was cut.";
    default:
      return null;
  }
}

/** Whether the outcome is a failure (styled as an error) rather than a notice. */
export function turnOutcomeIsFailure(outcome: TurnOutcome | null | undefined): boolean {
  return outcome === "process_died";
}
