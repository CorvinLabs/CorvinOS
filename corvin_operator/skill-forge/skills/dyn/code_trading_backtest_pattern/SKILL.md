---
name: code_trading_backtest_pattern
description: Etabliertes Muster im Corvin-Stack für Trading-Strategie-Optimization: vorhandene Forge-Tools, I/O-Konvention, Multi-Symbol-Optimization, Datenquellen, Workflow.
---

# Trading-Backtest-Pattern (für Forge-Tools)

Wenn ein Trading-Strategie-Optimization-Task ansteht, gibt es ein etabliertes Muster im Corvin-Stack:

## Tools die meist schon existieren
- `code.backtest_multi` — multi-symbol MA-Crossover + ATR-Stop, gibt min/mean PF + per-symbol-Stats zurück
- `code.plot_backtest` — Equity-Curve + Drawdown PNG, gibt base64-PNG zurück
- `code.backtest_btc` — historischer single-symbol Vorgänger (Backward-Compat)

## Convention für I/O
- CSV-Pfade als x-bind: ro-Felder (Forge bind-mountet ro im bwrap)
- Numerische Strategie-Parameter mit x-cache-key: true (Forge-Cache hits über parametric subset)
- Tool returns: {loss, ...metrics} — `loss = 1.0 / max(min_pf, 0.01)` so der Optimizer den schlechtesten Markt verbessert
- Plot-Tools returnen base64-PNG-Bytes UND speichern in `_artifacts_dir`

## Multi-Symbol Optimization
- Compute_run minimiert `loss` = 1/min_pf
- Penalty wenn ein Symbol <5 Trades hat (overfit-Schutz)
- Targeted PF: ≥2 means strategy works on the worst market

## Datenquellen
- yfinance via venv (network: deny im default-Sandbox → daten OUTSIDE bwrap fetchen, CSV reinleiten)
- Period 730d, Interval 1h gibt 5000–17000 Bars je Markt

## Workflow
1. Outside-bwrap: yfinance pull 6 Symbole → ~/.corvin/data/{key}_1h.csv
2. WorkerClient.submit_run(tool_name="code.backtest_multi", param_grid=..., strategy="bayesian")
3. Nach Konvergenz: WorkerClient calls code.plot_backtest pro Symbol mit best_params
4. PNGs aus base64 in outputs/ ablegen — Discord-Bridge attached sie automatisch

## Was NICHT in den Tools steht (operator territory)
- Hyperparameter-Tuning der Bayesian-Suche selbst (warmup, n_axes)
- Symbol-Auswahl (was bedeutet "general enough"?)
- Skin-in-the-game: das ist KEIN Trading-Advice, das ist Backtest-Methodology
