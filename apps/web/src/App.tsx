import { Boxes, Clapperboard } from "lucide-react";

import { useEvents } from "@/api/hooks";
import { Feedback } from "@/components/Feedback";
import { t } from "@/i18n/ru";
import { cn } from "@/lib/utils";
import { href, useRoute } from "@/lib/router";
import { PluginsPage } from "@/pages/PluginsPage";
import { ShowPage } from "@/pages/ShowPage";
import { ShowsPage } from "@/pages/ShowsPage";

export function App() {
  const route = useRoute();
  useEvents();
  return (
    <div className="flex h-full flex-col">
      <header className="flex h-11 shrink-0 items-center gap-4 border-b px-4">
        <a href={href({ page: "shows" })} className="font-semibold tracking-wide text-primary">
          {t("app.title")}
        </a>
        <nav className="flex gap-1 text-sm">
          <a
            href={href({ page: "shows" })}
            className={cn("flex items-center gap-1 rounded px-2 py-1 hover:bg-accent", route.page !== "plugins" && "text-foreground")}
          >
            <Clapperboard className="h-4 w-4" /> {t("app.shows")}
          </a>
          <a
            href={href({ page: "plugins" })}
            className={cn("flex items-center gap-1 rounded px-2 py-1 hover:bg-accent", route.page === "plugins" && "bg-accent")}
          >
            <Boxes className="h-4 w-4" /> {t("app.plugins")}
          </a>
        </nav>
      </header>
      <main className="min-h-0 flex-1">
        {route.page === "show" && <ShowPage id={route.id} />}
        {route.page === "plugins" && <PluginsPage />}
        {route.page === "shows" && <ShowsPage />}
      </main>
      <Feedback />
    </div>
  );
}
