# Getting Started: Academic Paper Generation

**Situation:** You have concepts and ADRs. You want a production-ready academic paper for arXiv/journals.

**Timeline:**
- Quick workflow: 2-4 hours (use `paper-generator-orchestrator`)
- Manual workflow: 10-18 hours (use `academic-paper-generation` + manual refinement)

---

## 1. Choose Your Workflow

### Fast Track: Automated (2-4 hours)
Use **`paper-generator-orchestrator`** skill if:
- ✓ You have clear concepts/ADRs to work from
- ✓ You want a complete draft in a day
- ✓ You're okay with agent-generated content that you refine afterward
- ✓ Output goal: arXiv/conference submission draft

**Best for:** Time-constrained projects, conference deadlines, initial drafts

### Deliberate Track: Methodical (10-18 hours)
Use **`academic-paper-generation`** skill step-by-step if:
- ✓ You want full control over narrative arc
- ✓ You're crafting a flagship paper that will be heavily cited
- ✓ You want to deeply integrate your own research
- ✓ Output goal: Polished journal submission or monograph

**Best for:** Long-form papers, groundbreaking research, publications that need to stand the test of time

---

## 2. Prepare Your Inputs

### For Either Track

**Required:**
- [ ] Concept files (`.md` format, in single directory)
  - Example: `/projects/my-paper/concepts/*.md`
  - Each file: 500-2000 words describing an idea/principle

- [ ] ADR files (if available, `.md` format)
  - Example: `/projects/Corvin-ADR/decisions/*.md`
  - These become "design decisions" section of paper

- [ ] Paper metadata:
  ```
  Title: "Structural Enforcement of Regulatory Compliance"
  Authors: 
    - "Silvio Jurk (Corvin Labs, Switzerland)"
    - "René de la Barre (Corvin Labs, Switzerland)"
  Scope: full  # or: short (4-8 pages), xfull (20+ pages)
  Venue: arxiv  # or: icml, neurips, ieee, acm
  ```

**Optional:**
- [ ] Reference list (CSV with URLs—saves time in Phase 1)
- [ ] Existing figures/diagrams (SVG source files)
- [ ] Prior paper drafts (for narrative continuity)

---

## 3. Fast Track: Run Orchestrator

```bash
/paper-generator-orchestrator \
  --concepts /path/to/concepts/*.md \
  --adrs /path/to/adrs/*.md \
  --title "Your Paper Title" \
  --authors "Author One (Affiliation)" "Author Two (Affiliation)" \
  --scope full \
  --venue arxiv \
  --output /path/to/output
```

**Expected timeline:**
- Phase 1 (Research): 30 min
- Phase 2 (Narrative): 15 min
- Phase 3 (Content): 60 min
- Phase 4 (Figures): 45 min
- Phase 5 (Assembly): 15 min
- **Total: ~2.5 hours**

**Artifacts generated:**
```
output/
  ├── paper.pdf            (final, production-ready)
  ├── paper.tex            (LaTeX source)
  ├── paper.bib            (BibTeX references)
  ├── figures/
  │   ├── *.svg            (diagram sources)
  │   └── *.pdf            (compiled diagrams)
  └── REPORT.md            (documentation of choices)
```

**Next step:** Read `REPORT.md`, review `paper.pdf`, make requested refinements.

---

## 4. Deliberate Track: Phase-by-Phase

If using `academic-paper-generation`:

### Phase 1: Research & References (2-4 hours)

**Goal:** Establish the paper's core claim and gather 40+ references.

**Action steps:**

```
1. Read all concept files
2. Extract central insight (one sentence)
3. Dispatch research agents (or manual search):
   - Academic papers on [topic]
   - Regulatory documents
   - Industry incidents
   - Competitive/related work
4. Build reference database (CSV):
   category | author | year | title | url | relevance
5. Validate: 40+ refs, ≥3 per section, all URLs live
```

**Deliverable:** `references.csv` + one-paragraph problem statement

**Quality gate:** Every major section has allocated references. No missing.

---

### Phase 2: Narrative Arc Design (1-2 hours)

**Goal:** Create a clear, compelling story.

**Action steps:**

```
1. Map narrative arc:
   Problem → Solution → Proof → Implications
2. Create outline with section titles + word counts
3. Allocate diagrams (1-2 per major section)
4. Validate: Can readers understand claim from headings alone?
5. Check: IMRAD structure (Intro 40%, Methods 25%, Results 35%)
```

**Deliverable:** One-page outline + narrative flow diagram

**Quality gate:** Reader understands the paper's claim from section headings alone.

---

### Phase 3: Content Generation (4-8 hours)

**Goal:** Write sections with sufficient depth (3-5 paragraphs each).

**Action steps:**

```
1. Write/generate each major section:
   - Introduction (800-1200 words)
   - Methodology (600-1000 words)
   - Each mechanism/result (200-300 words)
   - Related Work (400-600 words)
   - Conclusion (300-400 words)

2. Per-mechanism template:
   - Regulatory basis (cite article)
   - Current practice fails (why)
   - Structural solution (how/why structural)
   - Verification (concrete method)

3. Integrate citations (every section ≥3)
4. Remove placeholders (\cite{...} → actual references)
5. Add concrete examples/scenarios (not hypothetical)
```

**Deliverable:** Complete draft with all sections written

**Quality gate:** 
- No \cite{} placeholders
- Minimum 3 paragraphs per major section
- All citations researched
- Examples concrete, not hypothetical

---

### Phase 4: Figures & Diagrams (2-3 hours)

**Goal:** Create 4-6 publication-quality diagrams.

**Action steps:**

```
1. Define diagram specs:
   - Threat Model (flow chart)
   - Mechanisms Overview (matrix/table)
   - Architecture Diagram (if applicable)
   - Timeline/Sequence (if applicable)
   - Implementation Example (if applicable)

2. Create as SVG (source) + PDF (for paper)
   Tool options: TikZ, Graphviz, draw.io, Inkscape

3. Write captions:
   "Figure N: Title. Description. Why it matters for narrative."
   (15-25 words, every symbol defined)

4. Integrate into text:
   "As shown in Figure~\ref{fig:threat_model}, ..."

5. Validate:
   - All diagrams are PDF (not PNG)
   - All have captions
   - All are referenced in text
```

**Deliverable:** 4-6 diagrams (SVG + PDF) with captions

**Quality gate:**
- Every diagram is in PDF format
- Every diagram has a caption explaining narrative relevance
- Every diagram is referenced in text

---

### Phase 5: LaTeX Assembly & Compilation (1-2 hours)

**Goal:** Produce final PDF suitable for submission.

**Action steps:**

```
1. Use template (from skill):
   \documentclass[11pt]{article}
   \usepackage{arxiv}
   ... [boilerplate]

2. Assemble LaTeX file:
   - Metadata (title, authors, date)
   - Abstract (standalone, 150-200 words)
   - All sections (with \cite{} references)
   - All \includegraphics{} for figures

3. Convert references to BibTeX:
   @article{AuthorYear,
     author = "...",
     title = "...",
     journal = "...",
     year = "2024",
     url = "..."
   }

4. Compile:
   tectonic paper.tex
   bibtex paper
   tectonic paper.tex
   tectonic paper.tex

5. Validate:
   - pdfinfo paper.pdf (check metadata)
   - Visual inspection (no typos, formatting clean)
   - Page count reasonable
   - All figures render
   - No undefined references
```

**Deliverable:** `paper.pdf` (production-ready)

**Quality gate:**
- PDF compiles without errors
- All figures render
- No undefined citations
- Page count appropriate

---

## 5. After Generation: Refinement Cycle

Regardless of track, after you have a draft:

### Read & Critique (30-60 min)

```
1. Read abstract — does it stand alone?
2. Read introduction — is the problem clear?
3. Skim results — do figures support claims?
4. Read conclusion — does it restate thesis?
5. Note: What feels thin? What needs depth?
```

### Refine Content (1-4 hours)

```
1. Add examples where thin sections exist
2. Strengthen weak narrative transitions
3. Deepen related work section
4. Add ablations (if results-focused)
5. Write limitations paragraph (if not present)
```

### Polish Figures (1-2 hours)

```
1. Check all captions for clarity
2. Verify colorblind accessibility (use Color Oracle tool)
3. Ensure all labels are crisp (no blurry text)
4. Add missing references to text
5. Recompile SVG → PDF if needed
```

### Final Checks (30 min)

```
1. Proofread for typos
2. Verify all citations are formatted consistently
3. Check margins and page count
4. Verify author names/affiliations are correct
5. Test PDF hyperlinks (if URLs present)
```

---

## 6. Common Pitfalls & Fixes

| Issue | Symptom | Fix |
|-------|---------|-----|
| Thin content | Sections are 1-2 sentences | Add 2-3 more paragraphs per section |
| Orphaned figures | Figures exist but aren't referenced | Add text reference: "As shown in Figure 2..." |
| Bad narrative | Readers can't explain claim | Rewrite introduction; add problem statement |
| PNG artifacts | Figures look blurry | Reconvert from SVG to PDF via Inkscape |
| Undefined citations | LaTeX warnings about missing refs | Regenerate .bbl from .bib via bibtex |
| Weak voice | Text is full of hedges ("could potentially...") | Rewrite in active voice; remove hedges |

---

## 7. Where to Go for Help

**For Learning:**
- Read `/home/shumway/.claude/skills/academic-paper-generation/SKILL.md` (the guide)
- Read `/home/shumway/.claude/skills/academic-paper-writing-reference.md` (quick reference)

**For Automation:**
- Use `/paper-generator-orchestrator` skill (end-to-end)
- Or use `/academic-paper-generation` skill phase-by-phase

**For Tools:**
- LaTeX: Overleaf (online), Tectonic (CLI)
- Figures: TikZ, Graphviz, draw.io, Inkscape
- References: Mendeley, Zotero, DBLP
- Writing: Grammaly, Hemingway Editor

**For Submission:**
- arXiv: https://info.arxiv.org/help/submit
- ICML: https://icml.cc/Conferences/2024/
- NeurIPS: https://nips.cc/

---

## Summary: Decision Tree

```
START
  ↓
Have clear concepts/ADRs?
  YES → Have time for full manual control?
          NO → Use /paper-generator-orchestrator (2-4 hrs)
          YES → Use /academic-paper-generation (10-18 hrs)
  NO → Write concepts first (1-2 weeks of research)
```

**Key insight:** The orchestrator is fastest but requires refinement. The methodical approach gives full control but takes longer. Choose based on your time and perfectionism balance.

---

## One More Thing

**The most important principle:** A paper with a clear narrative arc, sufficient depth, and publication-quality figures will be read, cited, and respected.

Papers fail not because they're wrong, but because they're:
- Unclear (no one understands the claim)
- Thin (no depth; feels like a checklist)
- Unprofessional (figures are blurry; text has typos)

The skills in this guide prevent all three. Use them.

---

**Good luck writing. Remember: you're not writing for reviewers. You're writing for readers who will build on your work.**

