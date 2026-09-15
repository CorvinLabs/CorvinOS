---
name: assistant_corvinos_panel_design
description: Erzeugt konsistente, barrierearme und usability-optimierte Web-UI-Panels für die corvinOS Console.
---

# corvinOS Console – Panel Design

Ziel: ansprechende, konsistente und bedienbare Panels für die corvinOS Console erzeugen – ohne Design-Annahmen zu erfinden.

## 1. Bestandsaufnahme (immer zuerst)
- Repo nach vorhandenem Design-System durchsuchen: Glob auf `**/*.css`, `**/tokens*`, `**/theme*`, `**/*panel*`.
- Ein bestehendes Panel vollständig lesen: Markup, Styles, State-Handling.
- Framework, Styling-Ansatz (CSS-Module / Tailwind / plain CSS) und Namenskonventionen notieren.
- Findet sich nichts: die Minimal-Token-Basis aus Abschnitt 2 anlegen und das im Ergebnis ausdrücklich vermerken.

## 2. Design-Tokens statt Magic Values
Keine hartkodierten Farben oder Abstände im Panel-Code. Verwenden oder anlegen:
- Spacing-Skala: 4 / 8 / 12 / 16 / 24 / 32 px
- Radius: sm 4, md 8, lg 12
- Typo: 12 / 14 / 16 / 20 / 24 px, Zeilenhöhe 1.4–1.6
- Farben als semantische Rollen: `surface`, `surface-raised`, `border`, `text`, `text-muted`, `accent`, `success`, `warning`, `danger`
- Dark- und Light-Variante über eine einzige Token-Ebene (z. B. `[data-theme]`)

## 3. Panel-Anatomie
1. **Header:** Titel eine Ebene unter dem Seitenkontext, optionaler Statusindikator, rechts maximal zwei Primäraktionen.
2. **Body:** klare Informationshierarchie, höchstens drei Dichtestufen.
3. **Footer:** nur bei persistenten Aktionen.

Rahmen 1px `border`, Radius `md`, Innenabstand 16–24px. Bei verschachtelten Panels keinen doppelten Rahmen.

## 4. Alle Zustände ausliefern
Ein Panel ist erst fertig, wenn definiert sind: `loading` (Skeleton statt springendem Spinner), `empty` (Erklärung plus nächster Schritt), `error` (Ursache plus Retry), `stale` (Zeitstempel), `default`. Das Layout darf zwischen den Zuständen nicht springen – Höhe reservieren.

## 5. Usability-Regeln
- Wichtigste Information zuerst; Aktionen dort platzieren, wo sie wirken.
- Klickziele mindestens 32×32px, sichtbarer `:focus-visible`-Ring.
- Destruktive Aktionen visuell abgesetzt und bestätigungspflichtig.
- Zahlen mit `font-variant-numeric: tabular-nums` ausrichten.
- Langen Text mit Ellipse und `title` kürzen, niemals das Layout brechen lassen.

## 6. Accessibility (nicht optional)
- Kontrast mindestens 4.5:1 für Text, 3:1 für UI-Grenzen.
- Semantisches Markup (`<section aria-labelledby>`), Buttons sind `<button>`.
- `aria-live="polite"` für sich aktualisierende Werte.
- Vollständige Tastaturbedienung, logische Tab-Reihenfolge.
- `prefers-reduced-motion` respektieren, Transitions maximal 200ms.

## 7. Responsive
Container-orientiert denken: Panels müssen ab etwa 320px Breite funktionieren. Mehrspaltige Inhalte brechen auf eine Spalte um, Aktionen wandern unter den Titel.

## 8. Abschluss-Check
Vor der Übergabe durchgehen und jede Abweichung benennen: Tokens verwendet · alle fünf Zustände vorhanden · Fokus sichtbar · Kontrast geprüft · bei 320px getestet · keine neuen Abhängigkeiten ohne Rücksprache · bestehende Konventionen eingehalten.

---

**Keywords:** corvinos, panel, web-ui, design-system, usability, accessibility, css, frontend

**Dependencies:** Read, Glob, Grep, Write, Edit
