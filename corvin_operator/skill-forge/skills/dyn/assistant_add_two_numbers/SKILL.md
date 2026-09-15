---
name: assistant_add_two_numbers
description: Addiert exakt zwei Zahlen dezimalgenau und dient zugleich als Referenzbeispiel für minimale Skill-Struktur.
---

# add-two-numbers

## Zweck und Prämisse
Zwei Operanden, eine Operation. Der Mehrwert ist **nicht** das Addieren an sich, sondern (a) exakte Dezimalarithmetik via `Decimal` statt `float` und (b) ein bewusst minimales Referenzbeispiel für Skill-Struktur. Beide Prämissen gehören in die erste Zeile des SKILL.md-Bodys.

## Wann triggern — und wann nicht
Triggern **nur**, wenn zwei konkrete numerische Literale genannt sind und eine Summe gefragt ist ("addiere 3.5 und -2", "was ist 0.1 plus 0.2 exakt").
Nicht triggern bei: Summen über Datenmengen ("Summe der Spalte"), mehr als zwei Operanden, Ausdrücken mit anderen Operatoren, metaphorischem "plus". Diese Negativliste explizit in die `description` aufnehmen — sie liegt in jeder Session im Kontext und muss scharf sein.
Bei drei oder mehr Zahlen: nicht scheitern, sondern sagen, dass der Skill genau zwei Operanden abdeckt, und die Summe direkt nennen.

## Layout
Installation nach `~/.claude/skills/add-two-numbers/` (nur dort und in `<projekt>/.claude/skills/` wird gescannt). Entwicklung darf in `~/projects/claude-playground/skills/add-two-numbers/` liegen, dann per Symlink verlinken.

```
add-two-numbers/
  SKILL.md
  scripts/add.py   # chmod +x, Shebang #!/usr/bin/env python3
```

## SKILL.md
Frontmatter: `name: add-two-numbers`, `description:` mit Triggern **und** Negativliste. Body: Prämisse, Aufrufmuster, vier Beispiele (Ganzzahl, Dezimal, negativ, Komma-Eingabe), Fehlerverhalten.

## scripts/add.py — verbindliche Vorgaben
1. **Kein `argparse`.** Manuelles `sys.argv[1:]`-Parsing, sonst wird `-3` als Option gelesen und das dokumentierte Negativ-Beispiel bricht.
2. **`Decimal`, nicht `float`.** `0.1 + 0.2` muss `0.3` ergeben.
3. **Eingabe-Normalisierung:** führende/folgende Leerzeichen strippen; ein Dezimalkomma (`3,5`) wird zu `3.5`, aber nur bei genau einem Komma und keinem Punkt — Tausendertrennzeichen bleiben ein Fehler.
4. **Ablehnen:** `nan`, `inf`, `-inf`, leere Strings, alles was `InvalidOperation` wirft. Meldung auf stderr, Exit 2.
5. **Argumentzahl ≠ 2:** stderr-Meldung mit Usage-Zeile, Exit 2.
6. **Ausgabe-Vertrag:** `format(summe, 'f')` — **nie** `normalize()`. Damit gilt in *jedem* Fall: keine Exponentialschreibweise; die Skalierung der `Decimal`-Addition bleibt erhalten (das Ergebnis hat so viele Nachkommastellen wie der genauere Operand). Nachlaufende Nullen werden also **nicht** gekürzt: `19.99 + 4.01` ergibt `24.00`, nicht `24`. Ganzzahlige Operanden liefern ganzzahlige Ausgabe (`7`, nicht `7.0`), weil beide Skalierung 0 haben — das ist Folge des Vertrags, keine Zusatzregel.
7. **Ausgabe-Trennzeichen ist immer der Punkt**, auch wenn die Eingabe ein Komma verwendet hat. Bewusste Entscheidung: maschinenlesbar und konsistent mit der internen `Decimal`-Repräsentation. Diesen Satz wörtlich in SKILL.md aufnehmen, damit `24.00` bei Komma-Eingabe als korrekt und nicht als Bug gilt.
8. Erfolg = Exit 0, Summe als einzige Zeile auf stdout.

## Aufrufmuster (so dokumentieren)
```
python3 scripts/add.py 3 4            # -> 7
python3 scripts/add.py 0.1 0.2        # -> 0.3
python3 scripts/add.py -- -3 4        # -> 1
python3 scripts/add.py 19,99 4,01     # -> 24.00
```
`python3`, nie `python`. Das `--` im Negativ-Beispiel zeigen, auch wenn das Parsing ohne auskommt — es macht die Shell-Semantik explizit.

## Verifikation vor Abgabe
Alle vier Aufrufe oben ausführen. Der vierte ist nicht optional: er ist der einzige Fall, der nachlaufende Nullen, ein Ergebnis ≥ 10 und die Komma-Eingabe zugleich belastet — also genau die Konstellation, an der eine mechanismus-basierte Formatierung kippt. Dazu die Fehlerpfade: ein Argument, drei Argumente, `abc`, `nan`. Exit-Codes und Ausgaben mit der Doku vergleichen; Abweichungen im Skript korrigieren, nicht in der Doku wegdefinieren.

## Scope-Grenze
Keine weiteren Operationen ergänzen. Kommt der Wunsch nach Subtraktion oder Ausdrucksauswertung, ist das ein neuer Skill mit eigenem Namen — dieser hier wird nicht umgebaut.

---

**Keywords:** addition, summe, zwei zahlen, decimal, exakte arithmetik, skill referenzbeispiel

**Dependencies:** python3, Bash, Write
