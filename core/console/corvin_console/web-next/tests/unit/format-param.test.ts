/**
 * Compute run parameters in the experiment comparison table (src/lib/format-param.ts):
 * a search-space object used to render as "[object Object]" and a 25-point series ran off the table.
 */
import { describe, it, expect } from "vitest";
import { formatParam } from "@/lib/format-param";

describe("formatParam", () => {
  it("shows a search-space object as its type and range", () => {
    expect(formatParam({ type: "float_uniform", low: 0.05, high: 1 })).toBe("float_uniform 0.05–1");
    expect(formatParam({ type: "int_uniform", low: 2, high: 8 })).toBe("int_uniform 2–8");
  });

  it("never prints [object Object]", () => {
    expect(formatParam({ foo: 1 })).not.toContain("[object Object]");
    expect(formatParam({ foo: 1 })).toBe('{"foo":1}');
  });

  it("summarises a long series and keeps a short list readable", () => {
    expect(formatParam([Array.from({ length: 25 }, (_, i) => i)])).toBe("[25 values]");
    expect(formatParam([0.2, 0.4, 0.6, 0.8, 1])).toBe("0.2, 0.4, 0.6, 0.8, 1");
  });

  it("handles scalars and missing values", () => {
    expect(formatParam(6)).toBe("6");
    expect(formatParam(null)).toBe("—");
    expect(formatParam(undefined)).toBe("—");
  });
});
