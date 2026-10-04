<p align="center">
  <a href="../../README.md"><img src="../../assets/logo.svg" width="56" alt="CorvinOS"/></a>
</p>
<p align="center">
  <a href="../../README.md">Home</a> &middot;
  <a href="token-savings.md">Token savings</a> &middot;
  <a href="self-learning.md">Self-learning</a> &middot;
  <a href="skills-acp.md">Skills 2.0 &amp; ACP</a> &middot;
  <a href="operating-system.md">CorvinOS as an OS</a> &middot;
  <a href="organizations.md">Organizations</a> &middot;
  <a href="a2a.md">A2A</a> &middot;
  <strong>Video</strong> &middot;
  <a href="marketplace.md">Marketplace &amp; plugins</a> &middot;
  <a href="extensibility.md">Extensibility</a>
</p>

# Video producer

> **Type one sentence, get back a narrated explainer video with captions — produced on your own machine, behind the same gates as every other CorvinOS run.**

<p align="center"><img src="img/video-hero.svg" alt="Pipeline: a text task passes the L44, L34 and L35 gates; an LLM writes a storyboard of up to six scenes (local Ollama by default, Anthropic when a key is set and egress admits it); gTTS narrates each scene through Google; Pillow draws a slide per scene; ffmpeg encodes and joins; output is an MP4 with SRT captions, poster, slides and quality metrics." width="100%"/></p>

## What you get

- **A finished artefact, not a prompt.** One task text produces `output.mp4` plus `output.srt`, a poster image and one slide per scene.
- **Local by default.** The storyboard is written by a local Ollama model unless you deliberately provide an Anthropic key — and even then only if the L35 egress gate admits it for your tenant.
- **Gated like any other run.** The task passes the acceptable-use gate (L44), data classification (L34) and the egress gate (L35) before anything is generated.
- **Measurable output.** Each job reports per-step progress and ffprobe quality metrics; feedback on a single scene is recorded in the audit chain before it is stored.
- **A plugin you can take out.** It ships through the Corvin Marketplace; uninstall it and the sidebar entry is gone.

## How it works

1. **Task.** You submit a text task in the Video panel or with `POST /v1/console/video/jobs`. The job runs in the background; the request returns a `job_id` immediately.
2. **Gates.** The same pre-spawn function every console spawn site calls checks L44 acceptable use, capabilities, L34 classification and L35 egress — under the `video_producer` engine profile, because the narration text is sent to a cloud TTS service on every job.
3. **Storyboard.** An LLM turns the task into at most six scenes, each with narration and slide text. Default backend: local Ollama (`qwen3:1.7b`, sized for a CPU-only host). Anthropic is used only when `ANTHROPIC_API_KEY` is set and L35 admits `api.anthropic.com` for the tenant; any doubt or failure falls back to Ollama — the plugin never upgrades a job to more egress on its own.
4. **Narration.** gTTS synthesises one audio track per scene. **This calls Google's TTS endpoint** — the narration text leaves your machine.
5. **Slides.** Pillow renders one image per scene.
6. **Encode.** ffmpeg turns each slide plus its audio into a clip and joins them; captions are written as SRT from the scene timings.

<p align="center"><img src="img/video-status.svg" width="100%" alt="Live host routes under /v1/console/video: jobs, progress, download, captions, quality metrics, overview, poster and slides, scene feedback, settings. Not built or removed: YouTube upload (501), Blender, Three.js and Manim render tiers, screenshot capture and asset analysis."/></p>

### Where it fits

| Use | Why it works here |
|---|---|
| Onboarding and how-to clips for a team | A paragraph of process text becomes a one-minute narrated walkthrough with captions |
| Internal release notes | The changelog paragraph is the task; the SRT doubles as a transcript |
| Drafts for a human editor | Slides, poster and per-scene audio are separate artefacts you can rework |
| Privacy-sensitive drafts | Not a fit while narration uses gTTS — the text reaches Google |

### What each job leaves behind

- `output.mp4` — the joined clip, one slide per scene with its narration.
- `output.srt` — scene-timed captions, also usable as a transcript.
- `scenes/scene_NNN.{mp4,mp3,png}` — each scene's clip, voice track and slide, plus `metadata.json` and the storyboard.
- A poster image and one slide image per scene (`GET /videos/{id}/poster`, `GET /videos/{id}/scenes/{index}/slide`).
- Quality metrics measured from those files (`GET /jobs/{id}/quality-metrics`): ffprobe stream facts, caption coverage, planned vs rendered duration per scene, and a checklist whose score names its denominator — a check whose input is missing is `skip`, never a pass.
- The job record (status, current step, timings, error) in the tenant's video store. The output folder is fixed per tenant and cannot be redirected through settings.

The plugin's code lives entirely in the Marketplace (`plugins/contributor/media/video_producer`, about 1,000 lines). CorvinOS only hosts the HTTP routes and the panel slot; every route is tenant-scoped through the session record and writes need the session's CSRF token.

## What runs today

| Capability | Status | Where |
|---|---|---|
| Job API: create, list, status, per-step progress | **LIVE** | `core/console/corvin_console/routes/video_producer_api.py` |
| Storyboard (Ollama default, ≤ 6 scenes) | **LIVE** | Marketplace `video_producer/src/skill.py` |
| Storyboard via Anthropic | **GATED** — key set + L35 admits | `_storyboard_backend()` in the routes |
| gTTS narration, Pillow slides, ffmpeg encode + join, SRT | **LIVE** | Marketplace `video_producer/src/` |
| Pre-spawn gates L44 / L34 / L35 (engine profile `video_producer`) | **LIVE** | `core/console/corvin_console/_spawn_gates.py` |
| Download, captions, poster, per-scene slide | **LIVE** | `video_producer_api.py` |
| Quality metrics measured from the artefacts (ffprobe), overview | **LIVE** | `core/console/corvin_console/video_quality.py` |
| Scene feedback, audit-first | **LIVE** | `POST /jobs/{id}/scenes/{scene_id}/feedback` |
| Settings (TTS engine, max duration; output folder fixed per tenant) | **LIVE** | `GET/PUT /settings` |
| Sidebar entry | **LIVE** when the plugin is installed and enabled | console manifest |
| Video Quality panel | **GATED** — flag `video_producer_enabled` | console |
| YouTube upload | **NOT BUILT** — answers 501 | `POST /jobs/{id}/youtube` |
| Blender / Three.js / Manim tiers, screenshot capture, asset analysis | **NOT BUILT** — removed by ADR-0953 | — |

## Try it

1. Install the system tools: `ffmpeg`, plus Python packages `gTTS` and `Pillow` (the plugin's `requirements.txt` lists them). For the default storyboard, run [Ollama](https://ollama.com) locally and pull `qwen3:1.7b`.
2. In the console, open **Marketplace**, install **video_producer** and enable it. A **Video** entry appears in the sidebar.
3. Enter a task, for example *"Explain in one minute how our onboarding works"*, and watch the step progress. Download the MP4 and the SRT when it finishes.

From a script on the same machine, with a console session cookie and its CSRF token:

```bash
curl -s -X POST http://127.0.0.1:8765/v1/console/video/jobs \
  -H 'Content-Type: application/json' -H "X-CSRF-Token: $CSRF" -b "$COOKIE" \
  -d '{"task": "Explain in one minute how our onboarding works"}'
# → {"job_id": "job_1a2b3c4d", "status": "pending", "created_at": "…"}
curl -s -b "$COOKIE" http://127.0.0.1:8765/v1/console/video/jobs/job_1a2b3c4d/progress
curl -s -b "$COOKIE" -o video.mp4 http://127.0.0.1:8765/v1/console/video/videos/job_1a2b3c4d/download
```

## Honest limits

- **Narration goes to Google.** gTTS calls Google's TTS endpoint on every job. Do not put confidential text into a task unless that egress is acceptable; the L34/L35 gates exist precisely to refuse it where it is not.
- **Slides, not footage.** Output is narrated still slides. The 3D and animation tiers (Blender, Three.js, Manim), screenshot capture and asset analysis were removed (ADR-0953) — they had no working path.
- **No publishing.** YouTube upload answers 501.
- **Small storyboards.** At most six scenes; the default local model is chosen for CPU-only hosts, not for prose quality.
- **Console-only API.** The routes sit behind the single-operator console session; there is no separate public video API.

## Under the hood

- Host routes: `core/console/corvin_console/routes/video_producer_api.py`; spawn gates `core/console/corvin_console/_spawn_gates.py`.
- Plugin source: `Corvin-Marketplace/plugins/contributor/media/video_producer/` (`src/skill.py` storyboard + pipeline, `src/async_runner.py`, `src/storage.py`, `panel/`).
- `core/skills/video_producer/` in CorvinOS is maintainer tooling, not the shipped plugin.
- ADRs (Corvin-Knowledge): ADR-0953 (removal of the dead render tiers).
- Related: [Marketplace &amp; plugins](marketplace.md) · [CorvinOS as an OS](operating-system.md) · [Extensibility](extensibility.md)
