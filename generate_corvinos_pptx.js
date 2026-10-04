const PptxGenJS = require('pptxgenjs');

const pres = new PptxGenJS();
pres.layout = 'LAYOUT_16x9';
pres.defineLayout({ name: 'LAYOUT_16x9', width: 10, height: 5.625 });

// Theme with CorvinOS brand colors
const THEME = {
  name: 'CorvinOS Dark Minimal',
  headFontFace: 'Calibri',
  bodyFontFace: 'Calibri',
  colors: {
    dk1: '1E2761',  // Navy (dominance)
    lt1: 'FFFFFF',  // White
    dk2: '36454F',  // Charcoal
    lt2: 'F2F2F2',  // Off-white
    accent1: '028090', // Teal (action/decision)
    accent2: 'F96167', // Coral (critical paths)
    accent3: '2C5F2D', // Forest (systems)
    accent4: 'B85042', // Terracotta (details)
    accent5: 'E7E8D1', // Sand (highlights)
    accent6: 'A7BEAE', // Sage (secondary)
    hlink: '00A896',
    folHlink: '028090'
  }
};

pres.theme = {
  headFontFace: THEME.headFontFace,
  bodyFontFace: THEME.bodyFontFace
};

const C = pres.SchemeColor;

// Define slide layouts
pres.defineSlideMaster({
  title: 'Title Slide',
  background: { color: THEME.colors.dk1 },
  objects: [
    {
      placeholder: {
        options: { name: 'title', type: 'title' },
        text: 'Placeholder'
      },
      x: 0.5, y: 1.8, w: 9, h: 1.2,
      fontSize: 54, bold: true, color: THEME.colors.lt1, align: 'left', fontFace: THEME.headFontFace
    },
    {
      placeholder: {
        options: { name: 'subtitle', type: 'body' },
        text: 'Placeholder'
      },
      x: 0.5, y: 3.2, w: 9, h: 1.2,
      fontSize: 24, color: THEME.colors.accent1, align: 'left', fontFace: THEME.bodyFontFace
    }
  ]
});

pres.defineSlideMaster({
  title: 'Content Slide',
  background: { color: THEME.colors.lt1 },
  objects: [
    { x: 0, y: 0, w: 10, h: 0.08, fill: { color: THEME.colors.accent1 } },
    {
      placeholder: {
        options: { name: 'title', type: 'title' },
        text: 'Title'
      },
      x: 0.5, y: 0.3, w: 9, h: 0.6,
      fontSize: 40, bold: true, color: THEME.colors.dk1, fontFace: THEME.headFontFace
    },
    {
      placeholder: {
        options: { name: 'body', type: 'body' },
        text: 'Content'
      },
      x: 0.5, y: 1.2, w: 9, h: 4,
      fontSize: 16, color: THEME.colors.dk2, align: 'left', fontFace: THEME.bodyFontFace
    }
  ]
});

pres.defineSlideMaster({
  title: 'Section Divider',
  background: { color: THEME.colors.accent1 },
  objects: [
    {
      placeholder: {
        options: { name: 'title', type: 'title' },
        text: 'Section'
      },
      x: 0.5, y: 2.3, w: 9, h: 1,
      fontSize: 48, bold: true, color: THEME.colors.lt1, align: 'left', fontFace: THEME.headFontFace
    }
  ]
});

pres.defineSlideMaster({
  title: 'Two Column',
  background: { color: THEME.colors.lt1 },
  objects: [
    { x: 0, y: 0, w: 10, h: 0.08, fill: { color: THEME.colors.accent1 } },
    {
      placeholder: {
        options: { name: 'title', type: 'title' },
        text: 'Title'
      },
      x: 0.5, y: 0.3, w: 9, h: 0.6,
      fontSize: 40, bold: true, color: THEME.colors.dk1, fontFace: THEME.headFontFace
    },
    {
      placeholder: {
        options: { name: 'left', type: 'body' },
        text: 'Left'
      },
      x: 0.5, y: 1.2, w: 4.3, h: 4,
      fontSize: 14, color: THEME.colors.dk2, align: 'left', fontFace: THEME.bodyFontFace
    },
    {
      placeholder: {
        options: { name: 'right', type: 'body' },
        text: 'Right'
      },
      x: 5.2, y: 1.2, w: 4.3, h: 4,
      fontSize: 14, color: THEME.colors.dk2, align: 'left', fontFace: THEME.bodyFontFace
    }
  ]
});

// Slide 1: Title Slide
let slide = pres.addSlide();
slide.background = { color: THEME.colors.dk1 };
slide.addText('CorvinOS', {
  x: 0.5, y: 1.6, w: 9, h: 0.8,
  fontSize: 60, bold: true, color: THEME.colors.accent1, align: 'left', fontFace: THEME.headFontFace
});
slide.addText('Video Producer Skill 2.0', {
  x: 0.5, y: 2.5, w: 9, h: 0.6,
  fontSize: 36, color: THEME.colors.lt1, align: 'left', fontFace: THEME.bodyFontFace
});
slide.addText('Orchestrated Video Generation mit Maestro + Worker-Architektur', {
  x: 0.5, y: 3.3, w: 9, h: 0.8,
  fontSize: 18, color: THEME.colors.accent5, align: 'left', fontFace: THEME.bodyFontFace
});
slide.addText('Basierend auf ADR-0692, ADR-0695, ADR-0705', {
  x: 0.5, y: 4.3, w: 9, h: 0.4,
  fontSize: 12, color: THEME.colors.lt2, align: 'left', fontFace: THEME.bodyFontFace, italic: true
});

// Slide 2: Section - Überblick
slide = pres.addSlide();
slide.background = { color: THEME.colors.accent1 };
slide.addText('Teil 1: Überblick', {
  x: 0.5, y: 2.3, w: 9, h: 1,
  fontSize: 48, bold: true, color: THEME.colors.lt1, align: 'left', fontFace: THEME.headFontFace
});

// Slide 3: Das Problem
slide = pres.addSlide();
slide.background = { color: THEME.colors.lt1 };
slide.addShape(pres.ShapeType.rect, { x: 0, y: 0, w: 10, h: 0.08, fill: { color: THEME.colors.accent1 } });
slide.addText('Das Problem: Marketing-Videos für CorvinOS', {
  x: 0.5, y: 0.3, w: 9, h: 0.6,
  fontSize: 40, bold: true, color: THEME.colors.dk1, fontFace: THEME.headFontFace
});
slide.addText([
  { text: '📊 PowerPoint-Folien → Screenshots', options: { fontSize: 16, bold: true, color: THEME.colors.accent1 } },
  { text: '\nConsole-Screenshots + Branding', options: { fontSize: 16, color: THEME.colors.dk2 } },
  { text: '\n\n🎤 Text-to-Speech Narration', options: { fontSize: 16, bold: true, color: THEME.colors.accent1 } },
  { text: '\nVoice-over mit Timing-Synchronisation', options: { fontSize: 16, color: THEME.colors.dk2 } },
  { text: '\n\n🎬 Video-Assembly mit FFmpeg', options: { fontSize: 16, bold: true, color: THEME.colors.accent1 } },
  { text: '\nMixing + Captions + Encoding', options: { fontSize: 16, color: THEME.colors.dk2 } },
  { text: '\n\n📤 YouTube-Export (optional, async)', options: { fontSize: 16, bold: true, color: THEME.colors.accent1 } },
  { text: '\nOAuth Upload + Metadata', options: { fontSize: 16, color: THEME.colors.dk2 } }
], { x: 0.5, y: 1.2, w: 9, h: 4, fontFace: THEME.bodyFontFace });

// Slide 4: Herausforderungen
slide = pres.addSlide();
slide.background = { color: THEME.colors.lt1 };
slide.addShape(pres.ShapeType.rect, { x: 0, y: 0, w: 10, h: 0.08, fill: { color: THEME.colors.accent2 } });
slide.addText('Architektur-Herausforderungen', {
  x: 0.5, y: 0.3, w: 9, h: 0.6,
  fontSize: 40, bold: true, color: THEME.colors.dk1, fontFace: THEME.headFontFace
});
const challenges = [
  { title: '🚨 Halluzinationen verhindern', desc: 'Narration muss aus Analyse-Ergebnissen kommen, nicht erfunden' },
  { title: '🔒 Phase Gates erzwingen', desc: 'Workflow ist streng sequenziell — out-of-order bricht Video' },
  { title: '📊 Granular Feedback', desc: 'Pro-Szene Feedback für Learning, nicht global' },
  { title: '⚙️ Async Operations', desc: 'YouTube-Upload darf Console nicht blockieren' }
];
let y = 1.2;
challenges.forEach((ch, idx) => {
  slide.addText(ch.title, { x: 0.7, y, w: 8.6, h: 0.4, fontSize: 14, bold: true, color: THEME.colors.accent2, fontFace: THEME.headFontFace });
  slide.addText(ch.desc, { x: 1.0, y: y + 0.35, w: 8.3, h: 0.5, fontSize: 12, color: THEME.colors.dk2, fontFace: THEME.bodyFontFace });
  y += 0.95;
});

// Slide 5: Section - Kernkomponenten
slide = pres.addSlide();
slide.background = { color: THEME.colors.accent3 };
slide.addText('Teil 2: Kernkomponenten', {
  x: 0.5, y: 2.3, w: 9, h: 1,
  fontSize: 48, bold: true, color: THEME.colors.lt1, align: 'left', fontFace: THEME.headFontFace
});

// Slide 6: Maestro Orchestrator
slide = pres.addSlide();
slide.background = { color: THEME.colors.lt1 };
slide.addShape(pres.ShapeType.rect, { x: 0, y: 0, w: 10, h: 0.08, fill: { color: THEME.colors.accent3 } });
slide.addText('os.video_producer: Maestro-Orchestrator', {
  x: 0.5, y: 0.3, w: 9, h: 0.6,
  fontSize: 40, bold: true, color: THEME.colors.dk1, fontFace: THEME.headFontFace
});
slide.addText([
  { text: 'Phase 1: Asset Ingestion ', options: { fontSize: 14, bold: true } },
  { text: '→ assets.json\n', options: { fontSize: 14, color: THEME.colors.dk2 } },
  { text: 'Phase 2: Asset Analyzer ', options: { fontSize: 14, bold: true } },
  { text: '→ analysis.json (Fact-Sourcing)\n', options: { fontSize: 14, color: THEME.colors.dk2 } },
  { text: 'Phase 3: Storyboard Generation ', options: { fontSize: 14, bold: true } },
  { text: '(LLM, constrained to analysis only)\n', options: { fontSize: 14, color: THEME.colors.dk2 } },
  { text: 'Phase 4: Parallel Workers\n', options: { fontSize: 14, bold: true } },
  { text: '• Voice Synthesizer\n• Screenshot Capturer\n• Slide Renderer\n', options: { fontSize: 13, color: THEME.colors.dk2 } },
  { text: 'Phase 5: Video Assembly ', options: { fontSize: 14, bold: true } },
  { text: '(FFmpeg orchestration)\n', options: { fontSize: 14, color: THEME.colors.dk2 } },
  { text: 'Phase 6: Feedback Collection ', options: { fontSize: 14, bold: true } },
  { text: '(SceneRenderedEvent per scene)', options: { fontSize: 14, color: THEME.colors.dk2 } }
], { x: 0.7, y: 1.2, w: 8.6, h: 4, fontFace: THEME.bodyFontFace });

// Slide 7: Worker Architektur
slide = pres.addSlide();
slide.background = { color: THEME.colors.lt1 };
slide.addShape(pres.ShapeType.rect, { x: 0, y: 0, w: 10, h: 0.08, fill: { color: THEME.colors.accent4 } });
slide.addText('Five Specialized Worker Skills', {
  x: 0.5, y: 0.3, w: 9, h: 0.6,
  fontSize: 40, bold: true, color: THEME.colors.dk1, fontFace: THEME.headFontFace
});

const workers = [
  { name: 'asset_analyzer', duty: 'Deep analysis of PPTs, screenshots (Phase 2)' },
  { name: 'voice_synthesizer', duty: 'TTS + narration timing (Phase 4)' },
  { name: 'screenshot_capturer', duty: 'Console capture + metadata (Phase 4)' },
  { name: 'slide_renderer', duty: 'PowerPoint slides → PNG + branding (Phase 5)' },
  { name: 'video_assembler', duty: 'FFmpeg orchestration (Phase 6-7)' }
];

y = 1.2;
workers.forEach(w => {
  slide.addShape(pres.ShapeType.rect, {
    x: 0.5, y, w: 9, h: 0.6,
    fill: { color: THEME.colors.lt2 },
    line: { color: THEME.colors.accent4, width: 2 }
  });
  slide.addText(`worker.${w.name}`, { x: 0.7, y: y + 0.05, w: 2, h: 0.25, fontSize: 12, bold: true, color: THEME.colors.accent4, fontFace: THEME.headFontFace });
  slide.addText(w.duty, { x: 2.8, y: y + 0.08, w: 6.7, h: 0.4, fontSize: 11, color: THEME.colors.dk2, fontFace: THEME.bodyFontFace });
  y += 0.7;
});

// Slide 8: Designmuster - Dual-Host
slide = pres.addSlide();
slide.background = { color: THEME.colors.lt1 };
slide.addShape(pres.ShapeType.rect, { x: 0, y: 0, w: 10, h: 0.08, fill: { color: THEME.colors.accent2 } });
slide.addText('Designmuster: Dual-Host Architektur', {
  x: 0.5, y: 0.3, w: 9, h: 0.6,
  fontSize: 40, bold: true, color: THEME.colors.dk1, fontFace: THEME.headFontFace
});
slide.addText([
  { text: 'CorvinOS hat ZWEI unabhängige FastAPI-Hosts:\n', options: { fontSize: 14, bold: true, color: THEME.colors.accent2 } },
  { text: '1. ', options: { fontSize: 13, bold: true } },
  { text: 'corvin_gateway.app', options: { fontSize: 13, bold: true, color: THEME.colors.accent1 } },
  { text: ' — API-Server mit RelayListener\n', options: { fontSize: 13 } },
  { text: '2. ', options: { fontSize: 13, bold: true } },
  { text: 'corvin_console.standalone', options: { fontSize: 13, bold: true, color: THEME.colors.accent1 } },
  { text: ' — CLI-Server (was', options: { fontSize: 13 } },
  { text: ' corvin serve', options: { fontSize: 13, italic: true } },
  { text: ' startet)\n\n', options: { fontSize: 13 } },
  { text: '⚠️ Beide mounten dieselben A2A-Routen ', options: { fontSize: 13, bold: true, color: THEME.colors.accent2 } },
  { text: '(/v1/a2a/receive, etc.)\n', options: { fontSize: 13 } },
  { text: '⚠️ Fix in nur EINER Datei beeinträchtigt nicht die andere\n', options: { fontSize: 13, bold: true, color: THEME.colors.accent2 } },
  { text: '✓ Lösung: Grep BEIDE Dateien, explizit klären welcher Host läuft (ps aux)', options: { fontSize: 13, color: THEME.colors.accent3 } }
], { x: 0.5, y: 1.2, w: 9, h: 4, fontFace: THEME.bodyFontFace });

// Slide 9: Section - Architekturentscheidungen
slide = pres.addSlide();
slide.background = { color: THEME.colors.dk1 };
slide.addText('Teil 3: Architekturentscheidungen', {
  x: 0.5, y: 2.3, w: 9, h: 1,
  fontSize: 48, bold: true, color: THEME.colors.accent1, align: 'left', fontFace: THEME.headFontFace
});

// Slide 10: Phase Gates (ADR-0692)
slide = pres.addSlide();
slide.background = { color: THEME.colors.lt1 };
slide.addShape(pres.ShapeType.rect, { x: 0, y: 0, w: 10, h: 0.08, fill: { color: THEME.colors.accent1 } });
slide.addText('ADR-0692: Phase Gates gegen Halluzinationen', {
  x: 0.5, y: 0.3, w: 9, h: 0.6,
  fontSize: 40, bold: true, color: THEME.colors.dk1, fontFace: THEME.headFontFace
});
slide.addText([
  { text: 'Prinzip: ', options: { fontSize: 14, bold: true } },
  { text: '"Analyse vor Narration"\n', options: { fontSize: 14 } },
  { text: '• Phase 2 (Deep Analysis)', options: { fontSize: 13, bold: true } },
  { text: ' ist MANDATORY\n', options: { fontSize: 13 } },
  { text: '  → Menschliche Faktenchecks (nicht LLM-Guessing)\n', options: { fontSize: 13 } },
  { text: '• Storyboard muss ', options: { fontSize: 13, bold: true } },
  { text: 'NUR', options: { fontSize: 13, bold: true, color: THEME.colors.accent2 } },
  { text: ' analysis.json referenzieren\n', options: { fontSize: 13 } },
  { text: '• Enforcement: ', options: { fontSize: 13, bold: true } },
  { text: 'os.video_producer.AnalysisIncompleteError\n', options: { fontSize: 13 } },
  { text: '  wenn analysis.ready_for_narration != true\n\n', options: { fontSize: 13 } },
  { text: 'Resultat: Keine erfundenen Videos', options: { fontSize: 14, bold: true, color: THEME.colors.accent1 } }
], { x: 0.5, y: 1.2, w: 9, h: 4, fontFace: THEME.bodyFontFace });

// Slide 11: Preconditions (ADR-0692)
slide = pres.addSlide();
slide.background = { color: THEME.colors.lt1 };
slide.addShape(pres.ShapeType.rect, { x: 0, y: 0, w: 10, h: 0.08, fill: { color: THEME.colors.accent1 } });
slide.addText('ADR-0692: Preconditions for Sequential Execution', {
  x: 0.5, y: 0.3, w: 9, h: 0.6,
  fontSize: 40, bold: true, color: THEME.colors.dk1, fontFace: THEME.headFontFace
});
slide.addText([
  { text: 'Jeder Worker deklariert Hard Dependencies:', options: { fontSize: 14, bold: true } },
  { text: '\n\n', options: {} },
  { text: '@skill.register(preconditions=[', options: { fontSize: 12, fontFace: 'Courier New' } },
  { text: '\n    ', options: { fontSize: 12, fontFace: 'Courier New' } },
  { text: 'Precondition(path="storyboard.json", must_exist=True)', options: { fontSize: 11, fontFace: 'Courier New', color: THEME.colors.accent3 } },
  { text: '\n', options: { fontSize: 12, fontFace: 'Courier New' } },
  { text: '])\n', options: { fontSize: 12, fontFace: 'Courier New' } },
  { text: '\n✓ Versuch, Worker OHNE erfüllte Preconditions zu rufen:\n', options: { fontSize: 13, bold: true } },
  { text: '  SkillPreconditionNotMet Exception', options: { fontSize: 13, bold: true, color: THEME.colors.accent2 } },
  { text: '\n✓ Skill 2.0 Runtime validiert automatisch\n', options: { fontSize: 13 } },
  { text: '✓ Out-of-order Execution unmöglich', options: { fontSize: 13, bold: true, color: THEME.colors.accent1 } }
], { x: 0.5, y: 1.2, w: 9, h: 4, fontFace: THEME.bodyFontFace });

// Slide 12: Per-Scene Feedback (ADR-0695)
slide = pres.addSlide();
slide.background = { color: THEME.colors.lt1 };
slide.addShape(pres.ShapeType.rect, { x: 0, y: 0, w: 10, h: 0.08, fill: { color: THEME.colors.accent1 } });
slide.addText('ADR-0695: Fine-Grained Learning via Per-Scene Feedback', {
  x: 0.5, y: 0.3, w: 9, h: 0.6,
  fontSize: 40, bold: true, color: THEME.colors.dk1, fontFace: THEME.headFontFace
});
slide.addText([
  { text: 'Statt global "Video war gut":', options: { fontSize: 14, bold: true } },
  { text: '\n\n', options: {} },
  { text: '{\n  "scene_id": "s05",\n  "feedback": "screenshot_cropped_too_tight",\n  "quality_score": 0.73\n}', options: { fontSize: 11, fontFace: 'Courier New', color: THEME.colors.accent3 } },
  { text: '\n\nOptimizer lernt:', options: { fontSize: 14, bold: true } },
  { text: '\nconfig.screenshot_capturer.crop_margin += 10px', options: { fontSize: 13, fontFace: 'Courier New', color: THEME.colors.accent1 } },
  { text: '\n(nur für Scene-Typ s05)', options: { fontSize: 12, italic: true, color: THEME.colors.dk2 } }
], { x: 0.5, y: 1.2, w: 9, h: 4, fontFace: THEME.bodyFontFace });

// Slide 13: Async YouTube (ADR-0695)
slide = pres.addSlide();
slide.background = { color: THEME.colors.lt1 };
slide.addShape(pres.ShapeType.rect, { x: 0, y: 0, w: 10, h: 0.08, fill: { color: THEME.colors.accent1 } });
slide.addText('ADR-0695: Async YouTube Upload (Nicht blockierend)', {
  x: 0.5, y: 0.3, w: 9, h: 0.6,
  fontSize: 40, bold: true, color: THEME.colors.dk1, fontFace: THEME.headFontFace
});
slide.addText([
  { text: 'YouTube API Realität:', options: { fontSize: 14, bold: true } },
  { text: '\n• Upload-Limits: 50GB/Tag\n• Processing Delays: 10-60 Minuten\n• Exponential Backoff erforderlich\n\n', options: { fontSize: 13, color: THEME.colors.dk2 } },
  { text: '❌ Blocking Console auf Upload = Timeout\n\n', options: { fontSize: 13, bold: true, color: THEME.colors.accent2 } },
  { text: '✅ Lösung:', options: { fontSize: 14, bold: true } },
  { text: ' worker.youtube_uploader.enqueue()\n', options: { fontSize: 14 } },
  { text: '   → Returns task_id immediately\n', options: { fontSize: 13, color: THEME.colors.dk2 } },
  { text: '   → Operator polls via corvin task show <task_id>\n', options: { fontSize: 13, color: THEME.colors.dk2 } },
  { text: '   → Async Background Processing', options: { fontSize: 13, bold: true, color: THEME.colors.accent1 } }
], { x: 0.5, y: 1.2, w: 9, h: 4, fontFace: THEME.bodyFontFace });

// Slide 14: Asset System (ADR-0705)
slide = pres.addSlide();
slide.background = { color: THEME.colors.lt1 };
slide.addShape(pres.ShapeType.rect, { x: 0, y: 0, w: 10, h: 0.08, fill: { color: THEME.colors.accent1 } });
slide.addText('ADR-0705: Unified Asset System + Screenshot Engine', {
  x: 0.5, y: 0.3, w: 9, h: 0.6,
  fontSize: 40, bold: true, color: THEME.colors.dk1, fontFace: THEME.headFontFace
});
slide.addText([
  { text: 'Asset Manager – Extensible Renderer Registry:\n', options: { fontSize: 14, bold: true } },
  { text: '• TextSlideRenderer (Design System)\n', options: { fontSize: 13, color: THEME.colors.dk2 } },
  { text: '• SVGDiagramRenderer (Architecture/Flows)\n', options: { fontSize: 13, color: THEME.colors.dk2 } },
  { text: '• ScreenshotRenderer (Browser Automation)\n', options: { fontSize: 13, color: THEME.colors.dk2 } },
  { text: '• VideoClipRenderer (Segments)\n', options: { fontSize: 13, color: THEME.colors.dk2 } },
  { text: '• AnimationRenderer (GIFs/SVGs)\n\n', options: { fontSize: 13, color: THEME.colors.dk2 } },
  { text: 'Output: 1920×1080 RGB uniform', options: { fontSize: 14, bold: true, color: THEME.colors.accent1 } },
  { text: '\n\nScreenshot Engine:', options: { fontSize: 14, bold: true } },
  { text: '\n• Pluggable Backends (Stub → Playwright → Selenium)', options: { fontSize: 13, color: THEME.colors.dk2 } },
  { text: '\n• Built-in Annotations (Arrows, Boxes, Circles, Text)', options: { fontSize: 13, color: THEME.colors.dk2 } }
], { x: 0.5, y: 1.2, w: 9, h: 4, fontFace: THEME.bodyFontFace });

// Slide 15: Code Review System Integration
slide = pres.addSlide();
slide.background = { color: THEME.colors.lt1 };
slide.addShape(pres.ShapeType.rect, { x: 0, y: 0, w: 10, h: 0.08, fill: { color: THEME.colors.accent1 } });
slide.addText('Integration: CorvinOS Code Review System', {
  x: 0.5, y: 0.3, w: 9, h: 0.6,
  fontSize: 40, bold: true, color: THEME.colors.dk1, fontFace: THEME.headFontFace
});
slide.addText([
  { text: '6-Skill Code Review System (Memory):\n\n', options: { fontSize: 14, bold: true } },
  { text: '1. ', options: { fontSize: 13, bold: true } },
  { text: 'code.review', options: { fontSize: 13, bold: true, color: THEME.colors.accent1 } },
  { text: ' — LDD-optimized Orchestrator (3 Altitudes)\n', options: { fontSize: 13 } },
  { text: '2. corvinOS.review.gates — Compliance, ADR-Gate, Security\n', options: { fontSize: 13, color: THEME.colors.dk2 } },
  { text: '3. corvinOS.review.compliance — GDPR, EU AI Act\n', options: { fontSize: 13, color: THEME.colors.dk2 } },
  { text: '4. corvinOS.review.frontend — Build, TypeScript, E2E\n', options: { fontSize: 13, color: THEME.colors.dk2 } },
  { text: '5. corvinOS.review.backend — Protocol, State Machines, Tests\n', options: { fontSize: 13, color: THEME.colors.dk2 } },
  { text: '6. corvinOS.review.quality — Docs Sync (Hard Constraint)\n\n', options: { fontSize: 13, color: THEME.colors.dk2 } },
  { text: '✓ Video Producer Skill nutzt diesen Flow vor Merge', options: { fontSize: 13, bold: true, color: THEME.colors.accent1 } }
], { x: 0.5, y: 1.2, w: 9, h: 4, fontFace: THEME.bodyFontFace });

// Slide 16: Fazit & Nächste Schritte
slide = pres.addSlide();
slide.background = { color: THEME.colors.dk1 };
slide.addShape(pres.ShapeType.rect, { x: 0, y: 0, w: 10, h: 0.08, fill: { color: THEME.colors.accent1 } });
slide.addText('Fazit: Orchestrated > Monolithic', {
  x: 0.5, y: 0.5, w: 9, h: 0.6,
  fontSize: 40, bold: true, color: THEME.colors.lt1, fontFace: THEME.headFontFace
});
slide.addText([
  { text: '✅ Modulität:', options: { fontSize: 14, bold: true, color: THEME.colors.accent1 } },
  { text: ' Jeder Worker unabhängig testbar + deploybar\n', options: { fontSize: 13, color: THEME.colors.lt2 } },
  { text: '✅ Keine Halluzinationen:', options: { fontSize: 14, bold: true, color: THEME.colors.accent1 } },
  { text: ' Phase Gates + Fact-Sourcing\n', options: { fontSize: 13, color: THEME.colors.lt2 } },
  { text: '✅ Sequenzielle Ausführung:', options: { fontSize: 14, bold: true, color: THEME.colors.accent1 } },
  { text: ' Preconditions erzwingen Order\n', options: { fontSize: 13, color: THEME.colors.lt2 } },
  { text: '✅ Self-Learning:', options: { fontSize: 14, bold: true, color: THEME.colors.accent1 } },
  { text: ' Per-Scene Feedback → Optimizer\n', options: { fontSize: 13, color: THEME.colors.lt2 } },
  { text: '✅ Nicht blockierend:', options: { fontSize: 14, bold: true, color: THEME.colors.accent1 } },
  { text: ' YouTube async Enqueue\n', options: { fontSize: 13, color: THEME.colors.lt2 } },
  { text: '✅ Extensible Assets:', options: { fontSize: 14, bold: true, color: THEME.colors.accent1 } },
  { text: ' Unified Manager + Pluggable Renderers', options: { fontSize: 13, color: THEME.colors.lt2 } }
], { x: 0.5, y: 1.3, w: 9, h: 3.8, fontFace: THEME.bodyFontFace });

// Slide 17: Closing
slide = pres.addSlide();
slide.background = { color: THEME.colors.accent1 };
slide.addText('CorvinOS Video Producer Skill 2.0', {
  x: 0.5, y: 1.8, w: 9, h: 0.8,
  fontSize: 44, bold: true, color: THEME.colors.lt1, align: 'center', fontFace: THEME.headFontFace
});
slide.addText('Powered by Maestro + Workers + Learning Loop', {
  x: 0.5, y: 2.8, w: 9, h: 0.6,
  fontSize: 20, color: THEME.colors.lt2, align: 'center', fontFace: THEME.bodyFontFace
});
slide.addText('ADR-0692 • ADR-0695 • ADR-0705', {
  x: 0.5, y: 3.8, w: 9, h: 0.4,
  fontSize: 14, italic: true, color: THEME.colors.lt2, align: 'center', fontFace: THEME.bodyFontFace
});

// Save the presentation
pres.writeFile({ fileName: 'CorvinOS_VideoProducer_Presentation.pptx' });
console.log('✅ Presentation created: CorvinOS_VideoProducer_Presentation.pptx');
