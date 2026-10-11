# Video Producer — operator guide

The console page `/console/app/video-producer` is the studio for the Video
Producer plugin (`plugins/contributor/media/video_producer` in the
marketplace; ADR-0692, ADR-0695, ADR-0696). It produces a video from a
task, plays it, measures it and lets you teach the producer scene by scene.

## Layout

One column, top to bottom (ADR-2246):

| Region | What it shows | Source |
|---|---|---|
| Stage | the selected video at full panel width, with progress while it renders; **Fullscreen** button (or `F`, not while typing); download | `/videos/{id}/download`, `/videos/{id}/poster` |
| Composer | directly under the stage: a text box with **attachment**, **microphone** and **hold Space to dictate** (hold ≥ 0.4 s, release to stop; a short tap types a space), **Enter** sends. Two modes: **New video** and **Revise: <selected video>** | `POST /v1/console/video/jobs` (CSRF) |
| Produced videos | every job as a card with poster, status, creation time, progress while rendering, a *revision* badge, and **Revise** on finished videos | `GET /v1/console/video/jobs`, `/videos/{id}/poster` |
| Tiles | videos produced (of all jobs), total runtime and size, mean checklist share **over measured videos only**, last activity | `GET /v1/console/video/overview` |
| Quality and learning · Quality | the job's **production signals** (value, threshold, verdict, `n of N`) from its report, then what `ffprobe` measured on the real artifacts (below); for a failed or cancelled job only the signals and the stage that failed | `GET /v1/console/video/jobs/{id}/quality-metrics` |
| Quality and learning · Learning | the ADR-0314 feedback events recorded for this job; approve / reject per scene | `/jobs/{id}/learning-metrics`, `POST /jobs/{id}/scenes/{scene}/feedback` (CSRF) |
| Settings | output folder, text-to-speech engine, max duration | `/settings` |

### Revising a video

Select a produced video (or press **Revise** on its card), describe the change ("make scene 2 shorter",
"add a diagram") and send. A **new** video is produced from the old storyboard plus your request; the original
stays. The new job carries `revision_of`. Scenes the request does not touch are kept by instruction to the
storyboard model — their wording is stable, but it is a full new production (storyboard, narration, render),
not a patch of the old clips.

### Attachments

The paperclip takes up to 4 files of at most 2 MiB each: text, Markdown, JSON, CSV, YAML, log, and PDF when
`pdftotext` is installed on the host. The text (at most 20 000 characters per file, 40 000 in total) becomes
source material the video is built on. It is not stored by the console; it travels with the job request, and
the same pre-spawn gates (house rules, data classification, egress) that check your task text check it.

Deep links: `/app/video-producer?job=<id>&tab=quality`. The former page
`/app/video-quality-metrics?job_id=<id>` only redirects there (2026-09-20).

## Quality is measured, not rated

`core/console/corvin_console/video_quality.py` reads the job's own files —
`output.mp4`, `output.srt`, `scenes/scene_NNN.{mp4,mp3,png}`, the storyboard
— and probes them with `ffprobe` (cached per path, size and mtime):

* **Stream facts:** container, duration, size, total bitrate; video codec,
  resolution, fps, pixel format; audio codec, sample rate, channels.
* **Captions:** cue count, covered seconds, coverage share, consecutive
  duplicates.
* **Scenes:** planned seconds (storyboard `duration_ms`) vs rendered seconds
  (clip), drift, slide and voice presence, narration word count.
* **Checklist:** container readable · resolution ≥ 720p · fps ≥ 24 · pixel
  format yuv420p · audio present · sample rate ≥ 22.05 kHz · captions
  present and ≥ 80 % coverage · no repeated cues · every scene rendered ·
  every scene voiced. Each is `pass`, `warn`, `fail` or `skip`. The former
  `timing` and `runtime` checks are gone (unreleased 1.5.0): they compared
  the storyboard model's length estimate with the voice-driven real length
  and failed on every real video. That number is the report's
  `plan_estimate_error` signal — shown as information, never a verdict.
* **Score:** `passed / checks that could run` — the denominator is shown
  ("9 of 12 checks"). A check without its input is `skip`, never a pass.
  Without `ffprobe` on the host the stream facts are absent and the page
  says so.

Until 2026-09-20 the endpoint returned one hard-coded record for any job
id ("h264 7200k", three scenes); the numbers on the page were never about
the video. On the maintainer host the first measured job showed 10 of 10
scenes rendered, captions 99.9 % covered with 2 repeated cues, and 44 s
rendered against 14 s planned — real findings about the pipeline.

## Production report (unreleased 1.5.0, ADR-2247)

Every job writes `videos/<job>/report.json` (schema 1, at most 64 KB) next to its video — when it completes,
and also when it fails or is cancelled inside the job (then it names the stage that failed and the exception
class). It records stage timings, one entry per scene (TTS tier, narrated seconds, renderer, template, beats
source), the storyboard model's token usage as the backend reported it, every fallback the pipeline took (a
Pillow slide, a quote in place of a template, a lower TTS tier, a storyboard backend fallback, lost grounding,
a repaired storyboard) and the runtime **signals** — `degradation_rate`, `layout_clean`, `fact_support`,
`mux_integrity`, `style_fidelity`, plus `plan_estimate_error` and `beats_provenance` as information — each
with value, threshold, verdict and `n of N`. When `degradation_rate` fails, the other gated signals are marked
`valid: false`. The report never contains the task or narration text. Jobs from before 1.5.0 have none.

**In the console (T-0091).** `GET /v1/console/video/jobs/{id}/quality-metrics` merges the report into the
measured record under `report`, read from the session tenant's own store only (a second tenant asking for the
job id gets 404). `report.state` is `present`, `missing` (no report — jobs from before 1.5.0), `not_written`
(the job refused its own report on write) or `unreadable` (`reason`: `not_json`, `too_large`, `unknown_schema`,
`other_job`, or a symlink, which is never followed). Signals pass through as the job recorded them; an unknown
verdict reads `unknown`, never a pass. The Quality tab draws one row per signal — **Value**, **Threshold**,
**Verdict** (a colour mark plus its word), **n of N** — with the gate `degradation_rate` first; a signal it
invalidated reads "not valid". A share over fewer than 10 scenes is written `n of N`, not as a percentage, and
says so. A job without a report shows "No production report for this job" and no signal.
Proof: `core/console/tests/test_video_producer_quality_report_e2e.py` (real router, session, two tenants, the
report a real job wrote, a real MP4), `web-next/tests/unit/video-quality-signals.test.ts` (encodings),
and in a real browser `npx playwright test -c playwright.video-quality.config.ts` (web-next; an isolated console
on :8796 with a throwaway `CORVIN_HOME`, the real report, light and dark theme, another tenant's job 404).

## Running, budget and cancel (unreleased 1.5.0, ADR-2247)

* **Each production runs in its own process** (its own process group, started by the console with its
  interpreter). Progress still travels through the job record the panel polls. A console restart closes
  jobs it orphaned as `error` (reason `interrupted`).
* **Every external call has a deadline** (`src/deadline.py`): ffmpeg, ffprobe, the storyboard model, TTS,
  the browser. A call that overruns is stopped with its whole process tree; a hung browser is killed
  together with its driver. A raw `subprocess.run` in the plugin is a test failure.
* **A job has a wall-clock budget** of `min(3 x max_duration_minutes x 60, 2700)` seconds; when it runs out
  the job ends `error` and the message names what was running. The console adds a last-resort kill 60 s
  after the budget for a job process that cannot be reached any other way.
* **Cancel.** While a video is being produced the stage shows **Cancel production**. It calls
  `POST /v1/console/video/jobs/{id}/cancel` (session + CSRF, your workspace's jobs only): the job's process
  group gets SIGTERM, then SIGKILL after 5 s, and the job ends `cancelled`. A cancel of a queued job just
  removes it. The answer is `cancelled`, or `cancelling` if the record had not settled after 8 s. A finished
  job answers 409; another workspace's job id answers 404. A cancel is recorded as the job's one
  terminal audit event (`video_producer.job_cancelled`, below).
* **Lifecycle audit (T-0090).** The console - never the plugin - writes each job's lifecycle into the
  workspace's hash-chained audit log, from what it observes: `video_producer.job_started` when the runner
  accepted the job; exactly **one** terminal event - `job_completed`, `job_failed` or `job_cancelled` - when
  the job's record turns terminal (written by the cancel route, the host's job watcher, or at console start);
  and `job_degraded` after a completed job whose report lists degradations. `job_failed` carries a closed
  `reason`: `interrupted` (the console died mid-job; closed and audited by a background pass at the next
  console start, `start_boot_audit`, in both hosts), `child_exit`, `budget`, `job_error`, `start_failed`.
  Fields are ids, counts, durations and closed names read from the job's `report.json` (plugin version,
  scene count, web/Pillow scenes, TTS tiers, degradation count and kinds, verdict, failed stage, error
  class) - never the task, a narration or an error message. The events are **best-effort**: a chain write
  that does not commit is logged and counted (`lifecycle_audit_failures` in `GET /v1/console/video/overview`)
  and never fails or stops the video. Proof: `core/console/tests/test_video_producer_lifecycle_audit_e2e.py`
  (real router, session + CSRF, the plugin's real process runner, two tenants, chain verified).
* **Fallback.** `_JOB_RUNNER = "thread"` in `routes/video_producer_api.py` runs jobs on threads again. Cancel
  then stops the job's in-flight external process and ends it at the next step; a blocking SDK call (a
  TTS or model request) cannot be killed and is abandoned until it returns or its deadline passes.
* **Not done yet:** Art. 17 erasure does not stop a running job; there is no delete (PLAN-0946 R3).

## Troubleshooting

* **"The Video Producer plugin is not available on this build."** The
  console could not import the plugin's `src/` (`models.py`, `storage.py`).
  The loader tries, in order: `<CORVIN_HOME>/tenants/_default/plugins/instances/video_producer/src`,
  `<CORVIN_HOME>/plugins/media/video_producer/src`, `~/.corvin/plugins/…`,
  the sibling checkout `../Corvin-Marketplace/plugins/contributor/media/video_producer/src`,
  then `CORVIN_MARKETPLACE_ROOT`. The overview reports which one loaded
  (`plugin_source`).
* **Quality tab says "not measurable"** — no `output.mp4` for the job, or
  `ffprobe` is missing (`which ffprobe`).
* **Jobs live** under `~/.corvin/video-producer/{jobs,videos}` (the plugin's
  own storage root).

## Proof

`core/console/tests/test_video_producer_cancel_e2e.py` drives the cancel route through the real router with a
job hung in a SIGTERM-ignoring `ffmpeg` and with a live Chromium, and reads /proc for what is left;
`tests/e2e/test_video_quality_console_e2e.py` renders a two-second MP4 with
ffmpeg inside the test and asserts the measured facts and the checklist over
HTTP; `web-next/tests/unit/video-producer-page.test.tsx` covers the studio.
