/** Source material for a video: attachments are turned into text by the console, then travel with the job request. */
import { ApiError, BASE } from "@/lib/api/client";

export interface VideoSource { name: string; text: string; chars: number; truncated: boolean }

export const MAX_VIDEO_SOURCES = 4;
export const VIDEO_SOURCE_ACCEPT = ".txt,.md,.markdown,.json,.csv,.yaml,.yml,.log,.pdf";

export async function extractVideoSources(files: File[], csrf: string): Promise<VideoSource[]> {
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  const res = await fetch(`${BASE}/video/attachments/extract`, {
    method: "POST", body: form, credentials: "include", headers: { "X-CSRF-Token": csrf },
  });
  const text = await res.text();
  let payload: unknown = text;
  try { payload = JSON.parse(text); } catch { /* keep text */ }
  if (!res.ok) throw new ApiError(res.status, payload);
  return (payload as { sources: VideoSource[] }).sources;
}
