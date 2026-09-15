---
name: assistant_lint_yaml
description: Prüft YAML-Dateien deterministisch auf doppelte Schlüssel und Einrückungsfehler und berichtet Funde als Text oder JSON.
---

# YAML-Linter: doppelte Schlüssel & Einrückung

## Wann anwenden
- **Duplikate:** „prüfe die YAML", „duplicate keys", „warum wird mein Wert überschrieben".
- **Einrückung:** „Block landet nicht unter dem Parent", „Compose-Datei wird nicht akzeptiert", jede Frage nach Struktur oder Verschachtelung.
- Außerdem vor Commits an `*.yml`/`*.yaml` und bei Compose-, K8s- oder CI-Dateien.

YAML nie im Kopf parsen — immer per Bash aufrufen.

## Kapazitätsmatrix: was kann welcher Zweig?

| | Zweig 1 (`check_yaml.py`) | Zweig 2 (yamllint) |
|---|---|---|
| Regelabdeckung (Duplikate + Einrückung) | ja | ja |
| Text-Ausgabe | ja | ja (`-f parsable`) |
| Exit-Code-Gate (`0` sauber / `1` Funde) | ja | ja |
| Natives JSON (`--format json`) | ja | **nein** |

Die Zweige sind **nur in der Regelabdeckung äquivalent** — nicht im Ausgabe-Vertrag. Die Prüfung fällt nie aus, weil Zweig 1 fehlt; ein **JSON-Auftrag** kann jedoch nur eingeschränkt erfüllt werden.

## Skript-Pfad ermitteln (einmal pro Session)
Bash startet im Projektverzeichnis, nicht im Skill-Verzeichnis. Pfad suchen, Fehler nicht unterdrücken:
```bash
CHECK_YAML=$(find ~/.claude /tmp /opt -maxdepth 6 -name check_yaml.py -type f | head -1)
echo "gefunden: ${CHECK_YAML:-KEINS}"
```

## Werkzeugwahl
1. **`check_yaml.py`** (Default, braucht PyYAML) — nur bei nicht-leerem `$CHECK_YAML`:
   ```bash
   python3 "$CHECK_YAML" PFAD [PFAD...] --strict [--max-findings N] [--format text|json]
   ```
   `--strict` ist Normalfall. Exit: `0` sauber · `1` Funde · `2` interner Fehler/fehlende Dependency (Stderr lesen, dann Zweig 2).
2. **yamllint** — bei leerem `$CHECK_YAML`, Exit 2 oder fehlendem PyYAML:
   ```bash
   yamllint -f parsable -d '{extends: default, rules: {key-duplicates: enable, indentation: {spaces: consistent, indent-sequences: consistent}}}' PFAD
   ```
3. Fehlen beide: melden „YAML-Parsing nicht verfügbar, bitte `pip install yamllint`" und **nicht** raten. Das Verbot betrifft ausschließlich das **Parsen von YAML** — Regex, Eigenbau-Parser, erfundene Befunde.

## Wenn JSON gefordert ist, aber nur Zweig 2 läuft
Das ist ein **meldepflichtiger Zustand, kein stiller Fallback**. Reihenfolge:
1. Remediation zuerst anbieten: `pip install PyYAML` reaktiviert Zweig 1 und damit natives JSON.
2. Lehnt der Nutzer ab oder ist Installation unmöglich: `-f parsable` (`datei:zeile:spalte: [level] message (rule)`) **darf** deterministisch nach JSON konvertiert werden — das ist Formatumwandlung, kein YAML-Parsing, und ausdrücklich erlaubt. Fehlende Felder (`first_line`/`first_column`) als `null` setzen, nicht erfinden.
3. Für ein reines CI-Gate genügt yamllints Exit-Code (`0` sauber / `1` Funde) — ohne jede Konvertierung.
Die Einschränkung dem Nutzer immer offenlegen: welcher Zweig lief, dass JSON konvertiert (nicht nativ) ist.

## Ausgabe interpretieren
- `error` = doppelter Schlüssel oder Parse-Fehler; Duplikate nennen Duplikat **und** Erstauftreten.
- `warning` = Stil/Einrückung oder YAML-1.1-Alias (`yes:`/`true:`).
- `skipped (template)` = Helm/Jinja: kein Befund, aber keine Freigabe.
- `Parse-Fehler — Duplikatprüfung übersprungen` = Datei **ungeprüft**, nie als sauber darstellen.

**Nur Zweig 1:** `--format json` gibt ausschließlich ein Array auf Stdout (sauber: `[]`), Diagnostik nach Stderr. Felder: `file`, `line`, `column` (1-basiert, bei Dateibefunden `0`), `level`, `rule` (`key-duplicates`, `indentation`, `parse-error`, `yaml11-alias`, `template-skipped`, `truncated`), `message`, `first_line`/`first_column` (nur bei Duplikaten, sonst `null`). Bei Abschnitt ist das letzte Element `rule: "truncated"` mit `remaining` — kein Befund. Unbekannte Felder ignorieren.

## Ergebnis berichten
Zusammenfassungszeile (`N Fehler, M Warnungen, K Dateien übersprungen`), darunter Fundstellen sortiert nach Datei, Zeile, Spalte. Bei Duplikaten beide Zeilennummern. Zweig nennen, falls nicht Zweig 1. Rohes JSON nur auf Wunsch. Keine Datei ohne Rückfrage ändern — Fixes vorschlagen, nicht anwenden.

---

**Keywords:** yaml, linter, duplicate-keys, indentation, yamllint, pyyaml, validation, pre-commit, json-output, schema

**Dependencies:** Bash, Read, Glob, Write
