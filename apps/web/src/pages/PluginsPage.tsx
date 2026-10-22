import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Cpu, HeartPulse, Power } from "lucide-react";
import { useState } from "react";

import { api } from "@/api/client";
import { keys } from "@/api/hooks";
import type { PluginInfo } from "@/api/types";
import { SchemaForm } from "@/components/SchemaForm";
import { Button } from "@/components/ui/button";
import { Badge, Card, CardContent, CardHeader, CardTitle } from "@/components/ui/primitives";
import { type MessageKey, t } from "@/i18n/ru";

const STATUS_TONE = { ok: "ok", degraded: "warning", error: "error", disabled: "default" } as const;

export function PluginsPage() {
  const plugins = useQuery({ queryKey: keys.plugins, queryFn: () => api.get<PluginInfo[]>("/api/plugins") });
  return (
    <div className="mx-auto max-w-5xl space-y-3 p-6">
      <h1 className="text-xl font-semibold">{t("plugins.title")}</h1>
      {plugins.isLoading && <p className="text-muted-foreground">{t("common.loading")}</p>}
      {plugins.data?.map((plugin) => <PluginCard key={plugin.id} plugin={plugin} />)}
    </div>
  );
}

function PluginCard({ plugin }: { plugin: PluginInfo }) {
  const client = useQueryClient();
  const [preview, setPreview] = useState<Record<string, Record<string, unknown>>>({});
  const action = useMutation({
    mutationFn: (name: "enable" | "disable" | "health") => api.post<PluginInfo>(`/api/plugins/${plugin.id}/${name}`),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.plugins }),
    onError: (error) => window.alert(error instanceof Error ? error.message : String(error)),
  });
  const disabled = plugin.status === "disabled";
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <CardTitle>{plugin.name}</CardTitle>
          <span className="text-xs text-muted-foreground">
            {plugin.id} · {plugin.version} · {plugin.runtime}
          </span>
          {plugin.requires_gpu && (
            <Badge tone="primary">
              <Cpu className="mr-1 h-3 w-3" />
              {t("plugins.gpu")}
            </Badge>
          )}
          <Badge tone={STATUS_TONE[plugin.status]}>{t(`plugins.status.${plugin.status}` as MessageKey)}</Badge>
        </div>
        <div className="flex gap-1">
          <Button size="sm" variant="outline" disabled={disabled || action.isPending} onClick={() => action.mutate("health")}>
            <HeartPulse className="h-3.5 w-3.5" /> {t("plugins.health")}
          </Button>
          <Button size="sm" variant="outline" disabled={action.isPending} onClick={() => action.mutate(disabled ? "enable" : "disable")}>
            <Power className="h-3.5 w-3.5" /> {disabled ? t("plugins.enable") : t("plugins.disable")}
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {plugin.description && <p className="text-xs text-muted-foreground">{plugin.description}</p>}
        {plugin.error && <p className="text-xs text-destructive">{plugin.error}</p>}
        {plugin.health?.message && <p className="text-xs text-muted-foreground">{plugin.health.message}</p>}
        {Object.keys(plugin.settings).length > 0 && (
          <div className="text-xs">
            <div className="text-muted-foreground">{t("plugins.settings")}</div>
            {Object.entries(plugin.settings).map(([key, description]) => (
              <div key={key}>
                <code className="text-primary">{key}</code> — {description}
              </div>
            ))}
          </div>
        )}
        <div className="space-y-2">
          {plugin.components.map((component) => (
            <details key={component.id} className="rounded border p-2">
              <summary className="cursor-pointer text-sm">
                <code>{component.id}</code> <span className="text-xs text-muted-foreground">({component.role}) {component.description}</span>
              </summary>
              <div className="pt-2">
                <SchemaForm
                  schema={component.params_schema}
                  value={preview[component.id] ?? {}}
                  onChange={(v) => setPreview({ ...preview, [component.id]: v })}
                />
              </div>
            </details>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
