import { DockviewReact, type DockviewReadyEvent } from "dockview";
import { dockviewComponents } from "./widgetRegistry";
import type { WidgetParams } from "./widgetRegistry";

/** Full-window host for a single widget — the target of the desktop "open in new window" (⧉) control.
 *
 * The desktop bridge (backend run_desktop.py) spawns a fresh native window at "/?solo=<type>&…"; the
 * app entry (main.tsx) detects that and mounts this instead of the full terminal. We render a real
 * one-panel Dockview (same component map) rather than the raw widget, so every widget gets the exact
 * panel/container API it expects — updateParameters, setSize, setActive, nested sub-dockviews, the
 * resize grip — with no stubbing. It's a separate browser context, so its Zustand stores, links and
 * WebSockets are isolated: nothing here can touch the main window's saved layout. Theme chrome is
 * applied automatically when state/settings rehydrates on import (see settings onRehydrateStorage).
 * The single tab bar + resize grip are hidden via the `.oft-solo` CSS class for a clean full window.
 */
export default function SoloWorkspace({
  type,
  title,
  params,
}: {
  type: string;
  title?: string;
  params: WidgetParams;
}) {
  if (!type || !(type in dockviewComponents)) {
    return (
      <div className="grid h-full w-full place-items-center bg-term-bg text-sm text-term-muted">
        Unknown widget: {type || "(none)"}
      </div>
    );
  }

  const onReady = (event: DockviewReadyEvent) => {
    event.api.addPanel({ id: "solo", component: type, title, params });
  };

  return (
    <DockviewReact
      components={dockviewComponents}
      onReady={onReady}
      className="oft-dockview oft-solo h-full w-full"
    />
  );
}
