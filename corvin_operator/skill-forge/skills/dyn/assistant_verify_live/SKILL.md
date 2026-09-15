---
name: assistant_verify_live
description: Belegt per Versionsanker und Live-Checks, ob ein Feature wirklich deployed, erreichbar und nutzbar ist.
---

# verify-live: Beweis statt Selbstzeugnis

„Done" heißt: der erwartete Commit läuft auf dem Ziel-Target **und** die deklarierten Live-Surfaces antworten.

## Schritt 0.0: Deklaration prüfen (vor allem anderen)
`.verify-live.json` ist der **Input** dieses Skills und gehört dem Projekt — anders als das Script, das aus der Spec deterministisch ableitbar und deshalb selbst zu erzeugen ist. Fehlt die Datei oder fehlen Pflichtfelder → `BLOCKED` („Deklaration fehlt/unvollständig"), **niemals** `N/A`. Auflösungsschritt: das Gerüst unten befüllt vorschlagen und vom Nutzer bestätigen lassen. Live-Surfaces zu erfinden wäre selbst Selbstzeugnis.

```jsonc
{
  "targets": [                          // Pflicht, ≥ 1 Eintrag
    { "name": "prod",                   // Pflicht
      "baseUrl": "https://app.example", // Pflicht
      "versionEndpoint": "/api/version",// optional, Default "/api/version"
      "authEnvVar": "PROD_TOKEN",       // optional, Default: keine Auth
      "isProduction": true,             // optional, Default false
      "allowWrites": false }            // optional, Default false
  ],
  "surfaces": [                         // Pflicht; leeres Array erlaubt (→ N/A)
    { "route": "/dashboard",            // Pflicht
      "expectText": ["Umsatz"],         // optional, String oder Array, Default []
      "clientRendered": false,          // optional, Default false
      "requiresAuth": true,             // optional, Default false
      "apiCall": { "method": "GET", "path": "/api/kpi" } } // optional
  ]
}
```
Auth und Schreibrechte hängen am **Target**, Rendering-Modus und Auth-Bedarf am **Surface**. Geprüft wird jedes Surface gegen jedes Target.

## Schritt 0.1: Tooling
Existiert `scripts/verify-live.mjs`? Wenn nein → **jetzt erzeugen**, nicht blockieren. Fehlendes Script ist niemals ein `BLOCKED`-Grund.

## Schritt 0.2: Capabilities detektieren (im Report unter `capabilities`)
- `node --version` ≥ 20 (sonst global `BLOCKED`)
- Browser-Runtime nur prüfen, wenn ein Surface `clientRendered: true` hat (`npx playwright --version`). Fehlt sie → **nur diese Surfaces** `BLOCKED`.
- Netzwerk: HEAD auf jede `baseUrl`. Nicht erreichbar → Target global `BLOCKED`.
- Token: `process.env[authEnvVar]` gesetzt? Fehlt es → nur Surfaces mit `requiresAuth` `BLOCKED`.

## Schritt 0.3: Erwarteten Commit bestimmen
In dieser Reihenfolge: `EXPECTED_SHA` → CI-Variable (`GITHUB_SHA`, `CI_COMMIT_SHA`) → `git rev-parse HEAD`. Kein Repo und keine Variable → `BLOCKED` („erwarteter Stand unbekannt"), **niemals** `NOT DONE`. Herkunft der SHA im Report vermerken.

## Script-Spezifikation
`scripts/verify-live.mjs`, reines Node ohne Dependencies außer optionalem Playwright:
- liest `.verify-live.json`, validiert Pflichtfelder (Verstoß → Exit 3), iteriert `targets` × `surfaces`
- Versionsanker: GET `versionEndpoint` mit `Cache-Control: no-cache`, `commit` gegen `EXPECTED_SHA` (Präfix-Match ab 7 Zeichen)
- Surface: GET Route → 2xx, nicht-leerer Body, `expectText` enthalten
- `apiCall` read-only; schreibend nur bei `allowWrites: true` **und** `isProduction: false`
- schreibt `verify-live-report.json`: `timestamp`, `expectedSha` + `shaSource`, `foundSha`, `capabilities`, pro Check `status`/`httpStatus`/`headers`/`errors`, plus `verdict`. Credentials werden nie geloggt.
- Exit-Code: 0 = DONE, 1 = NOT DONE, 2 = N/A, 3 = BLOCKED

## Verdikte
- `DONE` — alle ausführbaren Checks grün **und** kein Check `BLOCKED`.
- `NOT DONE` — mindestens ein ausgeführter Check rot, mit Benennung des gerissenen Glieds.
- `N/A` — Config vorhanden und valide, aber `surfaces` leer, mit Begründung. Sichtbar ausgeben, nie als `DONE` tarnen. Eine fehlende Config ist niemals `N/A`.
- `BLOCKED` — Deklaration, Versionsanker, Credentials, Netzwerk oder Browser-Runtime fehlen. Der Report listet **alle** trotzdem geprüften Surfaces samt Ergebnis, dazu je Blocker den konkreten Auflösungsschritt (Befehl, Env-Var, Config-Feld). `BLOCKED` ist ein Zustand mit Ausweg — und wird nie zu `DONE` umgedeutet.

## Bewusste Grenzen
Ein Abruf trifft einen Edge-Node, keine globale Propagationsgarantie. Die Wiring-Kette wird nicht statisch bewiesen — geprüft wird, was der Health-Endpoint meldet. Kein Diff lokaler Bundle-Hashes: nicht reproduzierbar. Abgelaufene Session → `BLOCKED`, nicht `NOT DONE`.

---

**Keywords:** deployment, verification, live-check, version-anchor, smoke-test, evidence

**Dependencies:** git, curl, node, playwright
