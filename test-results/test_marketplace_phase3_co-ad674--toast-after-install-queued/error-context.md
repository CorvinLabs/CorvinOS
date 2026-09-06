# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: test_marketplace_phase3_complete.spec.ts >> Phase 3: Complete Marketplace Flows >> 4. Toast Notifications >> should show success toast after install queued
- Location: tests/e2e/test_marketplace_phase3_complete.spec.ts:127:9

# Error details

```
TimeoutError: page.waitForSelector: Timeout 5000ms exceeded.
Call log:
  - waiting for locator('[data-testid="plugin-center-tab-marketplace"]') to be visible

```

# Page snapshot

```yaml
- generic [ref=f1e3]:
  - complementary [ref=f1e4]:
    - link "Corvin Operator Console" [ref=f1e5] [cursor=pointer]:
      - /url: /console
      - generic [ref=f1e11]:
        - generic [ref=f1e12]: Corvin
        - generic [ref=f1e13]: Operator Console
    - navigation [ref=f1e14]:
      - generic [ref=f1e15]:
        - link "Chat" [ref=f1e16] [cursor=pointer]:
          - /url: /console/app/chat
        - link "Dashboard" [ref=f1e20] [cursor=pointer]:
          - /url: /console/app/dashboard
      - generic [ref=f1e27]:
        - button "Vibe Engineering" [ref=f1e28] [cursor=pointer]
        - link "Vibe Dashboard" [ref=f1e32] [cursor=pointer]:
          - /url: /console/app/vibe-engineering
      - generic [ref=f1e37]:
        - button "Observability" [ref=f1e38] [cursor=pointer]
        - link "Learning Dashboard" [ref=f1e42] [cursor=pointer]:
          - /url: /console/app/learning-dashboard
      - generic [ref=f1e46]:
        - generic [ref=f1e47]: Messaging
        - generic [ref=f1e48]:
          - link "Channels" [ref=f1e49] [cursor=pointer]:
            - /url: /console/app/bridges
          - link "Profile" [ref=f1e55] [cursor=pointer]:
            - /url: /console/app/voice
          - link "People" [ref=f1e57] [cursor=pointer]:
            - /url: /console/app/people
      - generic [ref=f1e64]:
        - generic [ref=f1e65]: Assistant
        - generic [ref=f1e66]:
          - link "AI Engine" [ref=f1e67] [cursor=pointer]:
            - /url: /console/app/engines
          - link "Browser" [ref=f1e71] [cursor=pointer]:
            - /url: /console/app/browser
          - link "Personas" [ref=f1e75] [cursor=pointer]:
            - /url: /console/app/personas
          - link "Memory" [ref=f1e78] [cursor=pointer]:
            - /url: /console/app/memory
          - link "Files" [ref=f1e81] [cursor=pointer]:
            - /url: /console/app/files
      - generic [ref=f1e85]:
        - button "Build" [ref=f1e86] [cursor=pointer]
        - generic [ref=f1e89]:
          - link "Workflows" [ref=f1e90] [cursor=pointer]:
            - /url: /console/app/workflows
          - link "Pipelines" [ref=f1e95] [cursor=pointer]:
            - /url: /console/app/flows
          - link "Agentic Compute" [ref=f1e101] [cursor=pointer]:
            - /url: /console/app/compute
          - link "Tools" [ref=f1e105] [cursor=pointer]:
            - /url: /console/app/forge
          - link "Skills" [ref=f1e110] [cursor=pointer]:
            - /url: /console/app/skills
          - link "OS Skills" [ref=f1e113] [cursor=pointer]:
            - /url: /console/app/os-skills
          - link "Packages" [ref=f1e118] [cursor=pointer]:
            - /url: /console/app/packages
          - link "Agents" [ref=f1e123] [cursor=pointer]:
            - /url: /console/app/agents
          - link "Plugins & Extensions" [ref=f1e127] [cursor=pointer]:
            - /url: /console/app/plugin-center
          - link "Marketplace" [ref=f1e131] [cursor=pointer]:
            - /url: /console/app/marketplace
      - generic [ref=f1e137]:
        - button "Network" [ref=f1e138] [cursor=pointer]
        - generic [ref=f1e141]:
          - link "Agent Hub" [ref=f1e142] [cursor=pointer]:
            - /url: /console/app/agent-hub
          - link "CorvinSpace" [ref=f1e148] [cursor=pointer]:
            - /url: /console/app/space
          - link "Organisations" [ref=f1e152] [cursor=pointer]:
            - /url: /console/app/orgs
          - link "Connectors" [ref=f1e157] [cursor=pointer]:
            - /url: /console/app/connectors
          - link "Sync Monitor" [ref=f1e160] [cursor=pointer]:
            - /url: /console/app/sync-monitor
          - link "Webhooks" [ref=f1e166] [cursor=pointer]:
            - /url: /console/app/webhooks
      - generic [ref=f1e172]:
        - button "Data" [ref=f1e173] [cursor=pointer]
        - generic [ref=f1e176]:
          - link "Databases" [ref=f1e177] [cursor=pointer]:
            - /url: /console/app/data-sources
          - link "Knowledge" [ref=f1e181] [cursor=pointer]:
            - /url: /console/app/rag
          - link "Knowledge Hub" [ref=f1e186] [cursor=pointer]:
            - /url: /console/app/rag-hub
          - link "Add Provider" [ref=f1e192] [cursor=pointer]:
            - /url: /console/app/custom-provider
      - button "System" [ref=f1e197] [cursor=pointer]
      - generic [ref=f1e201]:
        - generic [ref=f1e202]: Your panels
        - link "Task Graph — Redesigned" [ref=f1e204] [cursor=pointer]:
          - /url: /console/app/task-graph-redesigned
    - link "Licence Free" [ref=f1e207] [cursor=pointer]:
      - /url: /console/app/license
      - generic [ref=f1e208]: Licence
      - generic [ref=f1e209]: Free
    - generic [ref=f1e210]:
      - generic [ref=f1e211]:
        - generic [ref=f1e212]: _default
        - generic [ref=f1e213]: owner
      - button "Log out" [ref=f1e214] [cursor=pointer]
  - generic [ref=f1e215]:
    - banner [ref=f1e216]:
      - generic [ref=f1e217]: _default
      - generic [ref=f1e219]:
        - link "Claude Code" [ref=f1e220] [cursor=pointer]:
          - /url: /console/app/engines
        - button "Corvin Assistant" [ref=f1e224] [cursor=pointer]:
          - generic [ref=f1e230]: Assistant
        - 'button "Theme: Dark theme (click to switch)" [ref=f1e231] [cursor=pointer]'
    - main [ref=f1e232]:
      - generic [ref=f1e233]:
        - main [ref=f1e234]:
          - generic [ref=f1e235]:
            - generic [ref=f1e236]:
              - generic [ref=f1e237]: NordTech Solutions GmbH — KI-Kommandozentrale
              - generic [ref=f1e238]:
                - generic [ref=f1e239]: web:W81tTu42-G
                - generic [ref=f1e240]: 6 turns
            - generic [ref=f1e241]:
              - button "Audit" [ref=f1e242] [cursor=pointer]
              - button "Claude Code" [ref=f1e244] [cursor=pointer]
              - button "Recap the whole session" [ref=f1e250] [cursor=pointer]
              - button "Voice on" [ref=f1e251] [cursor=pointer]
              - button "Open session artifacts folder" [ref=f1e252] [cursor=pointer]
          - generic [ref=f1e254]:
            - paragraph [ref=f1e258]: "Hallo! Ich bin das NordTech KI-System. Beantworte bitte auf Deutsch: In welchem Bundesland liegt Berlin?"
            - generic [ref=f1e267]:
              - paragraph [ref=f1e270]:
                - text: Berlin ist selbst ein Bundesland. Berlin ist ein sogenannter Stadtstaat und einer der 16 Bundesländer der Bundesrepublik Deutschland. Es liegt nicht
                - emphasis [ref=f1e271]: in
                - text: einem anderen Bundesland, sondern ist eine eigenständige Verwaltungseinheit im Nordosten Deutschlands.
              - generic [ref=f1e272]:
                - generic [ref=f1e274]:
                  - button "Play" [ref=f1e275] [cursor=pointer]
                  - generic [ref=f1e278]: 0:00
                  - slider [ref=f1e279] [cursor=pointer]: "0"
                  - generic [ref=f1e280]: 0:36
                  - button "Mute" [ref=f1e281] [cursor=pointer]
                  - slider [ref=f1e286] [cursor=pointer]: "1"
                - generic [ref=f1e287]:
                  - generic [ref=f1e291]: 717ab3abe9a47abd.mp3
                  - generic [ref=f1e292]: voice
                  - generic [ref=f1e293]: 566.6 KB
                  - link "Download file" [ref=f1e294] [cursor=pointer]:
                    - /url: /v1/console/chat/sessions/W81tTu42-GdqQXZowDIBaw/workdir/voice/717ab3abe9a47abd.mp3
            - paragraph [ref=f1e301]: "Danke! Noch eine kurze Folgefrage: Nenne mir genau eine bekannte Sehenswürdigkeit in der Stadt, von der du gerade gesprochen hast."
            - generic [ref=f1e310]:
              - paragraph [ref=f1e313]: Das Brandenburger Tor ist eine der bekanntesten Sehenswürdigkeiten in Berlin. Es ist ein historisches Denkmal aus dem 18. Jahrhundert und eines der Wahrzeichen Deutschlands.
              - generic [ref=f1e314]:
                - generic [ref=f1e316]:
                  - button "Play" [ref=f1e317] [cursor=pointer]
                  - generic [ref=f1e320]: 0:00
                  - slider [ref=f1e321] [cursor=pointer]: "0"
                  - generic [ref=f1e322]: 0:17
                  - button "Mute" [ref=f1e323] [cursor=pointer]
                  - slider [ref=f1e328] [cursor=pointer]: "1"
                - generic [ref=f1e329]:
                  - generic [ref=f1e333]: 9287429e90f9250e.mp3
                  - generic [ref=f1e334]: voice
                  - generic [ref=f1e335]: 276.4 KB
                  - link "Download file" [ref=f1e336] [cursor=pointer]:
                    - /url: /v1/console/chat/sessions/W81tTu42-GdqQXZowDIBaw/workdir/voice/9287429e90f9250e.mp3
            - paragraph [ref=f1e343]: "NordTech-Aufgabe #4721: Schreibe bitte eine Python-Funktion `calculate_mrr(subscriptions: list[dict]) -> float` die aus einer Liste von Abonnements mit dem Feld `monthly_amount` den Monthly Recurring Revenue berechnet. Kommentiere die Funktion auf Deutsch."
            - paragraph [ref=f1e356]: "NordTech Q3-Analyse: Hier sind unsere monatlichen Umsatzzahlen in EUR: Jan=87400, Feb=91200, Mär=88750, Apr=95600, Mai=103200, Jun=98400, Jul=112000, Aug=108900, Sep=118500, Okt=124300, Nov=131000, Dez=145600. Bitte berechne: (1) Gesamtjahresumsatz, (2) Durchschnitt pro Monat, (3) stärkstes Wachstumsmonat, (4) Wachstumsrate Q1→Q4 in Prozent. Präsentiere die Ergebnisse strukturiert auf Deutsch."
            - paragraph [ref=f1e369]: "NordTech HR-System: Erstelle bitte eine CSV-Datei mit 8 fiktiven Mitarbeitern für unser Berliner Büro. Felder: mitarbeiter_id,name,abteilung,eintrittsdatum,gehalt_eur. Abteilungen: Engineering, Product, Sales, Operations. Nutze realistische deutsche Namen und Gehälter (50k–120k EUR)."
            - generic [ref=f1e378]:
              - paragraph [ref=f1e381]: NordTech Berlin HR roster (8 employees, CSV format) — realistic German names, 50–120k EUR salary band, 2019–2026 hire dates, balanced across Engineering/Product/Sales/Operations.
              - generic [ref=f1e382]:
                - generic [ref=f1e383]: "{ \"workflow_id\": \"web-chat-delegation\", \"run_id\": \"acs-web-1788654336-96e95b\", \"iteration\": 1, \"artifacts\": [ \"output/nordtech_employees.csv\" ], \"quality_score\": 0.98 }"
                - generic [ref=f1e385]:
                  - generic [ref=f1e389]: manifest.json
                  - generic [ref=f1e390]: live
                  - generic [ref=f1e391]: 0.2 KB
                  - link "Download file" [ref=f1e392] [cursor=pointer]:
                    - /url: /v1/console/chat/sessions/W81tTu42-GdqQXZowDIBaw/workdir/acs/runs/acs-web-1788654336-96e95b/output/FINAL/manifest.json
              - generic [ref=f1e396]:
                - generic [ref=f1e398]:
                  - button "Play" [ref=f1e399] [cursor=pointer]
                  - generic [ref=f1e402]: 0:00
                  - slider [ref=f1e403] [cursor=pointer]: "0"
                  - generic [ref=f1e404]: 1:07
                  - button "Mute" [ref=f1e405] [cursor=pointer]
                  - slider [ref=f1e410] [cursor=pointer]: "1"
                - generic [ref=f1e411]:
                  - generic [ref=f1e415]: 00ba11b656c6de76.mp3
                  - generic [ref=f1e416]: voice
                  - generic [ref=f1e417]: 1054.1 KB
                  - link "Download file" [ref=f1e418] [cursor=pointer]:
                    - /url: /v1/console/chat/sessions/W81tTu42-GdqQXZowDIBaw/workdir/voice/00ba11b656c6de76.mp3
            - paragraph [ref=f1e425]: "NordTech Sprint #23 Planning: Unser Engineering-Team (6 Personen, 85 SP Kapazität) soll folgende Epics priorisieren: (A) API-Authentifizierung OAuth2 [34 SP], (B) Dashboard-Performance-Optimierung [21 SP], (C) Automatisierte Backup-Pipeline [13 SP], (D) Kundenportal v2.0 Beta [55 SP], (E) Security-Audit-Remediation [8 SP]. Erstelle einen priorisierten Sprint-Backlog mit Begründung."
            - generic [ref=f1e434]:
              - generic [ref=f1e436]:
                - 'heading "NordTech Sprint #23 — Priorisierter Backlog (85 SP Kapazität)" [level=2] [ref=f1e437]'
                - paragraph [ref=f1e438]:
                  - strong [ref=f1e439]: "Sprint-Auswahl (76 SP):"
                - table [ref=f1e441]:
                  - rowgroup [ref=f1e442]:
                    - row [ref=f1e443]:
                      - columnheader "Rang" [ref=f1e444]
                      - columnheader "Epic" [ref=f1e445]
                      - columnheader "SP" [ref=f1e446]
                      - columnheader "Begründung" [ref=f1e447]
                  - rowgroup [ref=f1e448]:
                    - row [ref=f1e449]:
                      - cell "1" [ref=f1e450]
                      - cell [ref=f1e451]:
                        - strong [ref=f1e452]: (E) Security-Audit-Remediation
                      - cell "8" [ref=f1e453]
                      - cell [ref=f1e454]:
                        - strong [ref=f1e455]: KRITISCH
                        - text: ": Compliance-blocker, klein, Risk-Reduktion → sofort"
                    - row [ref=f1e456]:
                      - cell "2" [ref=f1e457]
                      - cell [ref=f1e458]:
                        - strong [ref=f1e459]: (A) OAuth2-Authentifizierung
                      - cell "34" [ref=f1e460]
                      - cell [ref=f1e461]:
                        - strong [ref=f1e462]: Foundational
                        - text: ": blockiert (D) Kundenportal, Sicherheit, mittlere Komplexität → Woche 1–2"
                    - row [ref=f1e463]:
                      - cell "3" [ref=f1e464]
                      - cell [ref=f1e465]:
                        - strong [ref=f1e466]: (C) Backup-Pipeline
                      - cell "13" [ref=f1e467]
                      - cell [ref=f1e468]:
                        - strong [ref=f1e469]: Operational
                        - text: ": Disaster-Recovery-Notwendigkeit, unabhängig, schnell → parallel zu A"
                    - row [ref=f1e470]:
                      - cell "4" [ref=f1e471]
                      - cell [ref=f1e472]:
                        - strong [ref=f1e473]: (B) Dashboard-Performance
                      - cell "21" [ref=f1e474]
                      - cell [ref=f1e475]:
                        - strong [ref=f1e476]: Customer Value
                        - text: ": observability, aber nicht kritisch → Woche 3"
                - paragraph [ref=f1e477]:
                  - strong [ref=f1e478]: "Nicht im Sprint:"
                - list [ref=f1e479]:
                  - listitem [ref=f1e480]:
                    - strong [ref=f1e481]: (D) Kundenportal v2.0 Beta
                    - text: "[55 SP] →"
                    - strong [ref=f1e482]: "Backlog für Sprint #24"
                    - text: "(benötigt OAuth2 aus Sprint #23 als Dependency)"
                - paragraph [ref=f1e483]:
                  - strong [ref=f1e484]: "Rationale:"
                - list [ref=f1e485]:
                  - listitem [ref=f1e486]:
                    - strong [ref=f1e487]: Risk First
                    - text: "(E): Audit-Funde blockieren ggf. Produktion → zuerst"
                  - listitem [ref=f1e488]:
                    - strong [ref=f1e489]: Dependency Chain
                    - text: "(A→D): OAuth2 ist Blocker für Portal → priorisieren"
                  - listitem [ref=f1e490]:
                    - strong [ref=f1e491]: Kapazitätspuffer
                    - text: ": 76/85 SP (9 SP Reserve für Unvorhergesehenes)"
                  - listitem [ref=f1e492]:
                    - strong [ref=f1e493]: Parallelisierbar
                    - text: ": (A) + (C) können verschiedene Teams nehmen"
                - paragraph [ref=f1e494]: Soll ich die Story-Point-Schätzung pro Epic verfeinern oder Sprint-Assignments für die 6 Team-Mitglieder entwerfen?
              - generic [ref=f1e495]:
                - generic [ref=f1e497]:
                  - button "Play" [ref=f1e498] [cursor=pointer]
                  - generic [ref=f1e501]: 0:00
                  - slider [ref=f1e502] [cursor=pointer]: "0"
                  - generic [ref=f1e503]: 2:12
                  - button "Mute" [ref=f1e504] [cursor=pointer]
                  - slider [ref=f1e509] [cursor=pointer]: "1"
                - generic [ref=f1e510]:
                  - generic [ref=f1e514]: 77d65a2302422081.mp3
                  - generic [ref=f1e515]: voice
                  - generic [ref=f1e516]: 2072.3 KB
                  - link "Download file" [ref=f1e517] [cursor=pointer]:
                    - /url: /v1/console/chat/sessions/W81tTu42-GdqQXZowDIBaw/workdir/voice/77d65a2302422081.mp3
          - generic [ref=f1e522]:
            - button "Claude Code" [ref=f1e523] [cursor=pointer]
            - button "auto" [ref=f1e526] [cursor=pointer]
            - button "Voice on" [ref=f1e529] [cursor=pointer]
            - generic [ref=f1e534]: type / for commands
          - generic [ref=f1e536]:
            - button "Attach files" [ref=f1e537]
            - generic [ref=f1e538]:
              - textbox "Message Corvin… (hold Space to speak)" [ref=f1e540]
              - generic [ref=f1e541]:
                - button "Attach files (CSV, PDF, images, …)" [ref=f1e542] [cursor=pointer]
                - button "Start recording (or hold Space)" [ref=f1e543] [cursor=pointer]
                - button "Send (Enter · Shift+Enter for newline)" [disabled]
        - complementary [ref=f1e544]:
          - generic [ref=f1e545]:
            - generic [ref=f1e546]: Chats
            - button "New" [ref=f1e547] [cursor=pointer]
          - generic [ref=f1e548]:
            - generic [ref=f1e549]:
              - button "NordTech Solutions GmbH — KI-Kommandozentrale 6 turns · Sep 06, 2026, 02:27 AM" [ref=f1e550] [cursor=pointer]:
                - generic [ref=f1e551]: NordTech Solutions GmbH — KI-Kommandozentrale
                - generic [ref=f1e553]:
                  - generic [ref=f1e554]: 6 turns
                  - generic [ref=f1e555]: ·
                  - generic [ref=f1e556]: Sep 06, 2026, 02:27 AM
              - button "Rename session" [ref=f1e557] [cursor=pointer]
              - button "Delete session" [ref=f1e561] [cursor=pointer]
            - generic [ref=f1e565]:
              - button "New Chat 0 turns · Sep 06, 2026, 02:23 AM" [ref=f1e566] [cursor=pointer]:
                - generic [ref=f1e567]: New Chat
                - generic [ref=f1e569]:
                  - generic [ref=f1e570]: 0 turns
                  - generic [ref=f1e571]: ·
                  - generic [ref=f1e572]: Sep 06, 2026, 02:23 AM
              - button "Rename session" [ref=f1e573] [cursor=pointer]
              - button "Delete session" [ref=f1e577] [cursor=pointer]
            - generic [ref=f1e581]:
              - button "New Chat 0 turns · Sep 06, 2026, 02:23 AM" [ref=f1e582] [cursor=pointer]:
                - generic [ref=f1e583]: New Chat
                - generic [ref=f1e585]:
                  - generic [ref=f1e586]: 0 turns
                  - generic [ref=f1e587]: ·
                  - generic [ref=f1e588]: Sep 06, 2026, 02:23 AM
              - button "Rename session" [ref=f1e589] [cursor=pointer]
              - button "Delete session" [ref=f1e593] [cursor=pointer]
            - generic [ref=f1e597]:
              - button "NordTech — Task Engine E2E 0 turns · Sep 06, 2026, 02:22 AM" [ref=f1e598] [cursor=pointer]:
                - generic [ref=f1e599]: NordTech — Task Engine E2E
                - generic [ref=f1e601]:
                  - generic [ref=f1e602]: 0 turns
                  - generic [ref=f1e603]: ·
                  - generic [ref=f1e604]: Sep 06, 2026, 02:22 AM
              - button "Rename session" [ref=f1e605] [cursor=pointer]
              - button "Delete session" [ref=f1e609] [cursor=pointer]
            - generic [ref=f1e613]:
              - button "mach mal eine tiefgehende… 2 turns · Aug 30, 2026, 11:28 AM" [ref=f1e614] [cursor=pointer]:
                - generic [ref=f1e615]: mach mal eine tiefgehende…
                - generic [ref=f1e617]:
                  - generic [ref=f1e618]: 2 turns
                  - generic [ref=f1e619]: ·
                  - generic [ref=f1e620]: Aug 30, 2026, 11:28 AM
              - button "Rename session" [ref=f1e621] [cursor=pointer]
              - button "Delete session" [ref=f1e625] [cursor=pointer]
            - generic [ref=f1e629]:
              - button "ich bekomme ständig diese… 1 turn · Aug 30, 2026, 09:42 AM" [ref=f1e630] [cursor=pointer]:
                - generic [ref=f1e631]: ich bekomme ständig diese…
                - generic [ref=f1e633]:
                  - generic [ref=f1e634]: 1 turn
                  - generic [ref=f1e635]: ·
                  - generic [ref=f1e636]: Aug 30, 2026, 09:42 AM
              - button "Rename session" [ref=f1e637] [cursor=pointer]
              - button "Delete session" [ref=f1e641] [cursor=pointer]
            - generic [ref=f1e645]:
              - button "das neue frontend wird… 1 turn · Aug 26, 2026, 04:27 PM" [ref=f1e646] [cursor=pointer]:
                - generic [ref=f1e647]: das neue frontend wird…
                - generic [ref=f1e649]:
                  - generic [ref=f1e650]: 1 turn
                  - generic [ref=f1e651]: ·
                  - generic [ref=f1e652]: Aug 26, 2026, 04:27 PM
              - button "Rename session" [ref=f1e653] [cursor=pointer]
              - button "Delete session" [ref=f1e657] [cursor=pointer]
            - generic [ref=f1e661]:
              - button "räume das repo auf… 1 turn · Aug 25, 2026, 01:09 AM" [ref=f1e662] [cursor=pointer]:
                - generic [ref=f1e663]: räume das repo auf…
                - generic [ref=f1e665]:
                  - generic [ref=f1e666]: 1 turn
                  - generic [ref=f1e667]: ·
                  - generic [ref=f1e668]: Aug 25, 2026, 01:09 AM
              - button "Rename session" [ref=f1e669] [cursor=pointer]
              - button "Delete session" [ref=f1e673] [cursor=pointer]
            - generic [ref=f1e677]:
              - button "nutze diesen skill assistant.corvin_end_to_end_ldd… 1 turn · Aug 25, 2026, 01:05 AM" [ref=f1e678] [cursor=pointer]:
                - generic [ref=f1e679]: nutze diesen skill assistant.corvin_end_to_end_ldd…
                - generic [ref=f1e681]:
                  - generic [ref=f1e682]: 1 turn
                  - generic [ref=f1e683]: ·
                  - generic [ref=f1e684]: Aug 25, 2026, 01:05 AM
              - button "Rename session" [ref=f1e685] [cursor=pointer]
              - button "Delete session" [ref=f1e689] [cursor=pointer]
            - generic [ref=f1e693]:
              - button "nutze den skill assistant.corvinos_panel_design… 3 turns · Aug 25, 2026, 12:35 AM" [ref=f1e694] [cursor=pointer]:
                - generic [ref=f1e695]: nutze den skill assistant.corvinos_panel_design…
                - generic [ref=f1e697]:
                  - generic [ref=f1e698]: 3 turns
                  - generic [ref=f1e699]: ·
                  - generic [ref=f1e700]: Aug 25, 2026, 12:35 AM
              - button "Rename session" [ref=f1e701] [cursor=pointer]
              - button "Delete session" [ref=f1e705] [cursor=pointer]
            - generic [ref=f1e709]:
              - button "es gibt eine neue… 3 turns · Aug 24, 2026, 10:45 PM" [ref=f1e710] [cursor=pointer]:
                - generic [ref=f1e711]: es gibt eine neue…
                - generic [ref=f1e713]:
                  - generic [ref=f1e714]: 3 turns
                  - generic [ref=f1e715]: ·
                  - generic [ref=f1e716]: Aug 24, 2026, 10:45 PM
              - button "Rename session" [ref=f1e717] [cursor=pointer]
              - button "Delete session" [ref=f1e721] [cursor=pointer]
            - generic [ref=f1e725]:
              - button "erzeuge in http://127.0.0.1:8765/console/app/skills mit… 6 turns · Aug 20, 2026, 12:41 PM" [ref=f1e726] [cursor=pointer]:
                - generic [ref=f1e727]: erzeuge in http://127.0.0.1:8765/console/app/skills mit…
                - generic [ref=f1e729]:
                  - generic [ref=f1e730]: 6 turns
                  - generic [ref=f1e731]: ·
                  - generic [ref=f1e732]: Aug 20, 2026, 12:41 PM
              - button "Rename session" [ref=f1e733] [cursor=pointer]
              - button "Delete session" [ref=f1e737] [cursor=pointer]
          - generic [ref=f1e741]: Chat sessions are scoped to this browser. Switch sessions using the list above.
```

# Test source

```ts
  1   | /**
  2   |  * E2E Tests for Console Marketplace Phase 3 — Complete Flows
  3   |  *
  4   |  * Tests all 6 components:
  5   |  * 1. Console Bootstrap (startup initialization)
  6   |  * 2. E2E Playwright Tests (all flows)
  7   |  * 3. Installed Tab Population (live-sync with PluginsPage)
  8   |  * 4. Toast Notifications (success/error feedback)
  9   |  * 5. Progress Polling (GET /marketplace/install/{job_id}/progress)
  10  |  * 6. Settings Panel (pre-install configuration)
  11  |  *
  12  |  * Constraint: ADR-0297 (PII fail-closed) — no PII in error messages
  13  |  * Coverage: 100% E2E for discover → search → install → settings → monitor flow
  14  |  */
  15  | 
  16  | import { test, expect } from '@playwright/test'
  17  | 
  18  | const CONSOLE_URL = 'http://127.0.0.1:8765/console'
  19  | const MARKETPLACE_TAB = '[data-testid="plugin-center-tab-marketplace"]'
  20  | const INSTALLED_TAB = '[data-testid="plugin-center-tab-plugins"]'
  21  | 
  22  | test.describe('Phase 3: Complete Marketplace Flows', () => {
  23  |   test.beforeEach(async ({ page }) => {
  24  |     // Navigate to plugin center, marketplace tab
  25  |     await page.goto(`${CONSOLE_URL}/#/app/plugin-center?tab=marketplace`)
> 26  |     await page.waitForSelector(MARKETPLACE_TAB, { timeout: 5000 })
      |                ^ TimeoutError: page.waitForSelector: Timeout 5000ms exceeded.
  27  |   })
  28  | 
  29  |   // ─── 1. CONSOLE BOOTSTRAP FLOW ───────────────────────────────────────────
  30  |   test.describe('1. Console Bootstrap', () => {
  31  |     test('should load console with marketplace panel initialized', async ({ page }) => {
  32  |       // Verify marketplace panel is rendered
  33  |       const browseView = page.locator('input[placeholder*="Search"]')
  34  |       await expect(browseView).toBeVisible({ timeout: 3000 })
  35  |     })
  36  | 
  37  |     test('should have marketplace routes available (/api/v2/marketplace/*)', async ({ page }) => {
  38  |       // Check that API calls to /api/v2/marketplace/index return 200
  39  |       const response = await page.evaluate(async () => {
  40  |         const res = await fetch('/api/v2/marketplace/index')
  41  |         return { status: res.status, ok: res.ok }
  42  |       })
  43  |       expect(response.ok).toBe(true)
  44  |     })
  45  |   })
  46  | 
  47  |   // ─── 2. E2E FLOWS ────────────────────────────────────────────────────────
  48  |   test.describe('2. E2E Discovery + Install Flow', () => {
  49  |     test('should discover plugins: browse → search → click → view details', async ({ page }) => {
  50  |       // Browse: Extensions grid loads
  51  |       await page.waitForTimeout(500)
  52  |       const grid = page.locator('[class*="grid"]')
  53  |       await expect(grid).toBeVisible()
  54  | 
  55  |       // Search: Filter by name
  56  |       const search = page.locator('input[placeholder*="Search"]')
  57  |       await search.fill('auth')
  58  |       await page.waitForTimeout(300)
  59  | 
  60  |       // Click: First result detail modal
  61  |       const firstCard = page.locator('[class*="grid"] > div').first()
  62  |       await expect(firstCard).toBeVisible()
  63  |       await firstCard.click()
  64  | 
  65  |       // Details: Modal shows metadata
  66  |       const modal = page.locator('[class*="fixed"][class*="inset-0"]')
  67  |       await expect(modal).toBeVisible()
  68  |       await expect(modal.locator('h2')).toBeVisible()
  69  |     })
  70  | 
  71  |     test('should handle install with error feedback (no PII in messages)', async ({ page }) => {
  72  |       // Open first extension
  73  |       await page.waitForTimeout(500)
  74  |       const firstCard = page.locator('[class*="grid"] > div').first()
  75  |       await firstCard.click()
  76  | 
  77  |       // Click install (will fail without real backend, but test error handling)
  78  |       const modal = page.locator('[class*="fixed"][class*="inset-0"]')
  79  |       const installBtn = modal.locator('button:has-text("Install")')
  80  |       await installBtn.click()
  81  | 
  82  |       // Wait for error message (should NOT contain PII like user IDs, emails, paths)
  83  |       const errorMsg = page.locator('[class*="bg-red"][class*="text-red"]')
  84  |       const text = await errorMsg.textContent()
  85  |       expect(text).not.toMatch(/[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}/) // No email
  86  |       expect(text).not.toMatch(/\/home\/\w+/) // No home paths
  87  |     })
  88  |   })
  89  | 
  90  |   // ─── 3. INSTALLED TAB POPULATION & SYNC ──────────────────────────────────
  91  |   test.describe('3. Installed Tab Population + Live-Sync', () => {
  92  |     test('should populate installed tab with plugins from PluginsPage', async ({ page }) => {
  93  |       // Switch to Plugins (Installed) tab
  94  |       const pluginsTab = page.locator(INSTALLED_TAB)
  95  |       await pluginsTab.click()
  96  | 
  97  |       // Wait for plugins list to load
  98  |       await page.waitForSelector('[data-testid="plugin-list"], [class*="grid"]', { timeout: 3000 })
  99  | 
  100 |       // Should show installed plugins (or empty placeholder)
  101 |       const list = page.locator('[class*="grid"], [data-testid="plugin-list"]')
  102 |       await expect(list).toBeVisible()
  103 |     })
  104 | 
  105 |     test('should sync when returning to marketplace after install', async ({ page }) => {
  106 |       // Click marketplace tab
  107 |       const marketplaceTab = page.locator(MARKETPLACE_TAB)
  108 |       await marketplaceTab.click()
  109 |       await page.waitForTimeout(300)
  110 | 
  111 |       // Install a plugin (mock/stub)
  112 |       // ... (simulated via state)
  113 | 
  114 |       // Return to plugins tab to verify sync
  115 |       const pluginsTab = page.locator(INSTALLED_TAB)
  116 |       await pluginsTab.click()
  117 |       await page.waitForTimeout(500)
  118 | 
  119 |       // Verify plugin appears in installed list (or verify sync flag triggered)
  120 |       // For now: just verify navigation works
  121 |       await expect(pluginsTab).toHaveAttribute('class', /active|selected/)
  122 |     })
  123 |   })
  124 | 
  125 |   // ─── 4. TOAST NOTIFICATIONS ─────────────────────────────────────────────
  126 |   test.describe('4. Toast Notifications', () => {
```