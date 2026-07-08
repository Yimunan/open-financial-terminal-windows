import { useEffect, useState } from "react";
import type { IDockviewHeaderActionsProps } from "dockview";
import { desktopWindowBridge, soloPayload, syncPopoutChrome } from "../lib/popout";

/* Inline SVG glyphs for the header controls — thin-stroke, currentColor, 16-grid, sized h-3.5 w-3.5.
 * Matches the app's icon convention (App.tsx GearIcon, ResizeGrip, OrderBook toggles); `aria-hidden`
 * because the enclosing button carries the accessible name. */
const ICON = "h-3.5 w-3.5";
const svgProps = {
  viewBox: "0 0 16 16",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.5,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  className: ICON,
  "aria-hidden": true,
  focusable: false as const,
};

/* Maximize/Restore are drawn in an inset ~3.5–12.5 box (not corner-to-corner) so their optical size
 * matches the more compact PopoutIcon — corner-reaching marks otherwise read larger at the same grid. */

/** Diagonal corner-arrows pointing OUTWARD — "expand this panel to fill". */
function MaximizeIcon() {
  return (
    <svg {...svgProps}>
      <path d="M9.5 3.5H12.5V6.5M12.5 3.5L9 7" />
      <path d="M6.5 12.5H3.5V9.5M3.5 12.5L7 9" />
    </svg>
  );
}

/** Diagonal corner-arrows pointing INWARD — "collapse back to normal size". */
function RestoreIcon() {
  return (
    <svg {...svgProps}>
      <path d="M12.5 3.5L9 7M9 7V3.5M9 7H12.5" />
      <path d="M3.5 12.5L7 9M7 9V12.5M7 9H3.5" />
    </svg>
  );
}

/** A window with a diagonal arrow leaving its top-right corner — "open in a new window". */
function PopoutIcon() {
  return (
    <svg {...svgProps}>
      <path d="M7 3H3.5V12.5H12.5V9" />
      <path d="M9 3H13V7" />
      <path d="M13 3L7.5 8.5" />
    </svg>
  );
}

/** Tab-bar controls on the right of every group header: maximize and pop-out-to-window.
 * Pops the group's ACTIVE panel into its own OS window via Dockview's popout group,
 * mirroring the theme chrome so it doesn't render colorless.
 */
export function RightHeaderActions(props: IDockviewHeaderActionsProps) {
  const { containerApi, activePanel } = props;
  const [maximized, setMaximized] = useState(() => containerApi.hasMaximizedGroup());

  useEffect(() => {
    const d = containerApi.onDidMaximizedGroupChange(() =>
      setMaximized(containerApi.hasMaximizedGroup()),
    );
    return () => d.dispose();
  }, [containerApi]);

  const popOut = () => {
    if (!activePanel) return;
    // Desktop shell (pywebview/WKWebView) blocks script window.open(), so Dockview's popout no-ops
    // there — hand off to the native bridge, which opens a real OS window rendering just this widget.
    const bridge = desktopWindowBridge();
    if (bridge?.open_panel_window) {
      void bridge.open_panel_window(soloPayload(activePanel));
      return;
    }
    // Browser: Dockview's real popout window works.
    let unsync: (() => void) | undefined;
    void containerApi.addPopoutGroup(activePanel, {
      onDidOpen: ({ window }) => {
        unsync = syncPopoutChrome(window);
      },
      onWillClose: () => {
        unsync?.();
        unsync = undefined;
      },
    });
  };

  const toggleMax = () => {
    if (!activePanel) return;
    if (containerApi.hasMaximizedGroup()) containerApi.exitMaximizedGroup();
    else containerApi.maximizeGroup(activePanel);
  };

  // Shared icon-button chrome: house focus ring + eased hover, muted → text on hover, subtle fill
  // (mirrors Dockview's own --dv-icon-hover-background-color), dimmed + inert when there's no panel.
  const btn =
    "focus-ring flex h-6 w-6 items-center justify-center rounded text-term-muted transition-colors hover:bg-term-border/60 hover:text-term-text disabled:pointer-events-none disabled:opacity-40";
  const maxLabel = maximized ? "Restore" : "Maximize";

  return (
    <div className="flex h-full items-center gap-0.5 px-1.5">
      <button
        onClick={toggleMax}
        disabled={!activePanel}
        title={maxLabel}
        aria-label={maxLabel}
        aria-pressed={maximized}
        className={`${btn} ${maximized ? "text-term-accent hover:text-term-accent" : ""}`}
      >
        {maximized ? <RestoreIcon /> : <MaximizeIcon />}
      </button>
      <button
        onClick={popOut}
        disabled={!activePanel}
        title="Open in new window"
        aria-label="Open in new window"
        className={btn}
      >
        <PopoutIcon />
      </button>
    </div>
  );
}
