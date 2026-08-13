# Academic Paper Writing: Synthesized Best Practices Reference

**Generated:** 2026-08-13 via research agent  
**Sources:** arXiv guidelines, ICML/NeurIPS templates, empirical citation analysis  
**Scope:** Publication-ready standards for academic papers

---

## Quick Reference: Citation Density by Section

| Section | % of Citations | Role | Typical Word Count |
|---------|-----------------|------|-------------------|
| **Introduction** | **41.8%** (highest) | Establish landscape + motivation | 10-15% of paper |
| **Methods** | 25.2% | Cite key prior techniques | 15-20% of paper |
| **Results** | 25.9% | Minimal; let data speak | 40-50% of paper |
| **Discussion** | 7% (lowest) | Interpret + implications | 15-20% of paper |

**Key Insight:** Weak papers over-cite methods without grounding. Strong papers front-load citations in intro, then minimize in results.

---

## Abstract Template (250-300 words, 0 citations)

```
[1-2 sentences, 30-40 words]
Context & motivation. Why does this problem matter?

[1-2 sentences, 40-50 words]
Gap or limitation. What existing work misses?

[1-2 sentences, 50-60 words]
Main contribution(s). What did you do differently?

[1-2 sentences, 40-50 words]
Results (quantitative if possible). What did you find?

[1 sentence, 30-40 words]
Implications. Why does this matter for the field?
```

---

## Content Density for 8-10 Page Paper

| Section | Word Count | % of Total | Pages |
|---------|-----------|-----------|--------|
| Abstract | 250-300 | 3% | 0.5 |
| Introduction | 800-1,000 | 12% | 1-1.5 |
| Related Work | 600-800 | 8% | 1 |
| Methods | 1,200-1,500 | 18% | 1.5-2 |
| Results | 2,500-3,500 | 35-40% | 3.5-4.5 |
| Discussion | 800-1,200 | 12% | 1-1.5 |
| Conclusion | 400-500 | 6% | 0.5-1 |
| References | — | — | (separate) |

**Rule of thumb:** 250 words ≈ 1 double-spaced page (12pt Times New Roman, 1" margins)

---

## Figure Best Practices

**Format Decision Matrix:**

| Content | Format | Resolution | Tool Examples |
|---------|--------|-----------|------------------|
| Graphs, flowcharts, trees | PDF/EPS (vector) | N/A | TikZ, Graphviz, Matplotlib pgf backend |
| Plots (scatter, lines, bars) | PDF (vector) | N/A | Matplotlib, Plotly (PDF export) |
| Photographs, heatmaps | PNG/TIFF | 300+ DPI | Direct raster export |
| Diagrams with layers | SVG → PDF | N/A | Inkscape (export as PDF) |

**Caption Quality:**
- 15-25 words for simple figures, up to 50 for complex ones
- Structure: `Figure N: Title. Description with all symbols/abbreviations defined.`
- Every symbol explained; error bars clearly indicated (CI % specified)
- Never refer to "above/below"—use figure numbers

**Design for Print:**
- Sans-serif fonts 6-12pt after reduction
- Line weights ≥0.5pt
- Colorblind-safe palettes (use Matplotlib `tab10` or Paul Tol schemes)
- Use line style + markers + hatching (not color alone) for distinction

---

## LaTeX + BibTeX Essentials

**Recommended Boilerplate:**
```latex
\documentclass[11pt]{article}
\usepackage[utf8]{inputenc}
\usepackage{arxiv}
\usepackage{amsmath, amssymb, graphicx, hyperref}
\usepackage{natbib}

\bibliographystyle{plainnat}  % or abbrvnat

\hypersetup{
  colorlinks=true,
  linkcolor=blue,
  urlcolor=blue,
  citecolor=blue
}

\title{Your Title}
\author{Author \and Coauthor}

\begin{document}
\maketitle
\begin{abstract}...\end{abstract}
\section{Introduction}
...
\bibliography{ms}  % calls ms.bbl after running bibtex locally
\end{document}
```

**Section Nesting:** Maximum 3 levels (`\section` → `\subsection` → `\subsubsection`)

**BibTeX Quality Checklist:**
- ✓ Complete entries: author, title, venue/journal, year, pages/DOI
- ✓ Correct entry types: `@article`, `@inproceedings`, `@book` (NOT `@misc` for papers)
- ✓ Citation keys: `lastname-year` scheme (e.g., `smith-2023`)
- ✓ Always include: DOI or URL
- ✗ Never: empty fields, mixed types, cryptic venues

**Common Pitfalls:**
- ✗ Do NOT modify arxiv.sty or venue .sty files
- ✗ Do NOT use .bib directly; convert to .bbl locally first
- ✗ Do NOT use JPEG if PDF version exists
- ✗ Do NOT use `\usepackage{titlesec}` or custom heading macros

---

## Narrative Arc for Top-Tier Papers

**Pattern observed in arXiv cs.LG papers:**

1. **Hook** (1-2 paragraphs)
   - Concrete failing case (not abstract problem statement)
   - Why it matters (practical impact or theoretical insight)

2. **Formalize the Gap** (1-2 paragraphs)
   - Existing work (heavy citation to prior approaches)
   - Why they fall short (specific limitation)
   - What's missing

3. **Lightweight Intuition** (0.5-1 paragraph)
   - High-level idea BEFORE diving into math
   - Readers should understand essence before technical details

4. **Technical Depth** (2-3 paragraphs)
   - Full formal treatment
   - Can be dense here (readers are motivated)

5. **Validation Incremental** (Results section)
   - Synthetic → toy data → realistic benchmarks
   - Each result builds on previous

6. **Ablations** (within Results)
   - Isolate each contribution claim
   - Show that removing each piece hurts performance

7. **Limitations Paragraph** (Discussion)
   - Explicit statement of weaknesses
   - Builds credibility with reviewers; shows awareness

---

## Anti-Patterns in Weak Papers

1. **Unclear problem statement** — readers can't articulate novelty in 1 sentence
2. **Citation overload in intro** — >50% of all citations in first section dilutes focus
3. **Methods section too brief** — insufficient detail for reproducibility
4. **Results without interpretation** — presents tables/plots without explaining meaning
5. **Orphaned figures** — figures exist but never mentioned in text
6. **Captions that duplicate text** — captions repeat paragraph content instead of standing alone
7. **Over-hedging in discussion** — "could potentially suggest that possibly..." (weak voice)
8. **Missing ablations** — claims multiple contributions but doesn't isolate each impact
9. **Inconsistent notation** — switching between $\mathbf{x}$ and $\vec{x}$ for same variable
10. **No limitation section** — reviewers assume authors unaware of weaknesses; kills credibility

---

## arXiv Submission Checklist

**Format:**
- [ ] Main file is `ms.tex` or `paper.tex`
- [ ] Bibliography is `ms.bbl` (NOT `ms.bib`—convert locally first)
- [ ] Figures are PDF/PNG/JPEG only (SVG unsupported by arXiv pdfLaTeX)
- [ ] File size <5 MB; large assets in ancillary files

**Metadata:**
- [ ] Title + authors (no anonymous submissions)
- [ ] Abstract + complete reference list
- [ ] Keywords (5-7 recommended)

**Compilation:**
- [ ] `pdflatex paper.tex && bibtex paper && pdflatex paper.tex && pdflatex paper.tex`
- [ ] Zero errors/warnings (except minor font substitutions)
- [ ] All figures render correctly
- [ ] Page count reasonable for venue
- [ ] Margins ≥1"

**Content:**
- [ ] Narrative arc: problem → solution → proof → implications
- [ ] Citation density: intro 40%+, results 26%, discussion 7%
- [ ] No \cite{} placeholders—all citations resolved
- [ ] Abstract is standalone (no section references)
- [ ] All figures have captions and are referenced in text
- [ ] Ablations isolate each contribution claim
- [ ] Limitations paragraph included

---

## Recommended Tools & Resources

**Writing & Collaboration:**
- Overleaf (LaTeX with real-time collaboration, version control)
- Mendeley/Zotero (BibTeX curation)
- Hemingway Editor (clarity check)
- Grammarly (grammar + tone)

**Figures & Diagrams:**
- TikZ (precise, publication-ready, vector)
- Graphviz (automatic layout for graphs)
- Matplotlib + pgf backend (plots as vector PDF)
- Inkscape (manual diagramming, SVG → PDF)

**Citation Management:**
- DBLP (authoritative for CS papers)
- arXiv (with DOI)
- IEEE Xplore (IEEE papers)
- ACM DL (ACM papers)
- Google Scholar (cross-check, but verify with above)

**Official Guidelines:**
- [arXiv Format Requirements](https://info.arxiv.org/help/policies/format_requirements.html)
- [ICML 2024 Guidelines](https://icml.cc/Conferences/2024/PaperGuidelines)
- [NeurIPS 2026 Template](https://www.overleaf.com/latex/templates/formatting-instructions-for-neurips-2026/bjdwqfdkyftc)

---

## Key Takeaway

**Write for readers, not for reviewers.** A strong narrative arc, sufficient depth, and clear visuals make papers readable. Weak voice, thin content, and orphaned figures make papers skimmable. Readers remember papers with:
- Clear problem statement (one sentence)
- Strong motivation (concrete examples)
- Sufficient depth (3-5 paragraphs per section)
- Publication-quality figures (vector, not raster)
- Proper citations (researched, not placeholder)
- Honest limitations (builds trust)

A paper that follows these principles will be read, cited, and respected.

