/** Mirror the terminal's theme chrome into a Dockview popout window.
 *
 * Dockview copies the parent document's CSS *rules* into the popout window, but not the
 * `<html>` attributes our theme tokens hang off (`data-theme`, `data-scheme`, `dir`,
 * `lang`) nor the inline `--term-accent` override. Without them the copied
 * `:root[data-theme="dark"] { --term-bg: … }` rules never match and the window renders
 * colorless. We copy those attributes on open and keep them in sync while the window
 * lives, so toggling theme / scheme / accent / language updates the popout too.
 */

import type { IDockviewPanel } from "dockview";
import { useSettings } from "../state/settings";

const MIRRORED_ATTRS = ["data-theme", "data-scheme", "dir", "lang"] as const;

export function syncPopoutChrome(win: Window): () => void {
  const src = document.documentElement;

  const apply = () => {
    const dst = win.document?.documentElement;
    if (!dst) return;
    for (const attr of MIRRORED_ATTRS) {
      const v = src.getAttribute(attr);
      if (v != null) dst.setAttribute(attr, v);
      else dst.removeAttribute(attr);
    }
    const accent = src.style.getPropertyValue("--term-accent");
    if (accent) dst.style.setProperty("--term-accent", accent);
    else dst.style.removeProperty("--term-accent");
  };

  apply();
  // zustand vanilla subscribe — fires on any settings change (theme/scheme/accent/lang).
  const unsub = useSettings.subscribe(apply);
  return unsub;
}

/* ─────────────────────────────────────────────────────────────────────────────
 * Desktop pop-out (pywebview)
 *
 * WKWebView / EdgeChromium block *script-initiated* window.open(), so Dockview's addPopoutGroup
 * silently no-ops inside the desktop shell (it works only in a real browser). When we're in the
 * desktop app, pywebview injects a `window.pywebview.api` bridge (see backend run_desktop.py
 * _WindowBridge) that spawns a genuine native window at a "/?solo=…" path rendering just one widget.
 * These helpers build that payload and detect the bridge; the browser path keeps using addPopoutGroup.
 * ────────────────────────────────────────────────────────────────────────── */

export interface SoloPayload {
  title: string;
  /** Same-origin relative path the solo window loads: "/?solo=<type>&p=<json>&t=<title>". */
  path: string;
  width: number;
  height: number;
}

/** Cross-module `send`/consume-once handoff params (and bulky live streams) that must NOT be carried
 * into a popped-out window: they're already consumed in the source panel and don't belong in a URL. */
const isTransientParam = (key: string): boolean => key.startsWith("incoming") || key === "workflows";

/** Build the native-window payload for a panel: its widget type + carry-over params as a solo path. */
export function soloPayload(panel: IDockviewPanel): SoloPayload {
  const type = panel.view.contentComponent;
  const params: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(panel.params ?? {})) {
    if (value == null || isTransientParam(key)) continue;
    params[key] = value;
  }

  const q = new URLSearchParams({ solo: type, p: JSON.stringify(params) });
  if (panel.title) q.set("t", panel.title);

  // Size the new window to roughly match the panel's current on-screen box.
  const rect = panel.group?.element?.getBoundingClientRect?.();
  return {
    title: panel.title || "Open Financial Terminal",
    path: `/?${q.toString()}`,
    width: Math.round(rect?.width || 900),
    height: Math.round(rect?.height || 640),
  };
}

type DesktopBridge = { open_panel_window?: (payload: SoloPayload) => Promise<unknown> };

/** The pywebview JS→Python bridge, present only inside the desktop shell (undefined in a browser). */
export function desktopWindowBridge(): DesktopBridge | undefined {
  return (window as unknown as { pywebview?: { api?: DesktopBridge } }).pywebview?.api;
}
