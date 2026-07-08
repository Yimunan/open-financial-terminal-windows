import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
// Self-hosted variable fonts (bundled by Vite, no external request) — see index.css /
// tailwind.config.js for the "Inter Variable" / "JetBrains Mono Variable" family names.
import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import App from "./App";
import SoloWorkspace from "./workspace/SoloWorkspace";
import type { WidgetParams } from "./workspace/widgetRegistry";
import { markFontsReady } from "./state/settings";
import "./index.css";

// Redraw canvas charts once the webfonts are ready (they bake in the fallback otherwise).
if (typeof document !== "undefined" && document.fonts?.ready) {
  void document.fonts.ready.then(markFontsReady);
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      staleTime: 30_000,
    },
  },
});

/** A widget popped into its own OS window (desktop "open in new window" ⧉) loads "/?solo=<type>&…".
 * When that param is present we mount just that widget instead of the whole terminal. See
 * workspace/SoloWorkspace.tsx and lib/popout.ts soloPayload(). */
function soloRoute(): { type: string; title?: string; params: WidgetParams } | null {
  const q = new URLSearchParams(window.location.search);
  const type = q.get("solo");
  if (!type) return null;
  let params: WidgetParams = {};
  try {
    params = JSON.parse(q.get("p") || "{}") as WidgetParams;
  } catch {
    /* malformed payload — render the widget with no carry-over params */
  }
  return { type, title: q.get("t") || undefined, params };
}

const solo = soloRoute();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      {solo ? (
        <SoloWorkspace type={solo.type} title={solo.title} params={solo.params} />
      ) : (
        <App />
      )}
    </QueryClientProvider>
  </React.StrictMode>,
);
