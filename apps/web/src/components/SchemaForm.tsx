import { useMemo, useState } from "react";

import type { JsonSchema } from "@/api/types";
import { Field, Input, Select } from "@/components/ui/primitives";
import { t } from "@/i18n/ru";
import { type FieldSpec, fieldsOf, formatField, parseField } from "@/lib/schema";

interface Props {
  schema: JsonSchema;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
}

/** A form generated from a plugin's parameter schema: plugins need no UI code. */
export function SchemaForm({ schema, value, onChange }: Props) {
  const fields = useMemo(() => fieldsOf(schema), [schema]);
  if (fields.length === 0) return <p className="text-xs text-muted-foreground">—</p>;
  return (
    <div className="grid grid-cols-2 gap-2">
      {fields.map((spec) => (
        <SchemaField
          key={spec.name}
          spec={spec}
          value={value[spec.name]}
          onChange={(v) => {
            const next = { ...value };
            if (v === undefined) delete next[spec.name];
            else next[spec.name] = v;
            onChange(next);
          }}
        />
      ))}
    </div>
  );
}

function SchemaField({ spec, value, onChange }: { spec: FieldSpec; value: unknown; onChange: (v: unknown) => void }) {
  const [text, setText] = useState(formatField(spec, value));
  const [error, setError] = useState<string | null>(null);
  const placeholder = spec.hasDefault ? formatField(spec, spec.defaultValue) || "—" : "";
  const hint = [spec.description, spec.min !== undefined || spec.max !== undefined ? `${spec.min ?? "…"}…${spec.max ?? "…"}` : null]
    .filter(Boolean)
    .join(" · ");

  if (spec.kind === "boolean") {
    const checked = value === undefined ? Boolean(spec.defaultValue) : Boolean(value);
    return (
      <Field label={spec.title} hint={spec.description}>
        <input type="checkbox" className="h-4 w-4 accent-primary" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      </Field>
    );
  }
  if (spec.kind === "enum") {
    return (
      <Field label={spec.title} hint={spec.description}>
        <Select value={value === undefined ? "" : String(value)} onChange={(e) => onChange(e.target.value === "" ? undefined : parseField(spec, e.target.value))}>
          <option value="">{t("schema.default", { value: placeholder })}</option>
          {spec.enumValues?.map((v) => (
            <option key={String(v)} value={String(v)}>
              {String(v)}
            </option>
          ))}
        </Select>
      </Field>
    );
  }
  return (
    <Field label={spec.title} hint={error ?? (hint || undefined)}>
      <Input
        value={text}
        placeholder={placeholder}
        className={error ? "ring-2 ring-destructive" : undefined}
        onChange={(e) => {
          setText(e.target.value);
          try {
            onChange(parseField(spec, e.target.value));
            setError(null);
          } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
          }
        }}
      />
    </Field>
  );
}
