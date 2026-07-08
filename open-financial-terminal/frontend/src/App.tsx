import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../src/api/client";
import BootstrapToast from "./components/BootstrapToast";
import CommandBar from "./components/CommandBar";
import SettingsDialog from "./components/SettingsDialog";
import WorkspaceTabs from "./components/WorkspaceTabs";
import { cx } from "./lib/format";
import { useT } from "./lib/i18n";
import { setStreamExchange } from "./lib/wsClient";
import { useRunningCount } from "./state/agentRuns";
import { useLinking } from "./state/linking";
import { useWorkspace } from "./state/workspace";
import DockviewWorkspace from "./workspace/DockviewWorkspace";

/** Top-bar badge that stays lit while backtest agents run in the background. */
function RunningIndicator() {
  const n = useRunningCount();
  if (n === 0) return null;
  return (
    <span
      className="flex items-center gap-1 rounded border border-term-accent/50 px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-term-accent"
      title={`${n} agent run${n > 1 ? "s" : ""} in progress (background)`}
    >
      <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-term-accent" />
      {n} running
    </span>
  );
}

function StatusDot({ ok, label }: { ok: boolean | undefined; label: string }) {
  const state = ok === undefined ? "unknown" : ok ? "online" : "offline";
  return (
    <span className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-term-muted" title={`${label}: ${state}`}>
      <span
        aria-hidden
        className={cx(
          "inline-block h-1.5 w-1.5 rounded-full",
          ok === undefined ? "bg-term-muted" : ok ? "bg-term-up" : "bg-term-down",
        )}
      />
      {label}
    </span>
  );
}

/** Settings cog — inline SVG so it inherits currentColor (theme + accent aware). */
function GearIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
      focusable="false"
    >
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

function isTyping(): boolean {
  const el = document.activeElement;
  return (
    el instanceof HTMLInputElement ||
    el instanceof HTMLTextAreaElement ||
    el instanceof HTMLSelectElement ||
    (el instanceof HTMLElement && el.isContentEditable)
  );
}

export default function App() {
  const [cmdOpen, setCmdOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const t = useT();
  const openWidget = useWorkspace((s) => s.openWidget);

  const { data: health } = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    // Poll fast while the first-run data bootstrap is running so the progress toast is live;
    // otherwise the normal 30s heartbeat.
    refetchInterval: (query) =>
      query.state.data?.bootstrap?.state === "running" ? 2500 : 30_000,
    retry: 1,
  });

  // Point realtime topics at the backend's configured crypto exchange (Settings → Market Data),
  // so live widgets open against it instead of the hardcoded default. Saving the setting updates
  // this directly; this keeps it in sync if the override changes server-side.
  useEffect(() => {
    if (health?.crypto_exchange) setStreamExchange(health.crypto_exchange);
  }, [health?.crypto_exchange]);

  // First launch only: seed the link-channel tickers from the per-asset-class default symbols
  // (Settings → Market Data). Guarded on the absence of the persisted linking store so it never
  // clobbers symbols the user has already set in a channel.
  useEffect(() => {
    if (localStorage.getItem("oft-linking")) return;
    api.marketDataSettings()
      .then((s) => useLinking.getState().seedDefaults(
        s.categories.equity.default_symbol,
        s.categories.crypto.default_symbol,
      ))
      .catch(() => {});
  }, []);

  // Keyboard-first: Ctrl+K is global; single-key shortcuts only when not typing.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCmdOpen((v) => !v);
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey || isTyping()) return;
      const key = e.key.toLowerCase();
      if (key === "t") {
        e.preventDefault();
        setCmdOpen(true);
      } else if (key === "c") {
        e.preventDefault();
        openWidget("chart");
      } else if (key === "n") {
        e.preventDefault();
        openWidget("news");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openWidget]);

  return (
    <div className="flex h-full flex-col bg-term-bg">
      <header className="flex h-9 shrink-0 items-center gap-4 border-b border-term-border bg-term-elev px-3 shadow-elev-1">
        <span className="text-xs font-bold tracking-widest text-term-accent [text-shadow:0_0_10px_rgb(var(--term-accent)/0.5)]">OFT</span>
        <span className="hidden text-xs font-semibold text-term-text/90 sm:block">Open Financial Terminal</span>

        <button
          onClick={() => setCmdOpen(true)}
          className="focus-ring flex items-center gap-2 rounded border border-term-border bg-term-sunken px-2.5 py-1 text-[11px] text-term-muted transition-colors hover:border-term-accent hover:text-term-text"
        >
          {t("app.search")}
          <kbd className="rounded border border-term-border px-1 text-[9px]">Ctrl K</kbd>
        </button>

        <div className="ml-auto flex items-center gap-3">
          <RunningIndicator />
          <StatusDot ok={health?.qhfi.ok} label="qhfi" />
          <StatusDot ok={health?.llm.ok} label={health?.qhfi.llm_model?.split("/").pop() ?? "llm"} />
          <button
            onClick={() => setSettingsOpen(true)}
            aria-label={t("app.settings")}
            className="focus-ring grid place-items-center rounded border border-term-border p-1.5 text-term-muted transition-colors hover:border-term-accent hover:text-term-accent"
            title={t("app.settings")}
          >
            <GearIcon className="h-3.5 w-3.5" />
          </button>
        </div>
      </header>

      <WorkspaceTabs />

      <main className="min-h-0 flex-1">
        <DockviewWorkspace />
      </main>

      <CommandBar
        open={cmdOpen}
        onClose={() => setCmdOpen(false)}
        onOpenSettings={() => setSettingsOpen(true)}
      />
      <SettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} />
      <BootstrapToast bootstrap={health?.bootstrap} />
    </div>
  );
}
