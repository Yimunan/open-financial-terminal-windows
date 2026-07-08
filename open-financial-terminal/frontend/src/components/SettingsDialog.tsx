import { useEffect, useState } from "react";
import { useT } from "../lib/i18n";
import { NavItem } from "./settings/common";
import AppearanceSection from "./settings/AppearanceSection";
import LlmSection from "./settings/LlmSection";
import McpSection from "./settings/McpSection";
import MarketDataSection from "./settings/MarketDataSection";
import DataProvidersSection from "./settings/DataProvidersSection";
import DataRefreshSection from "./settings/DataRefreshSection";
import NewsSection from "./settings/NewsSection";
import NewsTopicsSection from "./settings/NewsTopicsSection";
import DirectoriesSection from "./settings/DirectoriesSection";

type SectionKey =
  | "appearance"
  | "llm"
  | "mcp"
  | "marketData"
  | "providers"
  | "dataRefresh"
  | "news"
  | "topics"
  | "directories";

/** Grouped left-nav: categories keep the (now nine) sections legible instead of one flat list. */
const GROUPS: { label: string; items: { key: SectionKey; label: string }[] }[] = [
  { label: "General", items: [{ key: "appearance", label: "Appearance" }] },
  {
    label: "AI & Assistant",
    items: [
      { key: "llm", label: "LLM Model" },
      { key: "mcp", label: "MCP Servers" },
    ],
  },
  {
    label: "Market Data",
    items: [
      { key: "marketData", label: "Market Data" },
      { key: "providers", label: "Data Providers" },
      { key: "dataRefresh", label: "Data Refresh" },
    ],
  },
  {
    label: "News",
    items: [
      { key: "news", label: "News Sources" },
      { key: "topics", label: "News Topics" },
    ],
  },
  { label: "Directories", items: [{ key: "directories", label: "Data & Registries" }] },
];

/**
 * Settings shell — overlay, grouped nav, and section routing. Each section under ./settings owns its
 * own data-fetching + state and only mounts while active (so a fresh open re-reads current config).
 */
export default function SettingsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const t = useT();
  const [section, setSection] = useState<SectionKey>("appearance");

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <>
      <div className="fixed inset-0 z-50 bg-black/50" onClick={onClose} />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t("settings.title")}
        className="fixed left-1/2 top-[9vh] z-50 flex h-[min(600px,84vh)] w-[min(880px,95vw)] -translate-x-1/2 flex-col overflow-hidden rounded-lg border border-term-border bg-term-elev shadow-elev-3"
      >
        <div className="flex items-center justify-between border-b border-term-border px-4 py-2.5">
          <span className="text-sm font-semibold">{t("settings.title")}</span>
          <button
            onClick={onClose}
            aria-label="Close settings"
            className="focus-ring rounded text-term-muted hover:text-term-text"
          >
            ×
          </button>
        </div>

        <div className="flex min-h-0 flex-1">
          <nav className="w-48 shrink-0 space-y-2 overflow-y-auto border-r border-term-border p-2">
            {GROUPS.map((group) => (
              <div key={group.label} className="space-y-0.5">
                <div className="px-2.5 pb-0.5 pt-1 text-[9px] font-bold uppercase tracking-wider text-term-muted/70">
                  {group.label}
                </div>
                {group.items.map((s) => (
                  <NavItem key={s.key} active={section === s.key} onClick={() => setSection(s.key)}>
                    {s.label}
                  </NavItem>
                ))}
              </div>
            ))}
          </nav>

          <div className="min-h-0 flex-1 overflow-y-auto p-4">
            {section === "appearance" && <AppearanceSection />}
            {section === "llm" && <LlmSection />}
            {section === "mcp" && <McpSection />}
            {section === "marketData" && <MarketDataSection onGotoProviders={() => setSection("providers")} />}
            {section === "providers" && <DataProvidersSection />}
            {section === "dataRefresh" && <DataRefreshSection />}
            {section === "news" && <NewsSection />}
            {section === "topics" && <NewsTopicsSection />}
            {section === "directories" && <DirectoriesSection />}
          </div>
        </div>
      </div>
    </>
  );
}
