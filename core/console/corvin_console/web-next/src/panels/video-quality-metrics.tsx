/**
 * Video Quality Metrics — folded into the Video Producer studio (2026-09-20).
 *
 * The standalone page rendered a hard-coded record (three scenes, "h264
 * 7200k") for ANY job id, including ones that did not exist, and drew a
 * convergence line from synthetic learning data. The measured quality of a
 * produced video now lives on /app/video-producer, "Quality" tab, read from
 * the job's real artifacts via ffprobe. This route stays mounted for
 * deep-link stability (?job_id=…) and is dropped from the sidebar — see
 * NAV_EXEMPT in tests/unit/panel-nav-wiring.test.ts.
 */
import { Navigate, useLocation } from "react-router-dom";

export function VideoQualityMetricsPanel() {
  const { search } = useLocation();
  const job = new URLSearchParams(search).get("job_id");
  const params = new URLSearchParams({ tab: "quality" });
  if (job) params.set("job", job);
  return <Navigate to={`/app/video-producer?${params.toString()}`} replace />;
}

export default VideoQualityMetricsPanel;
