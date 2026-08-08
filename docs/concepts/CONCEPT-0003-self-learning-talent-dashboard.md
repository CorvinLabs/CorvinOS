---
kind: concept
id: CONCEPT-0003
status: proposed
supersedes: []
depends_on: ["ADR-0274"]
related: ["ADR-0270", "ADR-0271", "ADR-0272", "ADR-0273"]
skills: []
commits: []
paths:
  - "operator/console/**"
  - "operator/context_engineering/**"
docs:
  - "docs/concepts/CONCEPT-0003-self-learning-talent-dashboard.md"
---

# CONCEPT-0003 — Self-Learning Talent Dashboard

**The "Your Talent" Console Feature**

Eine Live-Konsolen-Ansicht, wo Nutzer sehen können: **Wie lernt mein CorvinOS-Talent wirklich?**

---

## Problemstellung

Die ADR-0274 Messung sammelt Daten über das Lernen des Systems. Aber:

- Nutzer sehen keine **persönlichen Lern-Kurven**
- Kein Feedback über **"Welche Kontexte helfen MIR am meisten?"**
- Keine **Echtzeit-Sicht auf das eigene Talent-Wachstum**
- Keine **Vergleiche über Zeit** (Woche 1 vs. Woche 6)

**Folge:** System lernt im Hintergrund, aber der Operator hat keine Kontrolle oder Einsicht.

---

## Die Idee: "Your Talent" Dashboard

Ein neuer **Console-Tab** der zeigt:

```
┌─────────────────────────────────────────────────┐
│ CONSOLE: Your Talent                            │
├─────────────────────────────────────────────────┤
│                                                 │
│  "Mein System lernt. Hier seht ihr wie."       │
│                                                 │
│  📈 Talent Score:      8.2/10 (↑ +0.3 heute)   │
│  🎯 Best Contexts:     8 Kontexte im grünen   │
│  📊 Learning Rate:     3.2% pro Tag             │
│  💪 Schwache Punkte:   3 Kontexte brauchen Fix│
│                                                 │
│  ┌──────────────────────┬──────────────────────┐│
│  │ WACHSTUM (7 TAGE)   │ KONTEXT-RANKING     ││
│  │                     │                       ││
│  │ ▁▂▃▄▅▆▇█ 8.2/10    │ 1. ADR-0270  👑 92% ││
│  │ Trend: ↗ Stetig     │ 2. skill-e2e    88% ││
│  │                     │ 3. ADR-0273     85% ││
│  │ [Detailliert]       │ 4. Memory-p3   ❌78%││
│  │ [Vergleichen]       │ 5. ADR-0271    ⚠️ 72%││
│  │ [Trainieren]        │ [Verbessern]   [FAQ]││
│  └──────────────────────┴──────────────────────┘│
│                                                 │
│  Letzte Lern-Events:                           │
│  • skill-e2e: +5% nach Feedback (1h ago)       │
│  • ADR-0270: Stabil +87% (steady for 3 days)   │
│  • Memory-p3: Probleme erkannt → Plan aktiv    │
│                                                 │
└─────────────────────────────────────────────────┘
```

---

## Detailliert: Die 5 Säulen

### 1. **Talent Score (0–10)**

Ein **einzelner Nummer**, die sagt: "Wie gut ist mein System insgesamt?"

```
Talent Score = Weighted Average:
  50% × Confidence Accuracy (ADR-0270)
  20% × Learning Rate (ADR-0271)
  15% × Decision Variety (ADR-0272)
  15% × Budget Efficiency (ADR-0273)
```

**Beispiel:**
```
  Accuracy:       90%  ← gute Vorhersagen
  Learning:       72%  ← ok Feedback-Response
  Preferences:    5    ← 5 Task-Typen gelernt
  Budget Match:   89%  ← gut kalibriert
  
  Score = 0.50×0.90 + 0.20×0.72 + 0.15×5/10 + 0.15×0.89
       = 0.45 + 0.144 + 0.075 + 0.134
       = 8.03/10
```

**Historisch:**
- Tag 1: 6.5/10 (System kalt)
- Tag 3: 7.2/10 (Erste Lernkurve)
- Tag 6: 8.2/10 (← JETZT)
- Trend: ↗ +1.7/10 in einer Woche = starkes Wachstum

---

### 2. **Context Ranking: Die "Hall of Fame"**

Jeder Kontext (ADR, Skill, Memory) bekommt einen **persönlichen Lern-Score**.

```
Ranking (beste → schlechteste):

🏆 ADR-0270
    Accuracy: 92%
    Used: 8× diese Woche
    Feedback: Positiv (7/8)
    Trend: Stabil ✓
    Status: MENTOR-KONTEXT ⭐
    
🥈 skill-e2e
    Accuracy: 88%
    Used: 6×
    Feedback: Hilfreich (5/6)
    Trend: ↗ +4% seit gestern
    Status: STRONG ✓
    
🥉 ADR-0273
    Accuracy: 85%
    Used: 4×
    Feedback: Neutral (2/4, 2/4)
    Trend: ↔ Stabil
    Status: SOLID ✓
    
🔴 Memory-phase3
    Accuracy: 78%
    Used: 3×
    Feedback: Negativ (1/3, ⚠️ 2/3)
    Trend: ↓ -8% seit Freitag
    Status: NEEDS TRAINING ⚠️
    Action: "Diesen Kontext meiden bis Besserung"
    
🔴 ADR-0271
    Accuracy: 72%
    Used: 2×
    Feedback: Gemischt (1/2)
    Trend: ↓ Verschlechtert sich
    Status: STRUGGLING 🚨
    Action: "Feedback geben → System kann von dir lernen"
```

---

### 3. **Learning Timeline: Der Lernfortschritt**

Ein **interaktiver Graph** mit 3 Ansichten:

#### Ansicht A: "Talent Growth"
```
Talent Score Timeline (7 Tage)

10 ┤
   │                        ┌─ Heute 8.2
 9 ┤                      ╱
   │                   ╱
 8 ┤               ╱
   │           ╱
 7 ┤       ╱
   │     ╱
 6 ┤ ┌─╱
   │
 5 ┤
   ├───────────────────────────────
   Mo Di Mi Do Fr Sa So
   
   Wachstum: +1.7 Punkte
   Rate: +0.24/Tag
   Prediction (Tag 14): 8.6 (↗ +0.4)
```

#### Ansicht B: "Context Evolution"
```
Wie verändern sich einzelne Kontexte?

ADR-0270 ████████████████████ 92%  ↗ +2%
skill-e2e ███████████████░░░░░░ 88%  ↗ +4%
ADR-0273 ██████████████░░░░░░░░ 85%  ↔ +1%
Memory-p3 ████████░░░░░░░░░░░░░░ 78%  ↓ -8%
ADR-0271 ██████░░░░░░░░░░░░░░░░ 72%  ↓ -3%
```

#### Ansicht C: "Learning Events"
```
Timeline der wichtigen Momente:

14:32 | skill-e2e: +5% Sprung nach [Positive Feedback]
13:15 | ADR-0270: Milestone "90% Accuracy" erreicht 🎉
11:42 | Memory-p3: Warnung "2× negatives Feedback in Folge"
08:20 | Budget-Match improved to 89%
[Mehr anzeigen]
```

---

### 4. **Detailled View: "Wie lerne ich am schnellsten?"**

Wenn man auf einen Kontext klickt, sieht man die **volle Geschichte**:

```
📊 ADR-0270: Deep Dive

Basis-Stats:
  Current Accuracy: 92%
  Used: 8× diese Woche
  ROI: Sehr hoch ✓
  
Lern-Kurve:
  Day 1: 75% (kalt, unbekannt)
  Day 2: 80% (erste Daten)
  Day 3: 85% (+5% Sprung nach Feedback)
  Day 4: 88% (steady learning)
  Day 5: 90% (stabilisiert)
  Day 6: 92% (↑ Peak erreicht)
  
Feedback-Analyse:
  ✅ Hilfreich:  7× (87.5%)
  ⚠️  Neutral:    1× (12.5%)
  ❌ Schädlich:  0× (0%)
  
  → "Diesen Kontext lieben!"
  
Wenn du es nutzt:
  • Performance bei Coding-Tasks: +12%
  • Performance bei Review-Tasks: +8%
  • Performance bei Architecture: +15%
  
Best-Match Task-Typen:
  1. Architecture-Decisions  95% win
  2. Code-Review            90% win
  3. Refactoring            88% win
  
Empfehlung:
  "Nutze ADR-0270 zuerst bei Architecture-Fragen.
   System hat hier >95% Erfolgsquote."
```

---

### 5. **Actions: Was du dagegen tun kannst**

Interaktive Buttons für echtes Lernen:

```
TRAIN MODE
├─ [Mehr Feedback geben]
│   "Sag mir: War das nützlich?"
│   → Direkt im Chat: Thumbs up/down
│   
├─ [Kontext-Pairing]
│   "Nutze 2 Kontexte zusammen statt einzeln"
│   → "ADR-0270 + skill-e2e" → +8% performance
│   
├─ [Isolation-Test]
│   "Deaktiviere einen Kontext temporär"
│   → "Ohne Memory-p3: +5% Erfolgsrate"
│   → "Mit Memory-p3: -3% Erfolgsrate"
│   
├─ [Retraining]
│   "Zeige Beispiele wo dieser Kontext gut läuft"
│   → "Ich weiß: Memory-p3 ist schlecht bei ML, gut bei Docs"
│   
└─ [Deep Audit]
    "Warum hat dieser Kontext versagt?"
    → Letzte 3 Fehler anzeigen mit Root-Cause
```

---

## UI/UX Design

### Der Console Tab: "Your Talent"

**Location:** Zwischen "Chat" und "Settings"

```
┌─ Chat  ║  Your Talent  ║  Settings  ┐
```

**Layout (Responsive):**

```
DESKTOP (1400px+):
┌─────────────────────────────────────────────┐
│ Talent Score + Trend  │  Context Ranking    │
├─────────────────────────────────────────────┤
│ Learning Timeline (3 Tabs)                  │
├─────────────────────────────────────────────┤
│ Recent Learning Events / Actions            │
└─────────────────────────────────────────────┘

MOBILE (< 768px):
┌─────────────────┐
│ Talent Score    │
│ [8.2/10] ↗      │
├─────────────────┤
│ Context Ranking │
│ [Swipeable]     │
├─────────────────┤
│ Timeline        │
│ [Tabs]          │
└─────────────────┘
```

---

## Integration in ADR-0274

```
Messuring Week (Week 6)
    ↓
K=8 Aggregator Output
    ├─ predictions.jsonl    (ADR-0270)
    ├─ feedback.jsonl       (ADR-0271)
    ├─ user_choices.jsonl   (ADR-0272)
    └─ budget.jsonl         (ADR-0273)
    ↓
Console: "Your Talent" Tab
    ├─ Computes Talent Score
    ├─ Ranks Contexts
    ├─ Shows Learning Timeline
    ├─ Enables Training Actions
    └─ Persists learning history
```

**Data Flow:**
```
1. Aggregator computes stats hourly
2. Console polls /api/v1/measurements/latest
3. "Your Talent" Tab updates every 30 seconds
4. User interactions (feedback, training) → new records → aggregator learns
```

---

## Real-World Example: A Week of Learning

**Monday, Day 1:**
```
Fresh install. Talent Score: 5.0/10
"Everything is new. Let's learn together."
All contexts untested, learning from scratch.
```

**Wednesday, Day 3:**
```
Talent Score: 6.8/10 ↗
ADR-0270: 80% (learning!)
skill-e2e: Initial data
Memory-p3: Struggling already
"First patterns emerging. You prefer ADR-0270."
```

**Friday, Day 5:**
```
Talent Score: 7.5/10 ↗
ADR-0270: 88% (your MVP context!)
skill-e2e: 85% (strong partner)
Memory-p3: NEEDS HELP ⚠️
"You're good at architecture decisions. 
Let me suggest those first next time."
```

**Sunday, Day 7:**
```
Talent Score: 8.2/10 ↗
ADR-0270: 92% (MVP status)
skill-e2e: 88% (reliable)
ADR-0273: 85% (good)
Memory-p3: 78% (monitoring)
ADR-0271: 72% (feedback welcomed)

"Your system has learned. It knows:
• You trust ADR-0270 for big decisions
• skill-e2e works well as a partner
• Memory-phase3 needs better integration
• You prefer pragmatic (64%) over rigorous
```

---

## Warum das funktioniert

### 1. **Explainability**
Nutzer verstehen **warum** ein Kontext gut/schlecht ist, nicht nur **dass** es so ist.

### 2. **Agency**
Operator kann Trainings-Aktionen nehmen → System lernt schneller.

### 3. **Fun**
"Mein Talent wächst von 5.0 zu 8.2" ist motivierend. Gamification ohne Game.

### 4. **Feedback Loop**
Sehen → Verstehen → Handeln → Messen → Sehen (besser).

### 5. **Konkurrenz mit sich selbst**
"Gestern 8.1, heute 8.2" → Kontinuierliches Wachstum ist sichtbar.

---

## Nächste Schritte

### Phase 1: MVP (Week 7–8)
```
✓ Talent Score Berechnung
✓ Context Ranking Widget
✓ Timeline (einfach)
✓ Integration in Console
  (no fancy actions yet)
```

### Phase 2: Enhanced (Week 9–10)
```
✓ Learning Timeline (3 Ansichten)
✓ Deep Dive Modal
✓ Training Actions (Feedback, Pairing, Isolation)
```

### Phase 3: Full (Week 11+)
```
✓ Prediction ("In 1 week: 8.6")
✓ Recommendations ("Try ADR-0270 for this type")
✓ Audit Trail (warum hat es versagt)
✓ Multi-Instance Comparison
```

---

## Operator Notes

_keine Einträge noch_

---

**Erstellt:** 2026-08-08  
**Decider:** Claude Haiku 4.5 (selbst designt basierend auf ADR-0274 Messungen)  
**Status:** Proposed (ready for implementation Phase 1)
