# Plan: CEL cache-stable injection — stop breaking prompt caching (review before implement)

**Status:** Proposal (LDD-review before implementation) · **Datum:** 2026-08-18
**Auslöser:** Gemessen (n=5, opus-5): CEL kostet Multi-Turn **−147 %** (2,5×). Mechanismus bewiesen —
CEL-on verschiebt ~51k Tokens/Turn von cache-READ ($0,50/MTok) in cache-CREATE ($6,25–10/MTok).
**Ziel:** CEL cache-neutral machen (Cache-Create sinkt Richtung CEL-off), **Qualität hält**, verifiziert
durch dasselbe Benchmark.

## Root Cause (aus dem Code-Scan, evidenzbasiert)
- Der CEL-Brief ist **klein** (<2k typisch, ~8-10k worst-case) und steht bereits **zuletzt** unter CorvinOS'
  Blöcken (`chat_runtime._turn_system_prompt` Block 8/9). **Reordering im File hilft nicht.**
- CorvinOS übergibt seinen ganzen Prompt via `--append-system-prompt-file`. Die **Claude-Code-CLI** stellt
  ihren *eigenen* großen Basis-Prompt + Tool-Defs davor und cached `[CLI-Basis + Tools + Append-File]` als
  **einen** System-Prefix mit **einem** End-Breakpoint (keiner der Pfade setzt `cache_control`).
- Eine per-Turn-Änderung im Append-File (CELs volatiler Brief + der meist-leere `_acs_directive_block`)
  invalidiert diesen einen Cache-Eintrag → die ~51k stabiler Upstream-Inhalt (CLI-Basis+Tools) werden
  **neu erzeugt** statt gelesen. Mit CEL-off ist das Append-File byte-stabil → alles Cache-READ.

## Der Fix (Relocation)
**Den volatilen Kontext aus dem gecachten System-Prompt in die per-Turn-User-Message verschieben.**
- `--append-system-prompt-file` bleibt **byte-stabil** über Turns: Basis-Prompt + Persona + User-Profil +
  Memory-*Index* (Blöcke 1,3,4,5,6,9 — alle schon stabil).
- Der **volatile** Teil — der CEL-Brief (Block 8) **und** der ACS-Directive (Block 7) — wird dem
  `claude -p "<prompt>"`-Argument **vorangestellt** (klar abgegrenzt), statt in die System-Datei.
- Ergebnis: Der System-Prefix (Basis+Tools+stabile Blöcke) ändert sich nie → **Cache-READ jeden Turn**;
  der CEL-Kontext liegt im User-Turn, der ohnehin per-Turn neu + billig ist (er liegt **nach** dem
  System-Cache-Breakpoint, berührt den 51k-Cache also nicht).
- Beide Oberflächen: Console (`chat_runtime`) + Bridge (`adapter.py:3367`), identischer Root Cause.
- **Flag** `cel_cache_stable` (default off, ship-dark). Off = heutiges Verhalten exakt.

## Dialektik — was am Fix schiefgehen könnte
1. **Verhaltens-/Qualitätsänderung (System- vs. User-Framing).** Kontext im System-Prompt ist „Rahmen";
   in der User-Message ist es „Teil der Nutzer-Turn". Das Modell könnte es anders gewichten → **muss per
   Benchmark-Qualitäts-Gate verifiziert werden** (Qualität(cache-stable) ≥ Qualität(heute)).
2. **Compliance — die Zwei-Gate-Enforcement inspiziert `task_text`, nicht den Brief** (Invariante I1). Wenn
   der Brief in die User-Message wandert, darf er **nicht** zu „task_text" werden, das die Gates prüfen —
   sonst ändert sich das Gate-Verhalten. Der Fix muss den echten Nutzer-Task von dem vorangestellten
   CEL-Kontext getrennt halten (Gate prüft weiter nur den echten Task).
3. **Wird die User-Message auch gecacht/neu-erzeugt?** Nein — sie liegt nach dem System-Breakpoint; eine
   Änderung dort invalidiert den 51k-System-Cache nicht. Der CEL-Kontext (~2k) ist billiger frischer Input.
4. **Multi-Turn-Konversation:** die User-Messages akkumulieren; frühere Turns können gecacht werden, aber
   das berührt den System-Prefix nicht. Zu prüfen: verschiebt sich das Problem in die Message-History?
   (Erwartung: nein, weil der System-Prefix stabil bleibt und die Message-Historie ohnehin wächst.)
5. **Reihenfolge im User-Prompt:** CEL-Kontext **vor** oder **nach** dem Nutzer-Task? Voranstellen
   („Kontext: … \n\n Aufgabe: <task>") ist natürlich, hält aber den Task als klar abgegrenzten Schluss —
   wichtig für Gate #2 (siehe Risiko 2).

## Verifikation (mit dem Benchmark)
Vorher/Nachher-A/B auf `_default`: der Fix ist erfolgreich, wenn für CEL-on **cache_create deutlich sinkt**
(Richtung CEL-off), die **Kosten-Savings von −147 % Richtung 0/positiv** kippen, **und die Qualität hält**
(Gate). Rohe Token-Zahl bleibt ~gleich (CEL-Kontext kostet gleich viel Input, nur in billigerer Cache-Klasse).

## Offene Punkte für den Review
- O1: Bestätigt der Scan-Mechanismus (ein End-Breakpoint, ganzer Prefix invalidiert)? Oder cached die CLI
  inkrementell (dann re-createn nur ~2k, nicht 51k — dann ist die Erklärung unvollständig)?
- O2: Kann die Bridge/Console den User-Prompt so setzen, ohne die Gate-Task-Extraktion zu stören?
- O3: Gibt es einen einfacheren Weg, einen Cache-Breakpoint VOR CEL zu setzen (statt Relocation)?
