/**
 * Video styles in the Video Producer panel against MSW: the composer's style chip and the style_id it sends,
 * a .pptx offered as a style (never uploaded to text extraction), the import dialog (edit -> one debounced
 * preview -> save payload with every unknown field untouched), server refusals shown, quota, and the Styles list.
 */
/* eslint-disable @typescript-eslint/no-explicit-any -- loosely typed request captures */
import type React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { delay, http, HttpResponse } from "msw";
import { server } from "../fixtures/server";
import { accentGlow, contrastRatio, mix, setColour, setFamily, type Draft } from "@/lib/api/video-styles";

vi.mock("recharts", async (importOriginal) => {
  const mod = await importOriginal<typeof import("recharts")>();
  return { ...mod, ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div> };
});
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ session: { tenant_id: "_default", csrf_token: "csrf-test", tier: "owner" }, loading: false, refresh: vi.fn(), logout: vi.fn() }),
}));
vi.mock("@/hooks/use-voice-input", () => ({ useVoiceInput: () => ({ recording: false, startRecording: vi.fn(), stopRecording: vi.fn() }) }));

import { VideoProducerPage } from "@/pages/video-producer";

const B = "/v1/console/video";
const PNG = "iVBORw0KGgo=";
const DRAFT: Draft = {
  name: "Acme deck", default_theme: "dark", decor: "minimal",
  brand: { wordmark: "Acme", intro_mark: true, credit: false },
  tokens: {
    dark: { bg: "#101820", bg_card: "#18222d", border: "#2a3744", text: "#f2f2f2", text_muted: "#9aa7b4", text_faint: "#6b7886", accent: "#0b7285", accent_hi: "#3bc9db", glow: "rgba(11, 114, 133, 0.35)", success: "#40c057" },
    light: { bg: "#ffffff", bg_card: "#f4f6f8", border: "#d0d7de", text: "#111111", text_muted: "#555555", text_faint: "#888888", accent: "#0b7285", accent_hi: "#0b5d6e", glow: "rgba(11, 114, 133, 0.2)", success: "#2b8a3e" },
    typography: { heading_family: "Newsreader", body_family: "Instrument Sans", mono_family: "JetBrains Mono" },
  },
  fonts: { mapping: [{ from: "Georgia", to: "Newsreader", reason: "closest serif" }, { from: "Verdana", to: "Instrument Sans", reason: "closest sans" }] },
  warnings: ["Embedded fonts were not used."],
  source: { kind: "pptx", sha256: "a".repeat(64), deck_aspect: "16:9", imported_at: null },
  mark_png_b64: PNG, x_future_field: { keep: [1, 2, 3] },
};
const PREVIEWS = ["hero", "diagram", "quote"].map((template) => ({ template, theme: "dark", data_uri: `data:image/png;base64,${PNG}` }));
const SAVED = { id: "sty_aaaaaaaa", name: "Acme deck", source: { kind: "pptx", sha256: null, deck_aspect: "16:9", imported_at: "2026-10-10T10:00:00Z" }, default_theme: "dark", decor: "minimal",
  brand: { wordmark: "Acme", has_mark: true, intro_mark: true, credit: false }, warnings: [], mark_data_uri: `data:image/png;base64,${PNG}` };
const LIST_EMPTY = { styles: [], default_style_id: null, builtin: { id: "corvin", name: "CorvinOS" }, limits: { max_styles: 3, max_upload_bytes: 26214400 } };
const LIST_ONE = { ...LIST_EMPTY, styles: [SAVED] };
const JOBS = { total: 1, jobs: [{ id: "job_1", task: "HTTP vs HTTPS", status: "complete", created_at: "2026-09-13T08:11:46", percent: 100 }] };

const calls = { extract: 0, previews: [] as any[], saves: [] as any[], jobs: [] as any[], puts: [] as any[], deletes: [] as string[], imports: 0 };
let list: any = LIST_EMPTY;
let importPreviews: any[] = PREVIEWS;
let saveStatus = 201;
let stylesStatus = 200;
let importRefusal: string | null = null;
let previewRefusal: string | null = null;

function handlers() {
  return [
    http.get(`${B}/overview`, () => HttpResponse.json({ jobs_total: 1, by_status: {}, videos: 1, runtime_s: 1, size_bytes: 1, measured_videos: 0, mean_score_share: null, last_activity: null, ffprobe_available: true, plugin_source: null })),
    http.get(`${B}/jobs`, () => HttpResponse.json(JOBS)),
    http.get(`${B}/jobs/job_1`, () => HttpResponse.json(JOBS.jobs[0])),
    http.get(`${B}/jobs/job_1/quality-metrics`, () => HttpResponse.json({}, { status: 404 })),
    http.get(`${B}/settings`, () => HttpResponse.json({ output_folder: "x", tts_engine: "openai", tts_engines: ["openai"], max_duration_minutes: 60, openai_configured: true, web_slides_available: true })),
    http.get(`${B}/styles`, () => (stylesStatus === 200 ? HttpResponse.json(list) : HttpResponse.json({ detail: "n/a" }, { status: stylesStatus }))),
    http.get(`${B}/styles/:id/preview`, () => HttpResponse.json({ previews: PREVIEWS })),
    http.post(`${B}/styles/import`, async () => { calls.imports++; if (importRefusal) return HttpResponse.json({ detail: importRefusal }, { status: 415 }); return HttpResponse.json({ draft: DRAFT, previews: importPreviews, notes: ["The deck has 12 slides."] }); }),
    http.post(`${B}/styles/preview`, async ({ request }) => { calls.previews.push(await request.json()); if (previewRefusal) return HttpResponse.json({ detail: previewRefusal }, { status: 422 }); return HttpResponse.json({ previews: PREVIEWS }); }),
    http.post(`${B}/styles`, async ({ request }) => {
      calls.saves.push({ csrf: request.headers.get("x-csrf-token"), body: await request.json() });
      return saveStatus === 201 ? HttpResponse.json({ style: SAVED }, { status: 201 }) : HttpResponse.json({ detail: "This style name is not allowed." }, { status: saveStatus });
    }),
    http.put(`${B}/styles/default`, async ({ request }) => { calls.puts.push(await request.json()); return HttpResponse.json({}); }),
    http.delete(`${B}/styles/:id`, ({ params }) => { calls.deletes.push(String(params.id)); return new HttpResponse(null, { status: 204 }); }),
    http.post(`${B}/attachments/extract`, () => { calls.extract++; return HttpResponse.json({ sources: [] }); }),
    http.post(`${B}/jobs`, async ({ request }) => { calls.jobs.push(await request.json()); return HttpResponse.json({ job_id: "job_new", status: "pending", created_at: "x" }); }),
  ];
}
function renderIt(search = "") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  server.use(...handlers());
  return render(<MemoryRouter initialEntries={[`/app/video-producer${search}`]}><QueryClientProvider client={qc}><VideoProducerPage /></QueryClientProvider></MemoryRouter>);
}
afterEach(() => {
  cleanup(); list = LIST_EMPTY; importPreviews = PREVIEWS; saveStatus = 201; stylesStatus = 200; importRefusal = null; previewRefusal = null;
  Object.assign(calls, { extract: 0, previews: [], saves: [], jobs: [], puts: [], deletes: [], imports: 0 });
});
const deck = (name = "brand.pptx") => new File(["PK"], name);
async function openDialog() {
  fireEvent.change(await screen.findByTestId("deck-input"), { target: { files: [deck()] } });
  await screen.findByTestId("style-name");
}
const send = async (text: string) => { fireEvent.change(await screen.findByTestId("task-input"), { target: { value: text } }); fireEvent.click(screen.getByTestId("start-production")); await waitFor(() => expect(calls.jobs).toHaveLength(1)); };

describe("style helpers", () => {
  it("accent change rewrites the glow with the previous alpha", () => {
    expect(accentGlow("#ff0000", "rgba(11, 114, 133, 0.2)")).toBe("rgba(255, 0, 0, 0.2)");
    expect(accentGlow("#00ff00", undefined)).toBe("rgba(0, 255, 0, 0.35)");
    const next = setColour(DRAFT, "dark", "accent", "#ff8800");
    expect(next.tokens.dark.glow).toBe("rgba(255, 136, 0, 0.35)");
    expect(next.tokens.light).toBe(DRAFT.tokens.light);
    expect(setColour(DRAFT, "dark", "bg", "#000000").tokens.dark.glow).toBe(DRAFT.tokens.dark.glow);
  });
  it("contrast ratio follows WCAG", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 1);
    expect(contrastRatio("#777777", "#777777")).toBeCloseTo(1, 5);
    expect(contrastRatio("#ffffff", "#000000")).toBe(contrastRatio("#000000", "#ffffff"));
  });
  it("a font change updates the matching mapping entry and nothing else", () => {
    const next = setFamily(DRAFT, "heading_family", "JetBrains Mono");
    expect(next.tokens.typography.heading_family).toBe("JetBrains Mono");
    expect(next.fonts.mapping[0]).toMatchObject({ from: "Georgia", to: "JetBrains Mono" });
    expect(next.fonts.mapping[1]).toEqual(DRAFT.fonts.mapping[1]);
    expect(next.x_future_field).toBe(DRAFT.x_future_field);
  });
});

describe("composer style chip", () => {
  it("shows the built-in by default and lists built-in, saved styles and the import action", async () => {
    list = LIST_ONE;
    renderIt();
    expect((await screen.findByTestId("style-chip")).textContent).toMatch(/Style: CorvinOS/);
    fireEvent.click(screen.getByTestId("style-chip"));
    await screen.findByTestId("style-option-sty_aaaaaaaa");
    expect(screen.getByTestId("style-option-corvin")).toBeInTheDocument();
    expect(screen.getByTestId("style-import").textContent).toMatch(/Import from PowerPoint/);
  });

  it("the tenant default is shown, and a new job sends no style_id unless one was picked", async () => {
    list = { ...LIST_ONE, default_style_id: "sty_aaaaaaaa" };
    renderIt();
    await waitFor(() => expect(screen.getByTestId("style-chip").textContent).toMatch(/Style: Acme deck/));
    await send("Explain it");
    expect(calls.jobs[0]).not.toHaveProperty("style_id");
  });

  it("a picked style travels with the job; the built-in is sent explicitly as corvin", async () => {
    list = { ...LIST_ONE, default_style_id: "sty_aaaaaaaa" };
    renderIt();
    fireEvent.click(await screen.findByTestId("style-chip"));
    fireEvent.click(await screen.findByTestId("style-option-corvin"));
    expect(screen.getByTestId("style-chip").textContent).toMatch(/Style: CorvinOS/);
    await send("Explain it");
    expect(calls.jobs[0].style_id).toBe("corvin");
    expect(calls.puts).toHaveLength(0); // choosing is per session; it never rewrites the default
  });

  it("a revision keeps its base video's style until one is picked", async () => {
    list = LIST_ONE;
    renderIt("?job=job_1");
    fireEvent.click(await screen.findByTestId("revise-job_1"));
    expect(screen.getByTestId("style-chip").textContent).toMatch(/same as original/);
    await send("Shorter");
    expect(calls.jobs[0]).toMatchObject({ base_job_id: "job_1" });
    expect(calls.jobs[0]).not.toHaveProperty("style_id");
  });
});

describe("attaching a deck", () => {
  it("a .pptx is offered as a style and never reaches text extraction", async () => {
    renderIt();
    fireEvent.change(await screen.findByTestId("attach-input"), { target: { files: [deck("template.potx")] } });
    const offer = await screen.findByTestId("deck-offer");
    expect(offer.textContent).toMatch(/cannot be used as source material/);
    expect(calls.extract).toBe(0);
    expect(screen.queryByTestId("source-chip")).toBeNull();
    fireEvent.click(screen.getByTestId("use-as-style"));
    await screen.findByTestId("style-dialog");
    await screen.findByTestId("style-name");
    expect(calls.imports).toBe(1);
    expect(calls.extract).toBe(0);
  });

  it("a mixed selection extracts the text files only", async () => {
    renderIt();
    fireEvent.change(await screen.findByTestId("attach-input"), { target: { files: [new File(["x"], "notes.md"), deck()] } });
    await screen.findByTestId("deck-offer");
    await waitFor(() => expect(calls.extract).toBe(1));
  });
});

describe("import dialog", () => {
  it("shows loading, then the draft, previews, warnings, font mapping and a credit that is off by default", async () => {
    renderIt();
    fireEvent.change(await screen.findByTestId("deck-input"), { target: { files: [deck()] } });
    expect(screen.getByTestId("style-loading")).toBeInTheDocument();
    await screen.findByTestId("style-name");
    expect(screen.getByTestId("preview-frames").querySelectorAll("img")).toHaveLength(3);
    expect(screen.getByTestId("style-warnings").textContent).toMatch(/Embedded fonts were not used\./);
    expect(screen.getByTestId("font-mapping").textContent).toMatch(/Georgia → Newsreader \(closest serif\)/);
    expect((screen.getByTestId("style-credit") as HTMLInputElement).checked).toBe(false);
    expect((screen.getByTestId("style-name") as HTMLInputElement).maxLength).toBe(60);
    expect((screen.getByTestId("style-wordmark") as HTMLInputElement).maxLength).toBe(40);
    expect(Array.from((screen.getByTestId("font-heading_family") as HTMLSelectElement).options).map((o) => o.value)).toEqual(["Newsreader", "Instrument Sans", "JetBrains Mono"]);
  });

  it("no previews is an honest empty state, not sample frames", async () => {
    importPreviews = [];
    renderIt();
    await openDialog();
    expect(screen.getByTestId("previews-empty").textContent).toBe("Previews are not available on this installation.");
    expect(screen.queryByTestId("preview-frames")).toBeNull();
  });

  it("rapid edits cause one debounced preview call, and the save carries the edits with every other field untouched", async () => {
    renderIt();
    await openDialog();
    fireEvent.change(screen.getByTestId("swatch-accent"), { target: { value: "#ff0000" } });
    fireEvent.change(screen.getByTestId("swatch-accent"), { target: { value: "#00ff00" } });
    fireEvent.change(screen.getByTestId("style-name"), { target: { value: "  Brand look " } });
    fireEvent.change(screen.getByTestId("font-body_family"), { target: { value: "Newsreader" } });
    fireEvent.click(screen.getByTestId("style-credit"));
    expect(calls.previews).toHaveLength(0);
    await waitFor(() => expect(calls.previews).toHaveLength(1), { timeout: 3000 });
    expect(calls.previews[0].draft.tokens.dark.accent).toBe("#00ff00");
    fireEvent.click(screen.getByTestId("style-save"));
    await waitFor(() => expect(calls.saves).toHaveLength(1));
    const { csrf, body } = calls.saves[0];
    expect(csrf).toBe("csrf-test");
    expect(body.set_default).toBe(false);
    const expected = structuredClone(DRAFT);
    expected.name = "Brand look";
    expected.tokens.dark.accent = "#00ff00";
    expected.tokens.dark.glow = "rgba(0, 255, 0, 0.35)";
    expected.tokens.typography.body_family = "Newsreader";
    expected.fonts.mapping[1] = { from: "Verdana", to: "Newsreader", reason: "Chosen by you" };
    expected.brand.credit = true;
    expect(body.draft).toEqual(expected); // incl. source and x_future_field
    await waitFor(() => expect(screen.queryByTestId("style-dialog")).toBeNull());
  });

  it("the saved style is the choice for the next video even while the refetched list is still on its way", async () => {
    renderIt();
    await openDialog();
    // The server list changes only on save, and its answer arrives late: the page must not drop the new choice meanwhile.
    server.use(
      http.post(`${B}/styles`, async () => { list = LIST_ONE; return HttpResponse.json({ style: SAVED }, { status: 201 }); }),
      http.get(`${B}/styles`, async () => { await delay(400); return HttpResponse.json(list); }),
    );
    fireEvent.click(screen.getByTestId("style-save"));
    await waitFor(() => expect(screen.queryByTestId("style-dialog")).toBeNull());
    expect(screen.getByTestId("style-chip").textContent).toContain("Acme deck");
    await new Promise((r) => setTimeout(r, 700)); // the late refetch has landed
    expect(screen.getByTestId("style-chip").textContent).toContain("Acme deck");
    await send("A video in the new look");
    expect(calls.jobs[0].style_id).toBe("sty_aaaaaaaa");
  });

  it("Save and set as default asks for the default; removing the logo clears it", async () => {
    renderIt();
    await openDialog();
    fireEvent.click(screen.getByTestId("remove-logo"));
    expect(screen.getByTestId("style-no-logo")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("style-save-default"));
    await waitFor(() => expect(calls.saves).toHaveLength(1));
    expect(calls.saves[0].body.set_default).toBe(true);
    expect(calls.saves[0].body.draft.mark_png_b64).toBeNull();
  });

  it("contrast hints warn under 4.5:1", async () => {
    renderIt();
    await openDialog();
    expect(screen.getByTestId("contrast-text").textContent).not.toMatch(/hard to read/);
    fireEvent.change(screen.getByTestId("swatch-text"), { target: { value: "#151f2a" } });
    expect(screen.getByTestId("contrast-text").textContent).toMatch(/below 4\.5:1/);
  });

  it("Save is disabled while the name is empty", async () => {
    renderIt();
    await openDialog();
    fireEvent.change(screen.getByTestId("style-name"), { target: { value: "   " } });
    expect((screen.getByTestId("style-save") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByTestId("style-save-default") as HTMLButtonElement).disabled).toBe(true);
  });

  it("a server refusal on save is shown inline and the dialog stays open", async () => {
    saveStatus = 403;
    renderIt();
    await openDialog();
    fireEvent.click(screen.getByTestId("style-save"));
    await waitFor(() => expect(screen.getByTestId("style-error").textContent).toMatch(/This style name is not allowed\./));
    expect(screen.getByTestId("style-dialog")).toBeInTheDocument();
    expect((screen.getByTestId("style-save") as HTMLButtonElement).disabled).toBe(false);
  });

  it("an import the server refuses says why", async () => {
    importRefusal = "Macro-enabled decks are not accepted.";
    renderIt();
    fireEvent.change(await screen.findByTestId("deck-input"), { target: { files: [deck("x.pptx")] } });
    await waitFor(() => expect(screen.getByTestId("style-error").textContent).toMatch(/Macro-enabled decks/));
  });
});

describe("styles list and limits", () => {
  it("empty state, and import is disabled with the reason when the quota is full", async () => {
    renderIt();
    fireEvent.click(await screen.findByTestId("styles-toggle"));
    expect((await screen.findByTestId("styles-empty")).textContent).toMatch(/No styles yet/);
    cleanup();
    list = { ...LIST_EMPTY, styles: [SAVED, { ...SAVED, id: "sty_bbbbbbbb" }, { ...SAVED, id: "sty_cccccccc" }] };
    renderIt();
    fireEvent.click(await screen.findByTestId("styles-toggle"));
    await screen.findByTestId("styles-list");
    expect((screen.getByTestId("styles-import") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByTestId("styles-quota").textContent).toMatch(/limit of 3 styles/);
    fireEvent.click(screen.getByTestId("style-chip"));
    expect((screen.getByTestId("style-import") as HTMLButtonElement).disabled).toBe(true);
  });

  it("set as default PUTs the id; delete asks first and then calls DELETE", async () => {
    list = LIST_ONE;
    renderIt();
    fireEvent.click(await screen.findByTestId("styles-toggle"));
    const row = await screen.findByTestId("style-row-sty_aaaaaaaa");
    expect(row.textContent).toMatch(/From a \.pptx deck/);
    fireEvent.click(within(row).getByTestId("style-default-sty_aaaaaaaa"));
    await waitFor(() => expect(calls.puts).toEqual([{ style_id: "sty_aaaaaaaa" }]));
    fireEvent.click(within(row).getByTestId("style-delete-sty_aaaaaaaa"));
    expect(calls.deletes).toHaveLength(0);
    fireEvent.click(within(row).getByTestId("style-delete-confirm-sty_aaaaaaaa"));
    await waitFor(() => expect(calls.deletes).toEqual(["sty_aaaaaaaa"]));
  });

  it("a build without styles says so and offers no import", async () => {
    stylesStatus = 501;
    renderIt();
    fireEvent.click(await screen.findByTestId("styles-toggle"));
    expect((await screen.findByTestId("styles-unavailable")).textContent).toMatch(/not available on this installation/);
    expect((screen.getByTestId("styles-import") as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("style editing parity with the server rules", () => {
  it("mix() matches style_pack.mix (Python rounding included)", () => {
    expect(mix("#101820", "#f2f2f2", 0.05)).toBe("#1b232a");
    expect(mix("#101820", "#f2f2f2", 0.14)).toBe("#30373d");
    expect(mix("#ffffff", "#111111", 0.05)).toBe("#f3f3f3");
    expect(mix("#101820", "#ffffff", 0.38)).toBe("#6b7075");
    expect(mix("#000000", "#010101", 0.5)).toBe("#000000");
  });

  it("Background/Text edits re-derive the card and border like the importer; a hand-set card is kept", () => {
    const a = setColour(DRAFT, "dark", "bg", "#101820");
    expect(a.tokens.dark.bg_card).toBe("#1b232a");
    expect(a.tokens.dark.border).toBe(mix("#101820", "#f2f2f2", 0.14));
    const b = setColour(DRAFT, "dark", "text", "#ffffff", { deriveCard: false });
    expect(b.tokens.dark.bg_card).toBe(DRAFT.tokens.dark.bg_card);
    expect(b.tokens.dark.border).toBe(mix("#101820", "#ffffff", 0.14));
  });

  it("the dialog re-derives surfaces until the card colour is set by hand", async () => {
    renderIt();
    await openDialog();
    fireEvent.change(screen.getByTestId("swatch-bg"), { target: { value: "#000000" } });
    fireEvent.click(screen.getByTestId("style-save"));
    await waitFor(() => expect(calls.saves).toHaveLength(1));
    expect(calls.saves[0].body.draft.tokens.dark.bg_card).toBe(mix("#000000", "#f2f2f2", 0.05));
    cleanup(); Object.assign(calls, { saves: [], previews: [] });
    renderIt();
    await openDialog();
    fireEvent.change(screen.getByTestId("swatch-bg_card"), { target: { value: "#123456" } });
    fireEvent.change(screen.getByTestId("swatch-bg"), { target: { value: "#000000" } });
    fireEvent.click(screen.getByTestId("style-save"));
    await waitFor(() => expect(calls.saves).toHaveLength(1));
    expect(calls.saves[0].body.draft.tokens.dark.bg_card).toBe("#123456");
  });

  it("the full set of readability hints is shown and the new ones warn", async () => {
    renderIt();
    await openDialog();
    for (const id of ["text", "text_muted", "card-text", "card-muted", "accent", "accent_hi", "dim"]) {
      expect(screen.getByTestId(`contrast-${id}`)).toBeInTheDocument();
    }
    // bg and text almost equal: the card text, the out-of-focus rule and the muted text all fail
    fireEvent.change(screen.getByTestId("swatch-text"), { target: { value: "#1b2430" } });
    expect(screen.getByTestId("contrast-card-text").textContent).toMatch(/below 4\.5:1/);
    expect(screen.getByTestId("contrast-dim").textContent).toMatch(/below 2:1/);
    fireEvent.change(screen.getByTestId("swatch-accent_hi"), { target: { value: "#101820" } });
    expect(screen.getByTestId("contrast-accent_hi").textContent).toMatch(/below 1\.8:1/);
  });

  it("a server refusal of the preview is shown with its reason", async () => {
    previewRefusal = "dark: text on a card is 2.10:1, needs 4.5:1";
    renderIt();
    await openDialog();
    fireEvent.click(screen.getByTestId("style-credit"));
    await waitFor(() => expect(screen.getByTestId("preview-error").textContent).toMatch(/text on a card is 2\.10:1/), { timeout: 3000 });
  });

  it("the logo toggle refreshes the preview, and the copy says what the server does", async () => {
    renderIt();
    await openDialog();
    expect(screen.getByText("Wordmark (shown in the footer of every slide)")).toBeInTheDocument();
    expect(screen.getByTestId("theme-help").textContent).toBe("Videos use this theme throughout.");
    fireEvent.click(screen.getByTestId("style-intro-mark"));
    await waitFor(() => expect(calls.previews).toHaveLength(1), { timeout: 3000 });
    expect(calls.previews[0].draft.brand.intro_mark).toBe(false);
  });

  it("a damaged default is reported in the styles list", async () => {
    list = { ...LIST_ONE, default_style_error: "The default style is damaged; videos use the built-in look until you delete it or pick another." };
    renderIt();
    fireEvent.click(await screen.findByTestId("styles-toggle"));
    expect((await screen.findByTestId("styles-default-error")).textContent).toMatch(/damaged/);
  });
});
