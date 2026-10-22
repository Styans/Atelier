import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "@/api/client";
import { useEdit } from "@/api/hooks";
import type { ComponentInfo, StageConfig } from "@/api/types";
import { SchemaForm } from "@/components/SchemaForm";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, Field, Select } from "@/components/ui/primitives";
import { type MessageKey, t } from "@/i18n/ru";

import { useShowState } from "./context";

const SINGLE = ["point_distributor", "transition_planner", "image_generator", "mesh_generator"] as const;
type SingleRole = (typeof SINGLE)[number];

function useComponents(role: string) {
  return useQuery({ queryKey: ["components", role], queryFn: () => api.get<ComponentInfo[]>(`/api/components/${role}`) });
}

/** Resolve a configured reference (component id or plugin id) to a component of the list. */
function matching(components: ComponentInfo[] | undefined, ref: string | undefined): ComponentInfo | undefined {
  if (!components || !ref) return undefined;
  return components.find((c) => c.id === ref) ?? components.find((c) => c.plugin_id === ref);
}

export function PipelinePanel() {
  return (
    <div className="space-y-3">
      <h2 className="text-sm font-semibold">{t("pipeline.title")}</h2>
      {SINGLE.map((role) => (
        <StageEditor key={role} role={role} />
      ))}
      <ListEditor which="validators" role="validator_rule" />
      <ListEditor which="exporters" role="exporter" />
    </div>
  );
}

function StageEditor({ role }: { role: SingleRole }) {
  const { id, view } = useShowState();
  const current = view.show.pipeline[role] as StageConfig | null;
  const components = useComponents(role);
  const configured = matching(components.data, current?.component);
  const [componentId, setComponentId] = useState<string | null>(null);
  const [params, setParams] = useState<Record<string, unknown> | null>(null);
  const chosen = componentId ?? configured?.id ?? "";
  const component = components.data?.find((c) => c.id === chosen);
  const value = params ?? (chosen === configured?.id ? current?.params ?? {} : {});
  const dirty = componentId !== null || params !== null;
  const save = useEdit(id, (body: { component: string; params: Record<string, unknown> }, base) =>
    api.put(`/api/shows/${id}/pipeline/${role}`, { base_revision: base, ...body }),
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t(`pipeline.${role}` as MessageKey)}</CardTitle>
        {dirty && component && (
          <Button
            size="sm"
            onClick={() =>
              save.mutate(
                // Keep the stored reference (it may be a plugin id) when only the parameters
                // change: rewriting it would mark results stale for no reason.
                { component: component.id === configured?.id && current ? current.component : component.id, params: value },
                {
                  onSuccess: () => {
                    setComponentId(null);
                    setParams(null);
                  },
                },
              )
            }
          >
            {t("common.save")}
          </Button>
        )}
      </CardHeader>
      <CardContent>
        <Field label={role}>
          <Select
            value={chosen}
            onChange={(e) => {
              setComponentId(e.target.value);
              setParams({});
            }}
          >
            {!configured && <option value="">{current?.component ?? t("pipeline.notSet")}</option>}
            {components.data?.map((c) => (
              <option key={c.id} value={c.id}>
                {c.id} ({c.version})
              </option>
            ))}
          </Select>
        </Field>
        {component && (
          <>
            <p className="text-[11px] text-muted-foreground">{component.description}</p>
            <SchemaForm key={component.id} schema={component.params_schema} value={value} onChange={setParams} />
          </>
        )}
      </CardContent>
    </Card>
  );
}

function ListEditor({ which, role }: { which: "validators" | "exporters"; role: string }) {
  const { id, view } = useShowState();
  const components = useComponents(role);
  const stored = view.show.pipeline[which].map((c) => c.component);
  const configured = stored.map((ref) => matching(components.data, ref)?.id ?? ref);
  const save = useEdit(id, (list: string[], base) =>
    api.put(`/api/shows/${id}/pipeline/${which}/list`, { base_revision: base, components: list }),
  );
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t(`pipeline.${which}` as MessageKey)}</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-1">
        {components.data?.map((c) => (
          <label key={c.id} className="flex items-center gap-2 text-sm" title={c.description}>
            <input
              type="checkbox"
              checked={configured.includes(c.id)}
              onChange={(e) =>
                save.mutate(e.target.checked ? [...stored, c.id] : stored.filter((_, i) => configured[i] !== c.id))
              }
            />
            {c.id}
          </label>
        ))}
      </CardContent>
    </Card>
  );
}
