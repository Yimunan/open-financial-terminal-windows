/** Corner resize grip: a draggable handle in each panel's bottom-right corner that resizes the
 * widget in BOTH dimensions at once (drag out = bigger, in = smaller) — the "grab the corner to
 * zoom the panel" gesture Dockview lacks (it ships only thin single-axis edge dividers).
 *
 * Applied once via `withResizeGrip` where the Dockview component map is built (widgetRegistry.tsx),
 * so every panel gets it with no per-widget code. Resizing calls `api.setSize`, which trips the
 * hub's `onDidLayoutChange` → autosave, so sizes persist for free. Visibility + color live in CSS
 * (`.oft-panel-wrap` / `.oft-resize-grip` in index.css) to avoid nested-Tailwind-group conflicts
 * with widgets that use their own `group`/`group-hover`.
 */

import { useEffect, useState, type FunctionComponent, type PointerEvent as ReactPointerEvent } from "react";
import type { WidgetProps } from "./widgetRegistry";

// Shared floor for both the grip and the edge sashes (via setConstraints) so panels can't be
// crushed to nothing (Dockview's default is a cramped 100×100).
const MIN_W = 160;
const MIN_H = 120;

type PanelApi = WidgetProps["api"];
type ContainerApi = WidgetProps["containerApi"];

function ResizeGrip({ api, containerApi }: { api: PanelApi; containerApi: ContainerApi }) {
  // The grip only makes sense for a normal docked, non-maximized panel. Popout/floating groups
  // have native/overlay resize; a maximized group fills the space already.
  const visibleNow = () => api.group.api.location.type === "grid" && !api.isMaximized();
  const [show, setShow] = useState(visibleNow);

  useEffect(() => {
    const update = () => setShow(api.group.api.location.type === "grid" && !api.isMaximized());
    const d1 = containerApi.onDidMaximizedGroupChange(update);
    const d2 = containerApi.onDidLayoutChange(update); // catches move / popout / float / dock
    return () => {
      d1.dispose();
      d2.dispose();
    };
  }, [api, containerApi]);

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation(); // don't let Dockview start a tab-drag / focus churn

    const el = e.currentTarget;
    el.setPointerCapture(e.pointerId); // keep receiving moves over canvases/iframes/other panels

    const startX = e.clientX;
    const startY = e.clientY;
    // Baseline from the GROUP box (header + content) — matches what setSize sets, so there's no
    // first-frame jump (api.width/height are content-only and exclude the 30px tab bar).
    const rect = api.group.element.getBoundingClientRect();
    const startW = rect.width;
    const startH = rect.height;

    const doc = el.ownerDocument; // popout-safe
    const prevCursor = doc.body.style.cursor;
    const prevSelect = doc.body.style.userSelect;
    doc.body.style.cursor = "nwse-resize";
    doc.body.style.userSelect = "none";

    let raf = 0;
    let pending: { width: number; height: number } | null = null;
    const flush = () => {
      raf = 0;
      if (pending) {
        api.setSize(pending);
        pending = null;
      }
    };

    const onMove = (ev: PointerEvent) => {
      pending = {
        width: Math.max(MIN_W, startW + (ev.clientX - startX)),
        height: Math.max(MIN_H, startH + (ev.clientY - startY)),
      };
      if (!raf) raf = requestAnimationFrame(flush);
    };
    const end = () => {
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerup", end);
      el.removeEventListener("pointercancel", end);
      if (raf) cancelAnimationFrame(raf);
      if (pending) api.setSize(pending); // final flush → one last onDidLayoutChange → autosave
      doc.body.style.cursor = prevCursor;
      doc.body.style.userSelect = prevSelect;
    };

    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
  };

  if (!show) return null;
  return (
    <div
      role="separator"
      aria-label="Resize panel"
      aria-orientation="vertical"
      onPointerDown={onPointerDown}
      style={{ touchAction: "none" }}
      className="oft-resize-grip absolute bottom-0 right-0 z-20 h-4 w-4 cursor-nwse-resize"
      title="Drag to resize"
    >
      <svg viewBox="0 0 16 16" className="h-full w-full" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
        <line x1="15" y1="6" x2="6" y2="15" />
        <line x1="15" y1="11" x2="11" y2="15" />
      </svg>
    </div>
  );
}

/** Wrap a panel component so it gains the corner grip (and a min-size floor). Applied at the
 * `dockviewComponents` map — one place, every panel. Wrapped OUTSIDE the error boundary so the
 * grip keeps working even if the widget crashes into its fallback. */
export function withResizeGrip(Inner: FunctionComponent<WidgetProps>): FunctionComponent<WidgetProps> {
  return function GrippedWidget(props: WidgetProps) {
    useEffect(() => {
      // Floor both resize paths (grip + edge sashes). Re-applied every mount, so it needn't persist.
      props.api.setConstraints({ minimumWidth: MIN_W, minimumHeight: MIN_H });
    }, [props.api]);
    return (
      <div className="oft-panel-wrap relative h-full w-full">
        <Inner {...props} />
        <ResizeGrip api={props.api} containerApi={props.containerApi} />
      </div>
    );
  };
}
