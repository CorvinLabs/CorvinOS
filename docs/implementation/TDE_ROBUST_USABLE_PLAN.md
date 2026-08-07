# TDE robust nutzbar machen — Plan & verifiziertes Design

**Status:** In Arbeit (Design verifiziert am Live-Code 2026-08-07, Bau folgt)
**Owner:** diese Session (Discord bridge)
**Operator-Auftrag (wörtlich):** „ok dann mach das es nutzbar wird. aber robust" (bezogen auf TDE)

**Leitplanke (mein eigener Befund, ADR-0222):** TDE ist als Token-Sparer
wahrscheinlich netto-negativ (93–99 % Kontext-Steuer, jeder parallele Worker zahlt
kalt neu). „Robust nutzbar" heißt deshalb NICHT „blind scharf schalten", sondern:
der Operator KANN `worker_engine: tde` sicher einschalten, es verhält sich robust
(kein toter Turn), und es schaltet sich nur datengesteuert weiter scharf.
Jede Degrade-Ladder endet bei **native**, nie bei einer anderen Engine (CLAUDE.md).

---

## Zustandskarte (Explore-Agent, verifiziert)

**LEBENDIG:** Engine-Wahl `worker_engine` (native/acs/tde) über shared
`delegation_policy.resolve_worker_engine`; Console-TDE-Ausführung `stream_turn` →
`_worker_engine_target` → `_stream_tde_turn` → `SendIntegration.select_engine_and_execute`
(real IPC); Pre-Dispatch-Degrade TDE→native (unavailable/Pool leer), robust; Shadow-
Baseline-Messung hinter `TDE_MEASUREMENT_ENABLED` → `measurement-week/measurement.jsonl`.

**GEBAUT-ABER-TOT:** `decision_gate.py` (Auto-Arm) — nur Tests rufen `evaluate_tde_verdict`;
kein Runtime/CLI/Cron fährt `measurement.jsonl` → `aggregate_measured_evidence` → Gate.
TDE auf Bridges: `adapter.py:7580-7581` `tde_available=False` hart, ADR-0221 P3/P4 frozen.

**FEHLT für „robust nutzbar":**
1. In-Flight-Degrade → native (aktuell: TDE-Fehler zeigt dem User eine Fehlermeldung).
2. Auto-Arm-Gate verdrahten (measurement.jsonl → gate → nur bei measured+TDE_WINS scharf).
3. Risikofreier Shadow (native an User, TDE misst still) — heute setzt Messung `mode=tde` voraus.
4. Bridge-Parität — bleibt frozen bis (2).

---

## Schritt 1 (JETZT): In-Flight-Degrade → native — die Kern-Robustheit

**Problem:** Sobald ein Turn in `_stream_tde_turn` eintritt, gibt es KEINEN Rückfall auf
native. Drei Fehlerpfade zeigen dem User eine Fehlermeldung statt einer Antwort:
- `chat_runtime.py:3880-3891` ImportError → „TDE ist nicht verfügbar …"
- `chat_runtime.py:4058-4074` `reason=="quota_exhausted"` → Upgrade-Hinweis (kein Re-Run)
- `chat_runtime.py:4138-4141` generische Exception → „TDE-Turn fehlgeschlagen: {e}"

Erster echter Content-Yield ist erst `chat_runtime.py:4157` (`"\n"+final+"\n"`); davor nur
Progress-Chrome (`3860-3864`, `3915-3919`). Alle drei Fehler schlagen VOR echtem Content zu
→ sauber auf native degradierbar (kein Doppel-Output).

**Verifizierter Durchfall-Mechanismus:** Der ACS-Block hat BEREITS einen vollständigen,
getesteten native-Fallback über `_quota_fallback` — `chat_runtime.py:5152 if _quota_fallback:`
macht ein L34/L35-Re-Gate mit dem echten `_os_engine` (fail-closed) und läuft dann in den
nativen Claude-Code-Turn (der ACS-Run bei `5210 if not _quota_fallback:` wird übersprungen).
TDE muss sich nur in genau diesen Zustand einklinken — KEIN neuer Kontrollfluss.

**Änderung A — `_stream_tde_turn` (3 Fehlerpfade):** statt Fehlermeldung an den User:
- `_close_books(1, reason="tde_degraded_to_native")` (Audit-Paar sauber schließen — kein
  unpaired `os_turn.started` von 3809); `books_closed = True`.
- KEIN `_append_turn` (der native Pfad persistiert die echte Antwort).
- `yield {"type": "_tde_degraded", "reason": <import|quota|runtime>}`; `return`.
- Wichtig: NUR diese drei frühen Pfade. Der Erfolgspfad mit rc=1/leer (`4080-4082`, TDE lief
  komplett durch) bleibt wie er ist — ein voller TDE-Lauf war kein „nie gestartet", ein
  native-Re-Run danach wäre teuer + doppelt.

**Änderung B — Aufrufer `stream_turn` (`chat_runtime.py:5036`):**
```python
if _tde_target == "tde":
    _tde_degraded = False
    async for _ev in _stream_tde_turn(...):
        if isinstance(_ev, dict) and _ev.get("type") == "_tde_degraded":
            _tde_degraded = True
            continue                      # Sentinel NICHT an den Client
        yield _ev
    if not _tde_degraded:
        return
    # TDE declined in-flight → native (ladder ends at native, never ACS).
else:
    _tde_degraded = False
task_text = _task_text
...
_quota_fallback = False        # → wird bei _tde_degraded auf True gesetzt (s.u.)
```
- ACS-Setup (Import `5060`, run-dir) mit `if not _tde_degraded:` guarden, damit ein TDE-
  Degrade nicht versehentlich ACS anfasst.
- Vor `5152`: bei `_tde_degraded` → `_quota_fallback = True`, ABER `_fb_quota_exceeded`
  MUSS False bleiben (kein irreführender Quota-Hinweis). Stattdessen ein eigener, kurzer
  transparenter Delta-Hinweis: „↩ TDE nicht verfügbar — wechsle auf Standard-Turn." vor dem
  Re-Gate. Prüfen: Default von `_fb_quota_exceeded` (im ACS-Block gesetzt; bei übersprungenem
  Block bleibt Default — verifizieren, dass Default False ist).

**Änderung C — E2E-Test** (`core/console/.../tests/`): TDE-Analyse/IPC wirft (monkeypatch
`run_initial_analysis_sync` bzw. `select_engine_and_execute` → raise) → assert: der Turn
liefert eine NATIVE Antwort (kein „TDE-Turn fehlgeschlagen"), `_tde_degraded`-Sentinel wird
NICHT an den Client geleakt, Audit-Paar sauber (kein unpaired started), genau EIN
`task.completed`/`web.turn.completed`. Muss durch den realen `stream_turn`-Streaming-Pfad
gehen (nicht `_stream_tde_turn` isoliert) — sonst Unit-Test im E2E-Mantel (e2e-wiring-proof).

**ACHTUNG (load-bearing):** `_stream_tde_turn` + der Delegations-Block sind audit-kritisch
(GDPR-Chain, mehrfach adversarial reviewt). Jede Änderung: Audit-Paare exakt einmal
schließen, kein `_append_turn` doppelt, `books_closed`/`_reply_persisted` konsistent.

---

## Schritt 2: Auto-Arm-Gate verdrahten (datengesteuert nutzbar)

- Lebendiger Konsument bauen (CLI `corvin tde gate` ODER Console-Startup-Check ODER
  systemd-timer): lädt `measurement.jsonl` → `aggregate_measured_evidence` →
  `evaluate_tde_verdict`. NUR bei `data_source=="measured"` UND Verdict `TDE_WINS` für eine
  Task-Klasse → schaltet diese Klasse scharf (schreibt die Engine-Wahl/Availability).
- decision_gate kann strukturell nicht ohne gemessene Daten durchstempeln (schon so gebaut).
- e2e-wiring-proof: der Konsument braucht einen realen Trigger + E2E-Test durch ihn.

## Schritt 3: Risikofreier Shadow (Datensammlung ohne TDE-Output an User)

**KRITISCHE Semantik-Entscheidung (verifiziert 2026-08-07):** Die native Antwort ist NUR
der Trigger, NICHT der `direct`-Mess-Arm. Grund: der native Turn läuft mit vollem
Repo-/Tool-Kontext (`cwd=sess.workdir`, Tools erlaubt), `whole_task_direct_baseline` läuft
bewusst tool-los + kontextfrei (`--disallowedTools *`). Die native Antwort als `direct` zu
nehmen würde `direct` künstlich aufblähen (mehr Tokens durch Tools) → TDE sähe gegen einen
fetten `direct` künstlich gut aus = genau der fabrizierte Vorteil, den ADR-0222 verhindert.
Also: der Shadow fährt die ECHTE tool-lose `{direct, tier, tde}`-Messung des Task-Textes
(dieselben Semantiken wie der bestehende Pfad), TDE wird SELBST gefahren statt als Input.

**Was fehlt (Kern-Baustein):** eine Methode, die TDE selbst für einen Task fährt und
`(tokens, output, complexity, workload_type)` liefert — heute kommt `tde_tokens`/`tde_output`
IMMER aus dem echten TDE-Haupt-Turn (Input in `orchestrate`). Neu:
`whole_task_tde_run(task_text, *, run_id, session_key, tenant_id)` (repliziert die
TDE-Mechanik aus `_stream_tde_turn`: `run_initial_analysis_sync` → `SendIntegration.
select_engine_and_execute`; Full-Instrumentation-Gate wie chat_runtime.py:4113).

**Eingriff (kleinster):**
1. `whole_task_tde_run(...)` in `tde_measurement.py` (TDE selbst fahren, Output verworfen).
2. `orchestrate_shadow(...)`: wie `orchestrate`, aber holt tde-Arm aus `whole_task_tde_run`
   statt aus Input; fährt direct + tier + tde, judged tde/tier gegen direct, schreibt Sample.
3. `_spawn_shadow_measurement(ctx)` in chat_runtime.py (Kopie von `_spawn_tde_measurement`,
   gleiches `_MEASUREMENT_TASKS`-Concurrency-1-Gate).
4. Hook nach dem nativen Turn (Ende `stream_turn`), hinter Flag `tde_shadow_measurement`
   (ships-dark, default False) UND `_measurement_should_sample()` (TDE_MEASUREMENT_ENABLED=1).
5. Flag `tde_shadow_measurement` in `feature_flags.py` (Vorlage `acs_context_sync`).
6. Tests (Flag-off = kein Shadow; Flag-on+sampling = Sample geschrieben, native unverändert).

Der User sieht NIE eine TDE-Antwort — TDEs Output geht nur an den Judge und wird verworfen.
Kosten: 3 Extra-Runs pro gemessenem Turn (direct+tier+tde), detached, nur bei Sampling,
nicht gegen Quota gebucht. Damit füttert der Shadow genau das Gate aus Schritt 2.

## Schritt 4: TDE auf der Bridge testen (Operator-Wunsch — Single-User-Messtest)

**Ziel (Operator):** TDE aus Discord/WhatsApp heraus TESTEN + im Hintergrund auf den
echten Bridge-Daten messen. Als einziger Nutzer bewusst den ADR-0221-Bridge-Freeze
aufheben — hinter einem opt-in Flag, reversibel.

**GEBAUT + GETESTET (`adapter.py` + `feature_flags.py`):**
- Flag `bridge_tde_execution` (ships-dark, default False) — hebt den Bridge-TDE-Freeze
  PRO TENANT auf. Allein ausreichend (triggert die volle Route, schaltet nur TDE frei,
  nicht ACS).
- `_worker_engine_target` (adapter.py): `tde_available`/`quota_ok` nicht mehr hart False —
  bei Flag on + mode=tde + nicht force/big-data werden die ECHTEN Console-Probes
  (`_tde_available`/`_tde_quota_peek_ok`) wiederverwendet (kein Bridge-Copy). Flag off /
  Probe-Fehler → frozen default (native).
- `_maybe_delegate_worker`: `target == "tde"` → `_run_tde_delegation`. tde-Flag ODER
  parity-Flag betritt die Route; tde-Flag allein schaltet NICHT ACS frei.
- `_run_tde_delegation`: Bridge-Geschwister von `_run_acs_delegation`. Compliance-Gates
  (L34/L35, engine=claude_code) → TDE via `SendIntegration.select_engine_and_execute`
  (engine-agnostischer Core, KEIN `_stream_tde_turn`-Copy) → Antwort oder None. **Robuster
  Degrade = Self-Healing:** None → nativer Turn (architektonisch geschenkt, die Bridge hat
  `answer=None → native` schon). Quota: TDE bucht INTERN (kein Doppel-Charge); leerer Pool
  → quota_exhausted → None → native.
- **Hintergrund-Messung auf echten Daten:** `_spawn_bridge_measurement` (Daemon-THREAD, weil
  die Bridge asyncio.run pro Turn nutzt — kein persistenter Loop wie die Console) → bestehendes
  `orchestrate` (TDE-Zahlen als Input, tool-lose direct+tier-Baselines schatten) →
  `measurement.jsonl`. Gated durch `_measurement_should_sample` (TDE_MEASUREMENT_ENABLED=1) +
  Full-Instrumentation-Gate.
- E2E `test_bridge_tde_execution.py` (7 grün): Flag-off→native, Flag-on+Probe→tde,
  Probe-Fehler→native, TDE läuft, TDE-Fehler→native (self-healing), tde-Flag-allein≠ACS,
  small-talk→direct. Bestehende `test_bridge_worker_engine_parity.py` (16) grün + Docstrings
  auf „flag-off default statt by construction" aktualisiert.

**Bedienung (Operator):** Settings → Features: `bridge_tde_execution` an, `worker_engine=tde`,
`TDE_MEASUREMENT_ENABLED=1` (+ optional Sample-Rate). Dann coden über Discord/WhatsApp → TDE
läuft real, misst still → nach ein paar Tagen `corvin tde gate` liest den Verdict.
**OFFEN:** optionaler Circuit-Breaker (N Degrades in Folge → Flag auto-aus); commit; ADR-Amendment.

## Schritt 5 (vormals 4): Bridge-ACS-Parität — Flags `bridge_worker_engine_parity` etc.

---

## KRITISCHE Audit-Erkenntnis (verifiziert 2026-08-07) — Design korrigiert

Naiver Erstentwurf (`_close_books` beim Degrade rufen) wäre ein **Compliance-Bug**:
`_os_emit_completed` (`chat_runtime.py:4587-4590`) und der ADR-0171-Span-START
(`4574`, `_os_span_started`) sind **nonlocal-idempotent** auf `stream_turn`-Ebene —
dieselben Funktionen, die TDE (via `os_audit`/`emit_completed`-Params) UND der native
Pfad teilen. Folgen des naiven Entwurfs:
- `_close_books` → `emit_completed(1)` versiegelt den os-Span mit **rc=1** (TDE-Fehler);
- der native `_os_emit_completed` wird dann zum **No-op** (Guard `_os_completed_emitted`)
  → der Span trägt den FALSCHEN Status (native lief evtl. mit rc=0);
- `_close_books` ruft zusätzlich direkt `audit_emit("web.turn.completed")` → **doppeltes**
  `web.turn.completed` für EINEN User-Turn (native emittiert sein eigenes bei `5648`).

**Korrektes Design:** beim Degrade emittiert `_stream_tde_turn` **NICHTS** an Audit
(kein `_close_books`, kein `emit_completed`, kein `web.turn.completed`, kein tm-Event) —
nur `yield {"type":"_tde_degraded","reason":…}` + `return`. Der native Pfad
(`task.started` engine="claude" bei `5830`, span-END via `_os_emit_completed` mit echtem
rc bei `5696`/`5735`) übernimmt ALLE books. Der os-Span-START (`3809`, zugeschrieben
`_os_engine` nicht TDE — Docstring `3797`) wird idempotent vom native `_os_emit_completed`
mit dem echten rc geschlossen → genau EIN sauberes Span-Paar, genau EIN
`web.turn.completed`. Das entspricht exakt dem ACS-quota-fallback (der auch nichts vor
native emittiert). Guard in Pfad 3 (generische Exception): nur degradieren, wenn KEIN
erfolgreicher Content (`final and rc==0`) schon produziert wurde (Post-Success-Exception
im measurement-tail behält die TDE-Antwort).

## Running log
- 2026-08-07 — Aktivierungs-/Degrade-Pfad kartiert (Explore). 3 Fehlerpfade in
  `_stream_tde_turn` identifiziert, native-Fallback via `_quota_fallback` (5152) verifiziert.
  Audit-Semantik (`_os_audit`/`_os_emit_completed`/`_close_books`) tief verifiziert →
  naiver `_close_books`-Degrade wäre Compliance-Bug (falscher Span-rc + doppeltes
  web.turn.completed); Design auf „NICHTS emittieren, native übernimmt" korrigiert.
- 2026-08-07 — **Schritt 1 GEBAUT + GETESTET.** 6 Edits in `chat_runtime.py`: 3
  Fehlerpfade in `_stream_tde_turn` (import/quota/runtime) → `_tde_degraded`-Sentinel
  ohne Audit-Emission + return; Aufrufer schluckt Sentinel + fällt via
  `_quota_fallback = (_tde_degraded_reason is not None)` in den nativen OS-turn; korrekter
  Fallback-Hinweis (`tde_fallback` bzw. Quota-Notice) je Grund. Post-Success-Guard in
  Pfad 3 (`final and rc==0` → TDE-Antwort behalten). Neuer E2E-Test
  `test_tde_degrade_to_native.py` (runtime + mid-run-quota, durch echten `stream_turn`):
  2 grün. Regression: `test_acs_quota_fallback` (3) + `test_delegation_routing_e2e` (15)
  grün — ACS-Pfad + nicht-degradierte Turns unberührt. Doku: `delegation-routing.md`
  um den In-Flight-Degrade ergänzt. Commit `7717c92` (lokal).
- 2026-08-07 — **Schritt 2 GEBAUT + GETESTET.** Lebendiger Gate-Konsument
  `corvin tde gate` (`ops/launcher/corvin/tde_cmd.py`, registriert in `cli.py`): lädt
  `measurement.jsonl` → `MeasurementRecorder.load_from_log` → `get_aggregated_evidence`
  → `evaluate_tde_verdict`, druckt den Verdict; mit `--arm` schaltet es global
  `worker_engine=tde` (via `feature_flags.set_worker_engine_mode`) NUR wenn: auf
  MEASURED-Daten entschieden UND ≥1 Band gewinnt UND KEINE gemessene Band verliert —
  sonst fail-dark (schreibt nichts). E2E `test_tde_gate_cli.py` (echter Subprocess): 4
  grün — keine-Daten→INSUFFICIENT+kein-Arm, alle-gewinnen→armed, mixed(trivial verliert)
  →kein-Arm (robust), JSON valide. WIRING.yaml `decision_gate` deferred→**live**.
  **GRENZE (bewusst):** Arming ist GLOBAL (kein per-Band-Routing heute) → ein Verdict, wo
  TDE `complex` gewinnt aber `trivial` verliert, armt NICHT. Da TDE laut ADR-0222 auf
  trivial/moderate wahrscheinlich verliert, feuert das globale Arm praktisch nie →
  ehrlich, aber TDE bleibt praktisch aus. **Echte Nutzbarkeit braucht per-Band-Arming**
  (armed-band-Store + `band`-Param durch `worker_engine_target`) — eigener ADR-Schritt.
  OFFEN: per-Band-Entscheidung (Operator), Mess-Woche für echte Daten, commit. Commit `ca1263c`.
- 2026-08-07 — **Schritt 3 GEBAUT + GETESTET.** Risikofreier Shadow-Mess-Modus.
  Orchestrierung (`tde_measurement.py`): `_run_tde_arm` (fährt TDE SELBST für einen Task,
  Full-Instrumentation-Gate, self-limiting via Quota — leerer Pool → None) + `orchestrate_shadow`
  (dünner Wrapper: TDE-Arm holen, dann bestehenden `orchestrate` mit direct+tier-Baselines +
  Judges füttern — maximale Wiederverwendung, kein Refactor am kritischen Code). Kern-Semantik:
  native Antwort ist NUR Trigger, NICHT der direct-Arm (tool-behaftet vs. tool-lose Baseline →
  würde fabrizierten TDE-Vorteil erzeugen). chat_runtime: `_run/_spawn_shadow_measurement`
  (detached, concurrency-1, gleiche `_MEASUREMENT_TASKS`) + Hook am Ende des nativen Turns,
  hinter Flag `tde_shadow_measurement` (ships-dark, default False) UND `_measurement_should_sample`
  (TDE_MEASUREMENT_ENABLED=1). Flag in `feature_flags.py` (Vorlage `acs_context_sync`).
  E2E `test_tde_shadow_measurement.py` (durch echten `stream_turn`): 3 grün — Flag+sampling→Hook
  feuert mit korrektem ctx (beweist `_task_text`/`_os_model_used` im Scope), Flag-off→still,
  sampling-off→still. Regression: 27 Tests grün (Schritt 1+2+3 + ACS + Routing).
  **KOSTEN-HINWEIS (Operator):** der Shadow-TDE-Arm ist ein echter Fan-out und bucht den
  geteilten Compute-Pool — self-limiting (leerer Pool → Sample fällt weg), nur bei Sampling +
  Flag. Für eine Mess-Woche: Flag an + `TDE_MEASUREMENT_ENABLED=1` + Sample-Rate, dann füttert
  der Shadow das Gate aus Schritt 2 ohne je eine TDE-Antwort zu zeigen. OFFEN: commit; danach
  Mess-Woche fahren → `corvin tde gate` liest den Verdict.
