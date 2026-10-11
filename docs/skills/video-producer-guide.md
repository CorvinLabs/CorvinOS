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
| Produced videos | every job as a card with poster, status, creation time, progress while rendering, a *revision* badge, **Revise** on finished videos and **Delete** (bin icon, with a confirmation) on finished, failed and cancelled ones | `GET /v1/console/video/jobs`, `/videos/{id}/poster`, `DELETE /v1/console/video/jobs/{id}` (CSRF) |
| Tiles | videos produced (of all jobs), total runtime and size, mean checklist share **over measured videos only**, last activity, **storage** used against the workspace's quota (a banner when it is full) | `GET /v1/console/video/overview` (`storage`) |
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
`mux_integrity`, `style_fidelity`, `still_time`, plus `plan_estimate_error`, `beats_provenance` and
`revision_locality` as information (and, on request, the post-hoc `cue_alignment`, below) — each
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

## Deleting videos, storage quota and erasure (unreleased 1.5.0, ADR-2247 R3)

* **Owner.** When a job is created the console writes who created it to `owners.json` in the workspace's
  store (job id → principal). The console has no per-user login - local-login is the workspace's owner - so a
  console job's owner is `console-owner:<tenant>`. `owners.json` and `revisions.json` are written through one
  locked read-modify-write with an atomic replace (`corvin_operator/bridges/shared/video_job_store.py`).
* **Delete.** The bin icon on a finished, failed or cancelled card asks *Delete this video?* (**Delete** /
  **Keep**). `DELETE /v1/console/video/jobs/{id}` (session + CSRF) removes the job record and its locks, the
  whole `videos/<id>/` directory (scenes, audio, posters, caches, the MP4, `metadata.json`, the production
  report), the run directory and thumbnails, and the job's entries in `owners.json` and `revisions.json` (a
  revision of a deleted video is then shown as a plain video). A job that is still being produced answers
  409 (cancel it first); another workspace's job id answers 404. The deletion is recorded **first**:
  `video_producer.job_deleted` (job id, end status, files and bytes removed - never the task) is written to
  the workspace's audit chain before anything is removed; if that record does not commit the answer is 503
  and the video stays. One job per request; there is no bulk delete.
* **Storage quota.** `spec.video_producer.storage_quota_gb` in the workspace's `tenant.corvin.yaml`, default
  **5** GB. The whole store counts (records, videos, styles). While the store holds as much as the quota or
  more, a new production (also a revision) is refused with 409 *Video storage is full (… of … used). Delete
  videos you no longer need to produce a new one.* - before any gate or model call. `GET
  /v1/console/video/overview` returns `storage` (`used_bytes`, `quota_bytes`, `quota_gb`, `full`); the panel
  shows it as a tile and a banner. A value that is not a positive number is ignored (the default applies).
  Not built: a retention period (`retention_days`).
* **Erasure (GDPR Art. 17).** The erasure layer `L-video-producer` deletes **whole** - through the same
  function as the delete route - every job owned by the subject or whose record names the subject under an
  identity key, and leaves every other job. The task text is never searched; jobs from before 1.5.0 have no
  owner entry. A matching job that is still being produced is not deleted under it: the layer fails and says
  how many, cancel and run the erasure again. The subject *being the workspace* removes the whole store.
  Details: `docs/claude-ref/layer-36-erasure.md`.
* Proof: `core/console/tests/test_video_producer_data_lifecycle_e2e.py` (real router, session + CSRF, two
  tenants: B deleting A's job is 404, a running job 409, every file gone from disk, chain verified; quota;
  erasure through the real `/erase` dispatch removing exactly the matching jobs; concurrent sidecar writers
  from threads and processes) and `web-next/tests/e2e/video-lifecycle/data-lifecycle.spec.ts` (a real
  browser against an isolated console: cancel, delete with confirmation, storage tile, full store refusing a
  production; `npx playwright test -c playwright.video-lifecycle.config.ts`).

## Tenant isolation (unreleased 1.5.0, ADR-2247 R4)

* **No scene can make anything fetch a page (F1).** Up to plugin 1.4.0 a model-written `screenshot_url`
  was opened by a browser on the console's loopback, where `GET /v1/console/auth/local-login` hands any
  loopback peer an owner session of tenant `_default` - so one workspace's video job was logged into
  another workspace's console. Since 1.4.1 the capture path is gone: `Scene.from_dict` drops
  `screenshot_url` from every storyboard (model output, operator storyboard, stored record) - always, not
  only when the task did not name the URL, because nothing would read it - and a `screenshot` scene renders
  as an ordinary slide; the web-slide browser aborts every request. Capturing a website is a separate,
  user-initiated design (ADR-2249 D4), not built.
  Proof: `core/console/tests/test_video_producer_tenant_isolation_e2e.py` - the real console served on a real
  loopback socket, two tenants, the requesting tenant's job created through the real route, the storyboard
  delivered as model output by a stand-in `claude` CLI. It is red against plugin 1.4.0 (the job's Chromium
  called `local-login`, was given a `_default` session cookie and the URL was persisted in the job record;
  run it with `VP_F1_PLUGIN_SRC=<1.4.0 src>`) and green from 1.4.1 on (no request reached the console, no URL
  stored, the video completes).
* **The plugin is loaded only for a workspace that enabled it (F3).** Importing the console executes no
  plugin code. The source is loaded on the first request of a workspace whose registry has the plugin
  installed **and** enabled (`_ensure_plugin(tenant_id)` in `routes/video_producer_api.py`, the one loader
  every path goes through - the router, the learning route `POST /video/jobs/{id}/feedback`, the boot audit).
  A workspace that did not enable it gets 404 and never causes a load, also after another workspace loaded
  it. The boot audit closes interrupted jobs only of workspaces that have the plugin enabled; another
  workspace's are closed at its first store open after enabling it again.
  Proof: `core/console/tests/test_video_producer_plugin_import_e2e.py` - a fresh interpreter imports the
  console with the plugin directory absent (an empty directory bind-mounted over it in a private mount
  namespace) and with it present: nothing of the plugin is imported, opened or written; over HTTP, a
  workspace with the plugin installed but not enabled gets 404 on both routers and the plugin stays unloaded.
* Not changed: once loaded, the plugin is one module set in the console process for every enabling workspace,
  and the first loader location, `<CORVIN_HOME>/tenants/_default/plugins/instances/…`, is the `_default`
  workspace's install - it serves every workspace. Jobs run in their own process (R1), which still inherits
  the console's whole environment (REVIEW-0039 F6).

## Post-hoc signals and the release lint (unreleased 1.5.0, ADR-2247 R5)

* **`still_time`** is a runtime signal now (it runs at the end of every job and is in the report): the share
  of the video's runtime in still stretches of at least 5 s (ADR-2245 definition), with the p90 over scenes of
  the longest still stretch. Verdict `warn` above 20 % dead share or above 8 s p90; `skip` without numpy. The
  code moved from the plugin's `scripts/measure_engagement.py` into its `src/signals.py`; the script calls it.
  Measured on the nine corpus videos of `tests/corpus/baseline.json` (2026-10-12, plugin
  `scripts/still_time_agreement.py`, 16 CPUs, load 3-13): the signal agrees with the pre-move script and
  with the recorded baseline to 0.0 points on all 9; it took 2.32-2.92 s per minute of video (pooled 2.58 s;
  the DoD limit is 15 s). The numbers are in the plugin's `tests/corpus/still_time_agreement.json`.
* **`cue_alignment`** - seconds between a reveal and the spoken word, median and p90, plus **coverage**
  (matched items / all items with a cue; a scene that fell back to a classic slide counts its items as
  unmatched) - is **post-hoc and on demand**: `POST /v1/console/video/jobs/{id}/measure` (session + CSRF,
  your workspace's finished jobs only; another workspace's job id is 404, an unfinished job 409, a second
  request while one runs 409). Every scene's narration audio is sent to OpenAI whisper-1, so the request
  passes the same pre-spawn gates as the job's own OpenAI TTS: L44 acceptable use, capabilities, L34 on the
  narration, L35 egress to the OpenAI host - and a grounded job's narration is classified `INTERNAL`, the
  class its knowledge pack was released under. A refusal (or no OpenAI key on the host) is not an error: the
  signal is stored in the report as `skip` with the reason and the answer carries the refusal text. The
  spoken time of an item is its first occurrence after the start of the sentence that names it (the beat
  sentence its cue was placed on), not the first mention in the scene. Audit: `video_producer.measure_started`
  is written **before** any audio leaves (no commit, nothing is sent: 503), then `measure_completed` (verdict,
  matched/items, median and p90 in ms) or `measure_refused` (`gate`: `pre_spawn` or `no_key`); ids and numbers
  only. Not built: a button in the Quality tab - the measurement is an API call today.
  Proof: `core/console/tests/test_video_producer_measure_e2e.py` (real router, session + CSRF, two tenants;
  whisper-1 answers from a recorded response of two scenes of the published German LDD video, the network is
  cut; L35 and L34 refusals, no key, chain verified).
* **`revision_locality`** (information): for a revision, the share of the base video's scenes whose narration
  the revision kept word-identical. The console hands the base video's narrations to the job
  (`revision_base_narrations`); the number is in the report, never the text.
* **`language_parity`** is checked when a video pair is stored (Corvin-Videos convention: every video in German
  and English): the plugin's `scripts/store_video_pair.py <videos> --de <job> --en <job> --dest
  <Corvin-Videos/<name>>` compares scene counts (within one), template sequence (at least 80 % identical
  positions) and the numeric facts of both narrations (locale-normalised: `77 %` = `77%` = `77 Prozent`,
  `1.234,5` = `1,234.5`), writes a "Language parity" block into `sources.md` and copies the two MP4s only when
  the pair passes; a failing pair is not stored (exit 1). The published LDD pair of 2026-10-09/10 fails it (4 of
  10 template positions; the German narration carries 1.5, 2 and 4.5 that the English one does not, the
  English a 4) - its `sources.md` now carries that block.
* **Release lint.** The plugin's `scripts/release_lint.py` (GitHub workflow
  `.github/workflows/video-producer-release-lint.yml` in Corvin-Marketplace) checks one version across
  `plugin.json`, `plugin.yaml`, `provider.py`, `setup.py` and the four marketplace index blocks, no placeholder
  token in the README, user guide and current release notes, and that every backticked test or plugin path in
  them exists. Its fixtures reproduce the three historical defects (index 1.0.0 against 1.1.0, provider/yaml
  1.0.0 inside 1.2.0, a `RESULTS_PLACEHOLDER`, a note naming
  `tests/test_api_routes.py::test_video_job_end_to_end`). Not checked yet: limits quoted in a guide against
  their constants.

## Troubleshooting

* **"The Video Producer plugin is not available on this build."** The
  console could not import the plugin's `src/` (`models.py`, `storage.py`).
  The loader tries, in order: `<CORVIN_HOME>/tenants/_default/plugins/instances/video_producer/src`,
  `<CORVIN_HOME>/plugins/media/video_producer/src`, `<CORVIN_HOME>/plugins/video_producer/src`,
  the sibling checkout `../Corvin-Marketplace/plugins/contributor/media/video_producer/src`,
  then `CORVIN_MARKETPLACE_ROOT`, then the marketplace cache `<CORVIN_HOME>/marketplace-cache/…`. It runs on
  the first request of a workspace that has the plugin enabled - never at console start, never for a
  workspace that did not enable it (that one gets 404). The overview reports which one loaded
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
