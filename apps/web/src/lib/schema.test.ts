import { describe, expect, it } from "vitest";

import { fieldsOf, formatField, parseField } from "./schema";

// Shapes as pydantic emits them for builtin.rotate and pointille.blue_noise.
const rotate = {
  properties: {
    axis: {
      anyOf: [{ enum: ["x", "y", "z"], type: "string" }, { prefixItems: [{ type: "number" }, { type: "number" }, { type: "number" }], type: "array" }],
      default: "z",
      title: "Axis",
    },
    angle_deg: { default: 90, title: "Angle Deg", type: "number" },
    pivot: { default: [0, 0, 0], prefixItems: [{ type: "number" }, { type: "number" }, { type: "number" }], type: "array", title: "Pivot" },
  },
  title: "RotateParams",
  type: "object",
};

const pointille = {
  $defs: { Mode: { enum: ["area", "point", "none"], title: "Mode", type: "string" } },
  properties: {
    oversample: { default: 8, maximum: 64, minimum: 2, title: "Oversample", type: "number" },
    relax_iterations: { default: 20, maximum: 200, minimum: 0, title: "Relax", type: "integer" },
    color_mode: { $ref: "#/$defs/Mode", default: "area" },
    facade: { default: false, type: "boolean", title: "Facade" },
    steps: { anyOf: [{ minimum: 1, type: "integer" }, { type: "null" }], default: null, title: "Steps" },
  },
  type: "object",
};

describe("fieldsOf", () => {
  it("reads numbers, vectors and unions", () => {
    const fields = Object.fromEntries(fieldsOf(rotate).map((f) => [f.name, f]));
    expect(fields.angle_deg.kind).toBe("number");
    expect(fields.pivot).toMatchObject({ kind: "vector", length: 3 });
    expect(fields.axis.kind).toBe("json");
    expect(fields.axis.defaultValue).toBe("z");
  });

  it("follows $refs, bounds and nullability", () => {
    const fields = Object.fromEntries(fieldsOf(pointille).map((f) => [f.name, f]));
    expect(fields.color_mode).toMatchObject({ kind: "enum", enumValues: ["area", "point", "none"] });
    expect(fields.oversample).toMatchObject({ min: 2, max: 64 });
    expect(fields.relax_iterations.kind).toBe("integer");
    expect(fields.steps).toMatchObject({ kind: "integer", nullable: true, min: 1 });
    expect(fields.facade.kind).toBe("boolean");
  });
});

describe("parseField", () => {
  const fields = Object.fromEntries([...fieldsOf(rotate), ...fieldsOf(pointille)].map((f) => [f.name, f]));

  it("parses numbers with a decimal comma", () => {
    expect(parseField(fields.angle_deg, "45,5")).toBe(45.5);
  });

  it("parses vectors and checks their length", () => {
    expect(parseField(fields.pivot, "1, 2 3")).toEqual([1, 2, 3]);
    expect(() => parseField(fields.pivot, "1 2")).toThrow();
  });

  it("empty means default, or null when nullable", () => {
    expect(parseField(fields.angle_deg, "")).toBeUndefined();
    expect(parseField(fields.steps, "")).toBeNull();
  });

  it("unions accept plain text or JSON", () => {
    expect(parseField(fields.axis, "x")).toBe("x");
    expect(parseField(fields.axis, "[0, 0, 1]")).toEqual([0, 0, 1]);
    expect(formatField(fields.axis, [0, 0, 1])).toBe("[0,0,1]");
  });

  it("rejects fractions for integers", () => {
    expect(() => parseField(fields.relax_iterations, "2.5")).toThrow();
  });
});
