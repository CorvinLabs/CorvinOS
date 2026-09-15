---
name: assistant_markdown_link_check
description: Prueft Markdown-Dateien auf kaputte relative Links und fehlende Bilddateien und meldet jede Fundstelle mit Datei und Zeilennummer.
---

# Markdown Link Check

Prüft `.md`-Dateien auf relative Links und Bildreferenzen, deren Ziel im Dateisystem nicht existiert. Auslösen, wenn Dokumentation umstrukturiert wurde, Dateien verschoben/umbenannt wurden oder vor einem Docs-Release.

**Dieses Skill-Verzeichnis enthält `check_links.py` (nur Standardbibliothek, nur lesende Zugriffe, keine Netzwerkaufrufe). Nicht neu implementieren, nur aufrufen.**

## Aufruf

```
python check_links.py docs/                      # rekursiv prüfen, Report auf stdout
python check_links.py . --ignore 'vendor/**' --case-check
python check_links.py docs/ --baseline .links-baseline.json --json
```

Weitere Optionen: `--root <dir>` (löst `/pfad`-Ziele gegen diesen Root auf statt sie zu ignorieren), `--check-anchors` (Anker gegen Headings, GitHub-Slug-Konvention), `--write-baseline <datei>`.

## Was geprüft wird

Nur: existiert das relative Ziel auf der Platte, aufgelöst relativ zum Verzeichnis der jeweiligen `.md`-Datei. Erkannt werden Inline-Links, Bilder, Referenz-Definitionen, `<img src>` und `<a href>`; Code-Fences, Frontmatter, HTML-Kommentare und Inline-Code werden ausgeblendet, eingerückte Blöcke in Listen dagegen geprüft. `pfad.md#anker` wird zerlegt, geprüft wird der Dateiteil.

Nicht geprüft werden:
- Anker/Headings — außer mit `--check-anchors`.
- `http(s)`-URLs, `mailto:`, andere Schemata, `//host`, reine Anker, leere Ziele, Template-Platzhalter (`{{`, `${`).
- Groß-/Kleinschreibung — außer mit `--case-check`; dann meldet der Report zusätzlich `(case mismatch: Assets/Logo.png)`.
- Docs-Root-Auflösung (MkDocs/Docusaurus/Sphinx) — außer mit `--root`.

## Ausgabe und Exit-Codes

Report gruppiert nach Datei, eine Zeile je Fund, abschließend `geprüft X Dateien / Y Referenzen, Z Funde`:

```
docs/setup.md:42: BROKEN LINK -> ../install.md
README.md:7: MISSING IMAGE -> assets/logo.png
```

`--json` schreibt stattdessen eine Liste von `{file, line, type, target, reason}` auf stdout, der Report geht nach stderr.

Exit-Codes: `0` keine Funde, `1` Funde vorhanden, `2` Ausführungsfehler (unlesbare Datei, ungültige Option).

## Baseline für Bestandsprojekte

Erstlauf ohne Baseline macht ein CI-Gate sofort rot. Daher:

1. `python check_links.py docs/ --write-baseline .links-baseline.json` — schreibt genau das `--json`-Format (Liste von Objekten mit `file`, `line`, `type`, `target`, `reason`), Datei einchecken.
2. Im CI `--baseline .links-baseline.json` mitgeben: Funde aus der Baseline werden als `known` gezählt und beeinflussen den Exit-Code nicht, alles Neue führt zu `1`.

Identitätsschlüssel eines Funds ist **`(file, type, target)` — ohne `line`**. Zeilennummern verschieben sich bei jeder Textänderung im Markdown; wären sie Teil des Schlüssels, würde jeder bekannte Fund nach einer harmlosen Absatzänderung als neu gemeldet und das Gate grundlos rot. `line` steht in der Baseline nur zur Orientierung.

---

**Keywords:** markdown, broken_links, images, documentation, validation, linkcheck, ci

**Dependencies:** python3, Bash, Read, Write, Glob
