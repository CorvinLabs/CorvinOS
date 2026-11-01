/** Cinematic film mode of the video producer: options, designs (Penpot SVG exports) and the job request body. */

export interface FilmDesign { name: string; svg: string }

export const FILM_MINUTES = [2, 3, 4, 6, 8, 10] as const;
export const DEFAULT_FILM_MINUTES = 4;
export const MAX_FILM_DESIGNS = 12;
export const MAX_DESIGN_CHARS = 8_000_000;

/** A file name as the design name the screenplay refers to: letters, digits, dot, underscore, hyphen; 1-40 characters. */
export function designName(fileName: string, taken: string[]): string {
  const stem = fileName.replace(/\.svg$/i, "").normalize("NFKD").replace(/[^A-Za-z0-9_.-]+/g, "-").replace(/^[^A-Za-z0-9]+/, "").slice(0, 36);
  const base = stem || "design";
  let name = base;
  for (let n = 2; taken.includes(name); n++) name = `${base}-${n}`;
  return name;
}

export interface JobBodyInput {
  task: string; baseJobId?: string; styleId?: string | null; sources: Array<{ name: string; text: string }>;
  film: boolean; filmMinutes: number; designs: FilmDesign[];
}

/** The POST /video/jobs body. A film adds its keys only when it is on; a revision is always a slide video. */
export function jobBody(i: JobBodyInput): Record<string, unknown> {
  const body: Record<string, unknown> = {
    task: i.task, base_job_id: i.baseJobId, style_id: i.styleId ?? undefined,
    sources: i.sources.map(({ name, text }) => ({ name, text })),
  };
  if (i.film && !i.baseJobId) {
    body.mode = "cinematic";
    body.cinematic_minutes = i.filmMinutes;
    if (i.designs.length > 0) body.designs = i.designs.map(({ name, svg }) => ({ name, svg }));
  }
  return body;
}
