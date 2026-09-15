# Academic Paper Generation: From Concepts to Production-Ready PDF

**Scope:** Complete workflow for generating publication-ready academic papers  
**Audience:** Academic writing, arXiv submissions, conference papers  
**Complexity:** High (multi-phase orchestration)  
**Time Investment:** Significant initial learning, fast payoff on repeated use

---

## Overview

This skill provides a systematic, reusable workflow for transforming raw technical concepts, architectural decisions (ADRs), and research findings into a complete, polished academic paper suitable for arXiv, IEEE, ACM, or other venues.

The workflow enforces quality gates at each phase to catch common academic writing failures early:
- Weak narrative arc (no clear story)
- Thin content (placeholders instead of depth)
- Broken references (placeholder citations without research)
- Poor figure quality (artifacts, misalignment, missing context)
- Structural inconsistency (section depth varies)

---

## The Five-Phase Workflow

### Phase 1: Research & Reference Collection (Narrative Planning)

**Goal:** Establish the paper's core claim and gather supporting references.

**Steps:**
1. **Read source material** (ADRs, concepts, existing docs) and extract the central insight—the one-sentence claim the paper argues.
   - Good claim: "Compliance mechanisms must be structural invariants, not configurable features."
   - Bad claim: "We implemented twelve compliance mechanisms."

2. **Identify the problem space** via three historical examples (real incidents, not invented):
   - What went wrong?
   - Why did it happen? (root cause)
   - What does it reveal about current practice?
   
3. **Dispatch reference research agents** (parallel work):
   - Agent A: "Find 15 academic papers on [topic + regulatory domain]"
   - Agent B: "Find 5 industry incident postmortems (Equifax, Facebook, etc.)"
   - Agent C: "Find 10 standards/regulations (GDPR, EU AI Act, HIPAA, PCI-DSS)"
   
   Each agent returns curated list with URLs, citations, and 1-line summary of relevance.

4. **Build a reference database** (CSV or JSON):
   ```
   category | author | year | title | url | relevance | sections_to_cite
   ----------|--------|------|-------|-----|-----------|------------------
   regulatory | EU | 2024 | EU AI Act | ... | Article 50 | intro, m1
   incident | Rouse | 2017 | Equifax | ... | compliance failure | intro
   academic | Emerson | 2008 | Model Checking | ... | formal methods | methodology
   ```

5. **Map references to paper sections** (pre-commitment):
   - Introduction (3-5 foundational + 2-3 incident refs)
   - Methodology (3-4 formal methods refs)
   - Each mechanism (1-2 regulatory + 1-2 domain-specific refs)
   - Related Work (5-7 competitive/complementary papers)

**Deliverable:** A reference database and a one-paragraph problem statement that explains the paper's narrative arc.

**Quality Gate:** Every section has ≥3 references allocated. No section is empty.

---

### Phase 2: Narrative Arc Design (Story Structure)

**Goal:** Create a clear, compelling story that carries the reader from problem through solution to validation.

**The IMRAD+C Structure (adapted for this domain):**

1. **Introduction (40% of body text)**
   - Hook (problem statement, 3 incidents showing pattern)
   - Gap (current approaches fail because...)
   - Thesis (our solution: structural invariants)
   - Roadmap (in this paper we...)

2. **Methodology (15% of body text)**
   - Formal model (state machines, CTL*)
   - Threat model (what could break compliance)
   - Design principles (fail-closed, audit-first, etc.)

3. **Results / Mechanisms (30% of body text)**
   - The twelve mechanisms, each:
     - Regulatory requirement (cite the article)
     - Implementation (how we enforce it)
     - Why it's structural (can't be toggled away)
     - Verification (how we prove it works)

4. **Application / Deployment (10% of body text)**
   - CorvinOS as proof-of-concept
   - Production stats (10M+ events, 50+ tenants, 100% audit clean)
   - Performance overhead (measured, not claimed)

5. **Conclusion (5% of body text)**
   - Restatement of thesis
   - Implications (compliance as architecture, not configuration)
   - Future work (formal verification, distributed systems)

**Narrative Arc Check:**
```
Problem:  "Compliance keeps getting disabled under operational pressure" 
          ✓ Concrete examples (Equifax, Facebook, SolarWinds)
          ✓ Root cause explained (treated as configurable feature)

Solution: "Make compliance a structural invariant instead"
          ✓ Formal definition
          ✓ Twelve orthogonal mechanisms
          ✓ Can't be disabled without system restart

Proof:    "CorvinOS shows this works in production"
          ✓ 12 months, 50+ tenants, zero incidents
          ✓ Measurable audit overhead
          ✓ Real deployment data
```

**Deliverable:** A one-page outline with section titles, estimated word count per section, and narrative flow check.

**Quality Gate:** Reader can understand the paper's claim by reading only the section headings. Every section advances the narrative.

---

### Phase 3: Content Generation (Writing with Depth)

**Goal:** Write sections with sufficient depth (3-5 well-developed paragraphs, not 1-2 sentences).

**Heuristics for Content Density:**

| Section | Length | Paragraphs | References | Formulas |
|---------|--------|-----------|-----------|----------|
| Abstract | 150-200 words | 1 | 0 | 0 |
| Introduction | 800-1200 words | 4-6 | 5-8 | 1-2 |
| Methodology | 600-1000 words | 3-5 | 3-5 | 3-5 |
| Each Mechanism | 200-300 words | 2-3 | 1-2 | 0-1 |
| Related Work | 400-600 words | 3-4 | 7-10 | 0 |
| Conclusion | 300-400 words | 2-3 | 0-2 | 0 |

**Writing Patterns:**

For each mechanism (M1-M12), follow this template:

```
**Regulatory Basis:** "GDPR Article X requires..."
→ Cite the regulation, explain what it's trying to protect

**Current Practice Fails:** "Most systems implement this as..."
→ Show why typical implementations are configurable/optional

**Structural Solution:** "In CorvinOS, we enforce this by..."
→ Explain the architectural decision
→ Show why it cannot be disabled

**Verification:** "Auditors can verify by..."
→ Concrete, measurable verification method
→ Complexity (O(n), O(1), etc.)

**Example Impact:** "When [scenario], the system [response]"
→ Concrete scenario showing the invariant holding
```

**References Within Text:**

- Cite regulations by article number: "GDPR Article 30 requires..."
- Cite academic work early in sections: "Prior work on compliance-as-code [Ref] assumes..."
- Cite incidents in introduction: "Equifax (2017) disabled audit logging [Ref]..."
- Do NOT use placeholder citations like \cite{ComplianceAsCode2019}; research the paper first.

**Deliverable:** Complete draft of all sections with proper citations embedded.

**Quality Gate:** 
- No \cite{} placeholders—every citation is researched and valid
- Minimum 2-3 paragraphs per mechanism
- Every regulatory requirement is cited to the actual regulation
- Abstract and conclusion are polished and final

---

### Phase 4: Figures & Diagrams (Quality Visual Communication)

**Goal:** Create publication-quality diagrams that support the narrative.

**Diagram Types & Creation Strategy:**

| Diagram | Type | Tools | Format | Checklist |
|---------|------|-------|--------|-----------|
| Threat Model | Flow | Mermaid / Graphviz | SVG → PDF (not PNG) | Labels clear, no overlaps |
| Hash Chain Timeline | Sequence | TikZ / draw.io | PDF | Time axis labeled, colors distinct |
| Compliance Matrix | Table | TikZ / Excel → TikZ | PDF | Green=compliant, labels aligned |
| Multi-tenant Isolation | Architecture | Mermaid / draw.io | PDF | Boundaries clear, isolation obvious |
| Mechanism Overview | Infographic | Custom SVG | PDF | Hierarchical layout, legend |

**Critical Rules for Figure Quality:**

1. **SVG → PDF Pipeline (not PNG):**
   - SVG source files stored in `figures/` directory
   - Use `inkscape --export-type=pdf` to convert (no rsvg-convert issues)
   - Verify PDF in Acrobat; check for text rendering issues
   - PNG export is only fallback if PDF fails

2. **Caption & Labeling:**
   - Caption format: "Figure N: [One-line claim]. [Supporting detail]. [How it relates to narrative]."
   - Example: "Figure 2: Boot-Time Tripwire Sequence. Audit chain is verified before plugins load, ensuring no instance runs with corrupted records. This structural enforcement prevents compliance drift over time."
   - All labels in figures must be English, sans-serif font (Arial, Helvetica)
   - No diagrams use red/green alone (colorblind-safe palette)

3. **Placement & Integration:**
   - Figures referenced in text before appearing: "As shown in Figure 2..."
   - No floating figures; every figure has a cite/reference in text
   - Maximum one figure per subsection; typically 1-2 per major section
   - Total figures: 3-5 for a short paper, 8-12 for a long paper

4. **Anti-patterns to Avoid:**
   - No screenshots of code (use verbatim blocks instead)
   - No auto-generated diagrams without manual cleanup
   - No figures with blurry text or pixel artifacts
   - No diagrams that replicate content in the text (figures should add, not duplicate)

**Deliverable:** 4-6 publication-quality diagrams (PDF format) with captions and references integrated into text.

**Quality Gate:** 
- Every diagram is in PDF, not PNG
- Every diagram has a caption explaining narrative relevance
- Every diagram is referenced by text (not orphaned)
- No text rendering issues; all labels are crisp

---

### Phase 5: LaTeX Assembly & Compilation (Final Production)

**Goal:** Produce a final, polished PDF suitable for submission.

**Template & Structure:**

```latex
\documentclass[11pt,a4paper]{article}
\usepackage{arxiv}
\usepackage[utf8]{inputenc}
\usepackage{hyperref}
\usepackage{amsmath}
\usepackage{graphicx}

\title{Title}
\author{Author1 \and Author2}
\date{Date}

\begin{document}
\maketitle
\begin{abstract}...\end{abstract}
\section{Introduction}...\end{section}
\section{Methodology}...\end{section}
\section{Results}...\end{section}
\section{Conclusion}...\end{section}
\begin{thebibliography}{99}...\end{thebibliography}
\end{document}
```

**BibTeX Best Practices:**

```bibtex
@article{AuthorYear,
  author = "First Last and Second Last",
  title = "Full Title with Capitals",
  journal = "Journal Name",
  year = "2024",
  volume = "10",
  pages = "123--456"
}

@misc{Regulation2024,
  title = "Regulation Title",
  author = "Organization",
  year = "2024",
  note = "Official Journal Reference"
}

@techreport{CISAReport,
  author = "CISA",
  title = "Full Title",
  year = "2020",
  note = "URL or Report Number"
}
```

**Compilation Workflow:**

```bash
# Step 1: Check for undefined references
tectonic paper.tex 2>&1 | grep -E "undefined|Missing"

# Step 2: Verify figure paths
for fig in figures/*.pdf; do
  pdfinfo "$fig" > /dev/null || echo "✗ $fig is corrupted"
done

# Step 3: Full compilation
tectonic paper.tex --keep-logs

# Step 4: Verify output
pdfinfo paper.pdf | head -15
```

**Final Checks Before Submission:**

- [ ] Abstract is 150-200 words, standalone (no references to sections)
- [ ] All figures are in PDF, not PNG
- [ ] No \cite{} placeholders (every citation resolved)
- [ ] Page count is reasonable (4-8 pages for short paper, 12-20 for full paper)
- [ ] Margins are 1 inch (0.75-1 inch is acceptable)
- [ ] All figures have captions and are referenced in text
- [ ] Keywords are listed (typically 5-7)
- [ ] No typos in author names or affiliations
- [ ] References are sorted consistently (alphabetical or numeric)

**Deliverable:** A final paper.pdf, production-ready and suitable for arXiv/journal submission.

**Quality Gate:**
- PDF compiles without errors or warnings
- All figures render correctly (pdfinfo validates)
- Page count is reasonable
- All references are resolved
- Visual inspection passes (no typos, formatting is clean)

---

## Anti-Patterns to Avoid

| Anti-Pattern | Why It Fails | Fix |
|--------------|-------------|-----|
| Placeholder citations (\cite{ComplianceAsCode2019} without research) | Reader can't verify; looks unfinished | Research first, cite real papers with URLs |
| One-sentence mechanism descriptions | Lacks depth; looks like a checklist, not analysis | 2-3 paragraphs per mechanism; regulatory basis + implementation + why structural |
| PNG figures with artifacts | Blurry, unprofessional; looks auto-generated | Use SVG → PDF pipeline; verify in Acrobat |
| No narrative arc (just lists facts) | Reader doesn't understand why they should care | IMRAD structure: problem → solution → proof → implications |
| Figures without captions (self-explanatory claim) | Impossible to reference; violates journal guidelines | Every figure gets a caption explaining narrative relevance |
| Section depth varies wildly (1 para, then 5 paras) | Looks inconsistent and unprofessional | Target density: 3-5 paras per major section |
| Related work crammed into conclusion | Makes conclusion weak; related work hard to find | Dedicated Related Work section (one major section) |
| No production data (only theory) | Claims unsupported; looks academic but not grounded | Include real deployment stats (page count, latency, audit events) |

---

## Implementation Checklist

Use this checklist to track progress through all five phases:

- [ ] **Phase 1: Research**
  - [ ] Central claim identified and phrased
  - [ ] 3+ historical examples (incidents) researched
  - [ ] Reference database built (40+ sources)
  - [ ] References mapped to sections

- [ ] **Phase 2: Narrative**
  - [ ] One-page outline written
  - [ ] IMRAD structure mapped
  - [ ] Narrative arc checked (problem → solution → proof)
  - [ ] Section word counts estimated

- [ ] **Phase 3: Content**
  - [ ] All sections written (no placeholders)
  - [ ] 3-5 paragraphs per major section
  - [ ] All citations researched (no \cite{} placeholders)
  - [ ] Examples and scenarios concrete (not hypothetical)

- [ ] **Phase 4: Figures**
  - [ ] 4-6 diagrams created (SVG source files saved)
  - [ ] All diagrams converted to PDF
  - [ ] Captions written and embedded
  - [ ] Figures referenced in text

- [ ] **Phase 5: Assembly**
  - [ ] LaTeX template created
  - [ ] BibTeX references formatted correctly
  - [ ] Paper compiles without errors
  - [ ] Final checks passed

---

## Automation Opportunities

This workflow can be partially automated:

1. **Phase 1 (Research):** Dispatch three parallel research agents; aggregate results into reference DB
2. **Phase 2 (Narrative):** Use template-driven outline generation; validate structure via checklist
3. **Phase 3 (Content):** Use generation agents with narrative-aware prompts; verify density heuristics
4. **Phase 4 (Figures):** Generate diagram specs from mechanisms; batch-convert SVG → PDF via Inkscape
5. **Phase 5 (Assembly):** Template-driven LaTeX generation; automated compilation and validation

**Next Skill:** "Academic Paper Generator" (orchestrates all five phases via multi-agent workflow)

---

## Resources & References

- **ArXiv Best Practices:** https://arxiv.org/help/submit
- **Henning, A. (2020). "How to Write a Good Research Paper."** IEEE Computer Society
- **Academic Writing Guides:** Purdue OWL (online writing lab)
- **Diagram Tools:** Mermaid, Graphviz, TikZ, draw.io
- **Compilation:** Tectonic (robust TeX engine)

---

## Key Takeaway

**Compliance-as-Architecture, not Configuration** applies to papers too: structure the narrative arc, depth, figures, and citations as load-bearing elements of the paper, not as optional flourishes added at the end. A paper with a clear story, sufficient depth, and strong visuals is far more likely to be read, cited, and accepted than one where these elements are afterthoughts.
