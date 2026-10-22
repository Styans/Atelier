import { useEffect, useState } from "react";

// A tiny hash router: #/, #/show/<id>, #/plugins. No dependency for three routes.
export type Route = { page: "shows" } | { page: "show"; id: string } | { page: "plugins" };

export function parseRoute(hash: string): Route {
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "show" && parts[1]) return { page: "show", id: decodeURIComponent(parts[1]) };
  if (parts[0] === "plugins") return { page: "plugins" };
  return { page: "shows" };
}

export function href(route: Route): string {
  if (route.page === "show") return `#/show/${encodeURIComponent(route.id)}`;
  if (route.page === "plugins") return "#/plugins";
  return "#/";
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}
