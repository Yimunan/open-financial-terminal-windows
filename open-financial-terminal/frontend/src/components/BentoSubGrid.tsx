/** A nested Dockview *inside* a widget, so the widget's sub-windows (e.g. dashboard / chat /
 * history) become a user-customizable bento grid — drag to rearrange, drag borders to resize,
 * tab panels together — exactly like the top-level workspace. Each widget instance persists its
 * own sub-layout to localStorage.
 *
 * Live React content is fed into the named panels through context (panel components are stable
 * and look up their slice by id), so dockview only owns the *layout* while the widget keeps
 * rendering the actual UI. Context propagates into dockview's React portals.
 */

import {
  DockviewReact,
  type DockviewApi,
  type DockviewReadyEvent,
} from "dockview";
import { createContext, useContext, useMemo, useRef, useState, type ReactNode } from "react";

export interface BentoPanel {
  id: string; // stable key — also the dockview component name
  title: string;
  content: ReactNode;
}

const BentoContent = createContext<Record<string, ReactNode>>({});

/** Build a STABLE components map (one per panel id) — each reads its live content from context. */
function makeComponents(ids: string[]): Record<string, React.FC> {
  const map: Record<string, React.FC> = {};
  for (const id of ids) {
    map[id] = function BentoPanelView() {
      const content = useContext(BentoContent)[id];
      return <div className="h-full min-h-0 w-full overflow-hidden bg-term-panel">{content}</div>;
    };
  }
  return map;
}

export default function BentoSubGrid({
  storageKey,
  panels,
  seed,
}: {
  /** Unique per widget instance (use the dockview panel id) so layouts don't collide. */
  storageKey: string;
  panels: BentoPanel[];
  /** Default arrangement when there's no saved layout. Falls back to a vertical stack. */
  seed?: (api: DockviewApi, ids: string[]) => void;
}) {
  const ids = panels.map((p) => p.id);
  const idsKey = ids.join("|");
  // Components must stay referentially stable or dockview remounts the panels every render.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const components = useMemo(() => makeComponents(ids), [idsKey]);
  const contentMap = useMemo(
    () => Object.fromEntries(panels.map((p) => [p.id, p.content])),
    [panels],
  );
  const apiRef = useRef<DockviewApi | null>(null);
  const saveKey = `oft-bento:${storageKey}`;
  // Panels the user has closed (✕ on the tab). Surfaced as "reopen" chips so a closed sub-window
  // isn't lost — re-adding it restores the live content (fed from the widget via context).
  const [closed, setClosed] = useState<string[]>([]);

  const syncClosed = (api: DockviewApi) => {
    const present = new Set(api.panels.map((p) => p.id));
    setClosed(ids.filter((id) => !present.has(id)));
  };

  const reopen = (id: string) => {
    const api = apiRef.current;
    const panel = panels.find((p) => p.id === id);
    if (!api || !panel || api.panels.some((p) => p.id === id)) return;
    api.addPanel({ id: panel.id, component: panel.id, title: panel.title });
  };

  const onReady = (e: DockviewReadyEvent) => {
    apiRef.current = e.api;

    let restored = false;
    try {
      const raw = localStorage.getItem(saveKey);
      if (raw) {
        e.api.fromJSON(JSON.parse(raw));
        restored = true;
      }
    } catch {
      /* corrupt/stale layout — fall through to seed */
    }
    // If the saved layout is missing any current panel (set changed), reseed cleanly.
    if (restored && ids.some((id) => !e.api.panels.find((p) => p.id === id))) {
      e.api.clear();
      restored = false;
    }
    if (!restored) {
      if (seed) {
        seed(e.api, ids);
      } else {
        let prev: string | undefined;
        panels.forEach((p, i) => {
          e.api.addPanel({
            id: p.id,
            component: p.id,
            title: p.title,
            position: prev ? { referencePanel: prev, direction: i % 2 ? "right" : "below" } : undefined,
          });
          prev = p.id;
        });
      }
    }

    syncClosed(e.api);

    let tmr: number | undefined;
    e.api.onDidLayoutChange(() => {
      syncClosed(e.api);
      window.clearTimeout(tmr);
      tmr = window.setTimeout(() => {
        try {
          localStorage.setItem(saveKey, JSON.stringify(e.api.toJSON()));
        } catch {
          /* quota/serialization — non-fatal */
        }
      }, 500);
    });
  };

  return (
    <BentoContent.Provider value={contentMap}>
      <div className="flex h-full min-h-0 w-full flex-col">
        {closed.length > 0 && (
          <div className="flex shrink-0 flex-wrap items-center gap-1 border-b border-term-border bg-term-bg/40 px-2 py-1">
            <span className="text-[9px] uppercase tracking-wider text-term-muted">Closed</span>
            {closed.map((id) => {
              const panel = panels.find((p) => p.id === id);
              return (
                <button
                  key={id}
                  type="button"
                  onClick={() => reopen(id)}
                  title={`Reopen ${panel?.title ?? id}`}
                  className="focus-ring rounded border border-term-border px-1.5 py-0.5 text-[10px] text-term-muted transition-colors hover:border-term-accent hover:text-term-accent"
                >
                  ＋ {panel?.title ?? id}
                </button>
              );
            })}
          </div>
        )}
        <div className="min-h-0 flex-1">
          <DockviewReact components={components} onReady={onReady} className="oft-dockview h-full w-full" />
        </div>
      </div>
    </BentoContent.Provider>
  );
}
