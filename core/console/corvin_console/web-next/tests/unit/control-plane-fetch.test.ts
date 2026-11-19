/**
 * controlPlaneFetch: which answers mean "not available on this build".
 *
 * A 404 on a MUTATION is the server saying that one plugin / snapshot /
 * subsystem does not exist. It used to be reported as "Not available on this
 * build." - the text for a missing feature - and the server's reason was lost.
 */
import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";
import {
  controlPlaneErrorText,
  controlPlaneFetch,
  controlPlanePollInterval,
  isControlPlaneUnavailable,
} from "@/lib/control-plane";

const URL = "/v1/console/control-plane/plugins";

async function failure(run: () => Promise<unknown>): Promise<unknown> {
  try {
    await run();
  } catch (e) {
    return e;
  }
  throw new Error("expected the call to fail");
}

describe("controlPlaneFetch", () => {
  it("positive control: a read that succeeds returns the body", async () => {
    server.use(http.get(URL, () => HttpResponse.json([{ id: "p1" }])));
    expect(await controlPlaneFetch(URL)).toEqual([{ id: "p1" }]);
  });

  it.each([501, 404])("a read answering %i is 'not available on this build'", async (status) => {
    server.use(http.get(URL, () => HttpResponse.json({ detail: "x" }, { status })));
    const e = await failure(() => controlPlaneFetch(URL));
    expect(isControlPlaneUnavailable(e)).toBe(true);
    expect(controlPlaneErrorText(e)).toBe("Not available on this build.");
    expect(controlPlanePollInterval(e, 5000)).toBe(false);
  });

  it.each(["POST", "PUT", "PATCH", "DELETE", "post"])(
    "a %s answering 404 carries the server's reason",
    async (method) => {
      server.use(
        http.all(`${URL}/ghost`, () =>
          HttpResponse.json({ detail: "plugin 'ghost' not found" }, { status: 404 })),
      );
      const e = await failure(() => controlPlaneFetch(`${URL}/ghost`, { method }));
      expect(isControlPlaneUnavailable(e)).toBe(false);
      expect(controlPlaneErrorText(e)).toBe("plugin 'ghost' not found");
      expect(controlPlanePollInterval(e, 5000)).toBe(5000);
    },
  );

  it("a mutation answering 404 without a body still names the status", async () => {
    server.use(http.delete(`${URL}/ghost`, () => new HttpResponse(null, { status: 404 })));
    const e = await failure(() => controlPlaneFetch(`${URL}/ghost`, { method: "DELETE" }));
    expect(isControlPlaneUnavailable(e)).toBe(false);
    expect(controlPlaneErrorText(e)).toBe("HTTP 404");
  });

  it("a mutation answering 501 is still 'not available on this build'", async () => {
    server.use(http.post(`${URL}/p1/enable`, () =>
      HttpResponse.json({ detail: "not_implemented" }, { status: 501 })));
    const e = await failure(() => controlPlaneFetch(`${URL}/p1/enable`, { method: "POST" }));
    expect(isControlPlaneUnavailable(e)).toBe(true);
  });
});
