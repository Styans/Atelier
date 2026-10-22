// Form fields from the JSON Schemas pydantic generates for plugin parameters (ADR-029).

import type { JsonSchema } from "@/api/types";

export type FieldKind = "number" | "integer" | "boolean" | "string" | "enum" | "vector" | "json";

export interface FieldSpec {
  name: string;
  title: string;
  description?: string;
  kind: FieldKind;
  nullable: boolean;
  defaultValue: unknown;
  hasDefault: boolean;
  enumValues?: (string | number)[];
  min?: number;
  max?: number;
  length?: number;
}

type Node = Record<string, unknown>;

/** Replace local ``$ref``s with their definitions so fields can be read directly. */
export function inlineRefs(schema: JsonSchema): JsonSchema {
  const defs = (schema.$defs ?? {}) as Record<string, Node>;
  const resolve = (node: unknown): unknown => {
    if (Array.isArray(node)) return node.map(resolve);
    if (node && typeof node === "object") {
      const obj = node as Node;
      if (typeof obj.$ref === "string" && obj.$ref.startsWith("#/$defs/")) {
        const target = defs[obj.$ref.slice("#/$defs/".length)] ?? {};
        const { $ref: _ref, ...rest } = obj;
        void _ref;
        return resolve({ ...target, ...rest });
      }
      return Object.fromEntries(Object.entries(obj).filter(([k]) => k !== "$defs").map(([k, v]) => [k, resolve(v)]));
    }
    return node;
  };
  return resolve(schema) as JsonSchema;
}

function kindOf(node: Node): Pick<FieldSpec, "kind" | "enumValues" | "length"> {
  if (Array.isArray(node.enum)) return { kind: "enum", enumValues: node.enum as (string | number)[] };
  if (node.const !== undefined) return { kind: "enum", enumValues: [node.const as string] };
  if (node.type === "array" && Array.isArray(node.prefixItems)) {
    const items = node.prefixItems as Node[];
    if (items.every((i) => i.type === "number" || i.type === "integer")) {
      return { kind: "vector", length: items.length };
    }
  }
  if (node.type === "number") return { kind: "number" };
  if (node.type === "integer") return { kind: "integer" };
  if (node.type === "boolean") return { kind: "boolean" };
  if (node.type === "string") return { kind: "string" };
  return { kind: "json" };
}

export function fieldsOf(schema: JsonSchema): FieldSpec[] {
  const root = inlineRefs(schema) as Node;
  const properties = (root.properties ?? {}) as Record<string, Node>;
  return Object.entries(properties).map(([name, node]) => {
    let nullable = false;
    let effective = node;
    if (Array.isArray(node.anyOf)) {
      const branches = (node.anyOf as Node[]).filter((b) => b.type !== "null");
      nullable = branches.length !== (node.anyOf as Node[]).length;
      effective = branches.length === 1 ? { ...branches[0], ...node, anyOf: undefined } : { type: "json" };
    }
    const min = (effective.minimum ?? effective.exclusiveMinimum) as number | undefined;
    const max = (effective.maximum ?? effective.exclusiveMaximum) as number | undefined;
    return {
      name,
      title: (node.title as string | undefined) ?? name,
      description: node.description as string | undefined,
      nullable,
      defaultValue: node.default,
      hasDefault: "default" in node,
      min,
      max,
      ...kindOf(effective),
    };
  });
}

/** Text typed into a field -> value for the params object (undefined = use the default). */
export function parseField(spec: FieldSpec, text: string): unknown {
  const trimmed = text.trim();
  if (trimmed === "") return spec.nullable ? null : undefined;
  switch (spec.kind) {
    case "number":
    case "integer": {
      const value = Number(trimmed.replace(",", "."));
      if (Number.isNaN(value)) throw new Error(`${spec.title}: not a number`);
      if (spec.kind === "integer" && !Number.isInteger(value)) throw new Error(`${spec.title}: whole number`);
      return value;
    }
    case "vector": {
      const parts = trimmed.split(/[,;\s]+/).filter(Boolean).map((p) => Number(p));
      if (parts.length !== spec.length || parts.some(Number.isNaN)) {
        throw new Error(`${spec.title}: ${spec.length} numbers`);
      }
      return parts;
    }
    case "json":
      try {
        return JSON.parse(trimmed);
      } catch {
        return trimmed;
      }
    case "enum": {
      const match = spec.enumValues?.find((v) => String(v) === trimmed);
      return match ?? trimmed;
    }
    default:
      return trimmed;
  }
}

export function formatField(spec: FieldSpec, value: unknown): string {
  if (value === undefined || value === null) return "";
  if (spec.kind === "vector" && Array.isArray(value)) return value.join(", ");
  if (spec.kind === "json" && typeof value !== "string") return JSON.stringify(value);
  return String(value);
}
