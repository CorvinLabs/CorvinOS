# Chat cost and token-efficiency dashboard (ADR-2324)

A collapsed strip above the message list of every console chat ("Cost & efficiency").
Opening it loads `GET /v1/console/chat/sessions/{sid}/efficiency`; a closed panel makes no request.
It reloads when a reply finishes streaming.

## Where the numbers come from

Nothing is stored. `corvin_console/chat_efficiency.py::compute_chat_efficiency` derives every figure on
each request from the chat's own turn log (`<session>.turns.jsonl`):

| Input | Written by | Used for |
|---|---|---|
| `turn_usage.model` + four token counts | `chat_runtime` (ADR-2263), from the CLI `init` / `result` events | tokens, cost, cache |
| `diff` / `diff_truncated` / `diff_withheld` on tool parts | `chat_runtime._tool_result_diff` (ADR-2241) | changed lines |

Prices come from `core.learning.model_selection_learner` (`model_price_per_1k`, `_turn_cost_usd`): the
same rate card the Models page and the savings meter use, so figures agree across panels. Cache writes are
priced at 1.25x the input rate, cache reads at the card's cache-read rate.

## Metric definitions

Every ratio divides figures from the **same set of turns**.

| Metric | Formula | Turns covered |
|---|---|---|
| Total cost | sum of per-turn cost | priced turns (model in the rate card, all four token counts reported) |
| Cost per turn | total cost / priced turns | priced |
| Cost per 1k output tokens | total cost / (output tokens / 1000) | priced |
| Cache hit rate | cache read / (input + cache read + cache write) | turns with complete usage |
| Cache saving | cost if every input-side token were billed at the plain input rate, minus actual cost | priced |
| Changed lines | `+` and `-` lines of the CLI diffs (hunk headers excluded) | turns with a fully known diff |
| Output tokens per changed line | output tokens / changed lines | turns with a fully known diff (turns without edits included) |
| Changed lines per 1k output tokens | changed lines / (output tokens / 1000) | same |
| Cost per changed line | cost / changed lines | priced turns with a fully known diff |

## Rules the numbers obey

- **Unknown is not zero.** A count the CLI did not report is `null` and leaves the turn out of every sum;
  the UI shows an em dash. A zero denominator also yields `null`.
- **Unpriced models** (not in the rate card, e.g. a brand-new model) are counted in tokens, listed under
  `unpriced_models`, and excluded from every dollar figure. They are never priced at a guess.
- **Changed lines are what the CLI diff reported.** A truncated diff (200-line bound) makes the figure a lower
  bound (`>=` in the UI); a diff withheld by the credential gate removes the turn from line figures; a Bash
  command that changes files without naming them is not seen.
- **Not included:** delegated worker runs (ACS) - they are not in the turn log (`includes_delegated_workers: false`).
- **Window:** the whole chat (`window.scope = "chat_lifetime"`). The usage epoch (ADR-0760) is the Models
  page's view and does not apply here.
- `coverage` in the payload carries the turn counts behind each exclusion; the UI turns them into notes.

## Code map

- Backend: `corvin_console/chat_efficiency.py`, route `get_chat_efficiency` in `routes/chat.py` (own session only, 404 otherwise).
- Frontend: `web-next/src/components/chat/ChatEfficiencyPanel.tsx` (mounted in `pages/chat.tsx`), `src/lib/chat-efficiency.ts`
  (formatting, tiles, notes), `getChatEfficiency` in `src/lib/api/chat.ts`.
- Tests: `core/console/tests/test_chat_efficiency_e2e.py` (registered in `run-all-tests.sh`), `web-next/tests/unit/chat-efficiency.test.tsx`,
  browser E2E `web-next/tests/e2e-fresh/chat-efficiency.spec.ts` (`playwright.chat-efficiency.config.ts`; needs a console seeded with
  `fixtures/chat_efficiency_seed.py`).

## Adding a model

Add its rate to `_MODEL_PRICING_USD_PER_1K` in `model_selection_learner.py`; until then its turns show as unpriced.
