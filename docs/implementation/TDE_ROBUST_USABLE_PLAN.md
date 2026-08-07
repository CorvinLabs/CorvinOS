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

## Schritt 3: Risikofreier Shadow (optional, für Datensammlung ohne TDE-Output an User)

- Separater Pfad: native an den User, TDE läuft detached mit, Output verworfen, nur
  Tokens/Latenz/Judge → measurement.jsonl. Erlaubt Datensammlung, ohne je eine TDE-Antwort
  auszuliefern. Größer; nur wenn Schritt 2 zeigt, dass wir mehr Daten brauchen.

## Schritt 4: Bridge-Parität — erst wenn Gate grün (ADR-0221 P3/P4).

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
  um den In-Flight-Degrade ergänzt. OFFEN: adversarial review + commit/rebase auf
  origin/main; dann Schritt 2 (Auto-Arm-Gate).
