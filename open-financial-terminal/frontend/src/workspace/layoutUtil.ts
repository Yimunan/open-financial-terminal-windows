/** Shared Dockview layout helpers used by the host and the workspace store. */

import type { DockviewApi } from "dockview";
import { widgetTitle, type Lang } from "../lib/i18n";
import { LEGACY_ALIASES } from "./widgetRegistry";

/** Re-derive every panel's tab title from the CURRENT module name so renamed/merged modules
 * propagate to existing workspaces + templates automatically. Panels carrying an explicit
 * `params.label` (topic feeds, custom template titles like "Chart · 1m") keep it; everything else
 * tracks the registry title via `widgetTitle`. Legacy merged ids (research→Profile, rates→FICC)
 * resolve to their target module's name. Call after any `fromJSON`/layout build. */
export function retitlePanels(api: DockviewApi, lang: Lang): void {
  for (const p of api.panels) {
    const label = (p.params as { label?: string } | undefined)?.label;
    if (label) {
      p.setTitle(label);
      continue;
    }
    const rawType = p.id.split("-")[0];
    const type = LEGACY_ALIASES[rawType] ?? rawType;
    p.setTitle(widgetTitle(type, lang));
  }
}

/** Starter arrangement for a fresh bento space: watchlist | chart (+news below) | assistant. */
export function seedDefaultLayout(api: DockviewApi): void {
  const watchlist = api.addPanel({
    id: `watchlist-${Date.now()}`,
    component: "watchlist",
    title: "Watchlist",
    params: { channel: "red" },
  });
  const chart = api.addPanel({
    id: `chart-${Date.now()}`,
    component: "chart",
    title: "Chart",
    params: { channel: "red" },
    position: { referencePanel: watchlist.id, direction: "right" },
  });
  api.addPanel({
    id: `news-${Date.now()}`,
    component: "news",
    title: "News",
    params: { channel: "red" },
    position: { referencePanel: chart.id, direction: "below" },
  });
  api.addPanel({
    id: `assistant-${Date.now()}`,
    component: "assistant",
    title: "Assistant",
    params: { channel: "red" },
    position: { referencePanel: chart.id, direction: "right" },
  });
  watchlist.api.setSize({ width: 340 });
}

/** Collapse any restored popout/floating groups back into the docked grid (pop-out is a
 * live action, not persisted state — auto-reopening a window on load would surprise the
 * user and skip our theme-chrome mirroring). */
export function redockFloating(api: DockviewApi): void {
  const grid = api.groups.find((g) => g.api.location.type === "grid");
  if (!grid) return;
  for (const group of [...api.groups]) {
    if (group.api.location.type === "grid") continue;
    for (const panel of [...group.panels]) panel.api.moveTo({ group: grid });
  }
}
