# Automatic model saving — the Free-tier allowance (`cost.optimize`)

> Load when touching `model_selector.resolve_os_model`, `savings_cap`, `savings_meter`, the
> `cost.optimize` capability or the Settings card "Automatic model saving".
> Decision: ADR-2259 · plan: PLAN-0955 · analysis: CONCEPT-0110 (all in Corvin-Knowledge).

## What it is

CorvinOS picks a cheaper model for simple turns (Tier 2.7 / 2.8 / 2.9 / 3 of the OS-model
resolver). On the **Free** tier that automation is limited to a measured share of saving per
UTC month; **Member** is unlimited. The cap removes *savings*, never quality or the
operator's choice.

| Rule | Detail |
|---|---|
| What is capped | only the AUTOMATIC sources `workload`, `ato`, `classifier`, `autoselect` |
| What is never capped | every pin (`override`, `profile_pin`, `persona_pin`, `tenant_pin`) — the Console model choice is binding; upgrades (Opus for a complex turn); helper models; chat; compliance |
| Effect when capped | an automatic choice that ranks **below** the default tier model is replaced by the default (`high_model`); an equal or higher choice passes |
| Metric | `saved / counterfactual` over the current UTC month and installation, where counterfactual = the same tokens priced at the default tier model, actual = priced at the model that ran, **net** (a dearer model subtracts). Pins are included in the meter — `os_turn.completed` carries no decision source — they only never get capped |
| Trigger | `ratio >= effective cap` **and** `counterfactual >= effective floor` (Free: USD 5). Self-regulating: capped turns add counterfactual spend without saving, the ratio decays, automation resumes |

## Single source of the number

`CAPABILITIES["cost.optimize"]["free"]` in `corvin_operator/license/limits.py`
(`ratio` 0.10, `floor_usd` 5.0) — nowhere else, pinned by
`tests/integration/test_cost_optimize_capability.py`. The operator's Settings value is a
*choice* in `<tenant>/global/cost_optimize.json` (`cap_ratio` float 0–1 or absent = tier
default, `floor_usd`):

* **Free** — effective = `min(choice, ceiling)` for ratio and floor alike (stricter only;
  `0.0` switches the automation off). A higher stored value is kept but reported `clamped`.
* **Member** — the choice as given; absent = unlimited; a number is a voluntary self-cap.
* There is **no env var, flag or dev override** (CONCEPT-0041: no kill-switch, no test trust
  root) and **no reset/quota endpoint** — the meter is derived from the audit chain.

## Where it lives

| Piece | File |
|---|---|
| Meter (per-model token sums from `os_turn.completed`, incremental, non-blocking warm-up) | `core/learning/savings_meter.py` |
| Evaluator, settings reader/validator, audit transitions | `corvin_operator/bridges/shared/savings_cap.py` |
| Chokepoint (`resolve_os_model_with_source` + `apply_savings_cap`) | `corvin_operator/bridges/shared/model_selector.py` |
| Settings API `GET/PUT /v1/console/settings/cost-optimize` (above `PUT /settings/{label}`) | `core/console/corvin_console/routes/settings.py` |
| UI card (Settings page, no nav entry) + encoding | `web-next/src/components/settings/CostOptimizeCard.tsx`, `src/lib/cost-optimize.ts` |
| Audit events `cost.optimize_capped` / `_resumed` / `_config_changed` | `corvin_operator/forge/forge/security_events.py` (severity **and** allowlist) |

Both surfaces (bridge `adapter._resolve_os_model_bundled`, console `chat_runtime`) call
`resolve_os_model`; `test_savings_cap_wiring.py` fails if anything reaches the automatic
tiers around it.

## Fail contract

licence module missing/unreadable → tier `free` (never unlimited) · meter I/O fault → **not**
capped (`meter_unavailable`) · meter still warming (first ~0.3 s after start; the request
path never blocks on the chain scan) → not capped (`meter_warming`) · malformed settings
file → ignored · any fault inside the cap → the tiers' choice stands.

## Honest limits

Class L = **deterrence**: a fork can delete the check. Pins make the automation optional for
anyone who sets them. What a fork cannot get is data the authority signs (ADR-2259 §6, future
ADR). Savings are list-price equivalents; a subscription user pays no per-token price.
Measured on the reference install (October 2026, month to date): **1.3 %** net saving against a Sonnet default
(62 % against always-Opus) — the cap rarely trips there because complex turns run on Opus.

## Operations

The Python side needs a restart to take effect:
`systemd-run --user --unit=cost-optimize-restart scripts/safe-restart.sh --wait 900 corvin-webui`
(never `systemctl restart` — it kills running turns). The bridge adapter picks up the same
change on its next restart. A Settings change applies to the NEXT turn without restart.

## Must NOT do

add a second literal of the ratio · let a pin be capped · raise the Free ceiling from the
settings file · add a reset/quota endpoint · write the audit event per turn instead of per
transition · import the meter on the request path blocking · call `resolve_os_model_with_source`
from production code outside `model_selector.py` · describe the cap as a lock.
