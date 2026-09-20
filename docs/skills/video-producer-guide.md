# Video Producer — operator guide

The console page `/console/app/video-producer` is the studio for the Video
Producer plugin (`plugins/contributor/media/video_producer` in the
marketplace; ADR-0692, ADR-0695, ADR-0696). It produces a video from a
task, plays it, measures it and lets you teach the producer scene by scene.

## Layout

| Region | What it shows | Source |
|---|---|---|
| Tiles | videos produced (of all jobs), total runtime and size, mean checklist share **over measured videos only**, last activity | `GET /v1/console/video/overview` |
| New production | the task text; **Start production** creates a job | `POST /v1/console/video/jobs` (CSRF) |
| Library | every job with poster (first rendered slide), status, creation time, progress while rendering | `GET /v1/console/video/jobs`, `/videos/{id}/poster` |
| Studio · Playback | the MP4 with controls and download, the transcript from `output.srt` | `/videos/{id}/download`, `/videos/{id}/captions` |
| Studio · Quality | what `ffprobe` measured on the real artifacts (below) | `GET /v1/console/video/jobs/{id}/quality-metrics` |
| Studio · Learning | the ADR-0314 feedback events recorded for this job; approve / reject per scene | `/jobs/{id}/learning-metrics`, `POST /jobs/{id}/scenes/{scene}/feedback` (CSRF) |
| Settings | output folder, text-to-speech engine, max duration | `/settings` |

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
  scene timing within 10 % · runtime within 10 % of the plan · every scene
  voiced. Each is `pass`, `warn`, `fail` or `skip`.
* **Score:** `passed / checks that could run` — the denominator is shown
  ("9 of 12 checks"). A check without its input is `skip`, never a pass.
  Without `ffprobe` on the host the stream facts are absent and the page
  says so.

Until 2026-09-20 the endpoint returned one hard-coded record for any job
id ("h264 7200k", three scenes); the numbers on the page were never about
the video. On the maintainer host the first measured job showed 10 of 10
scenes rendered, captions 99.9 % covered with 2 repeated cues, and 44 s
rendered against 14 s planned — real findings about the pipeline.

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

`tests/e2e/test_video_quality_console_e2e.py` renders a two-second MP4 with
ffmpeg inside the test and asserts the measured facts and the checklist over
HTTP; `web-next/tests/unit/video-producer-page.test.tsx` covers the studio.
