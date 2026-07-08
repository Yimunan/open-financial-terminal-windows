import { useEffect, useState } from "react";
import { Command } from "cmdk";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Asset } from "../api/types";
import { useT, widgetTitle } from "../lib/i18n";
import { useLinking } from "../state/linking";
import { useSettings } from "../state/settings";
import { useWorkspace } from "../state/workspace";
import { WIDGET_TYPES, type WidgetType } from "../workspace/widgetRegistry";

/** Global Ctrl+K palette: ticker jump, widget launcher, theme/workspace commands, and a
 * natural-language fallback that routes free text to the LLM screener (`/api/ask`).
 */
export default function CommandBar({
  open,
  onClose,
  onOpenSettings,
}: {
  open: boolean;
  onClose: () => void;
  onOpenSettings: () => void;
}) {
  const [query, setQuery] = useState("");
  const t = useT();
  const lang = useSettings((s) => s.language);
  const openWidget = useWorkspace((s) => s.openWidget);
  const setSymbol = useLinking((s) => s.setSymbol);
  const toggleTheme = useSettings((s) => s.toggleTheme);

  useEffect(() => {
    if (!open) setQuery("");
  }, [open]);

  const { data: hits } = useQuery({
    queryKey: ["search", query],
    queryFn: () => api.search(query),
    enabled: open && query.trim().length >= 1,
    staleTime: 60_000,
  });

  // News topics (built-in Market/Macro + user "interest" subscriptions) — each opens a topic widget.
  const { data: topics } = useQuery({
    queryKey: ["news-topics"],
    queryFn: () => api.newsTopics(),
    enabled: open,
    staleTime: 60_000,
  });

  const pickTicker = (symbol: string, asset: Asset) => {
    setSymbol("red", { symbol, asset });
    const dv = useWorkspace.getState().api;
    const hasChart = dv?.panels.some((p) => p.id.startsWith("chart"));
    if (!hasChart) openWidget("chart", { channel: "red" });
    onClose();
  };

  const askNl = (q: string) => {
    openWidget("screener", { channel: "none", initialQuery: q });
    onClose();
  };

  const nlCandidate = query.trim().length > 8 && query.trim().includes(" ");
  const itemClass =
    "cursor-pointer rounded px-2 py-1.5 text-sm normal-case tracking-normal text-term-text data-[selected=true]:bg-term-border/60";
  const groupClass = "px-1 py-1 text-[10px] uppercase tracking-wider text-term-muted";

  return (
    <Command.Dialog
      open={open}
      onOpenChange={(v) => !v && onClose()}
      shouldFilter={true}
      label="Command bar"
    >
      <div className="overflow-hidden rounded-lg border border-term-border bg-term-elev shadow-elev-3">
        <Command.Input
          value={query}
          onValueChange={setQuery}
          placeholder={t("cmd.placeholder")}
          className="w-full border-b border-term-border bg-term-sunken px-4 py-3 text-sm text-term-text outline-none placeholder:text-term-muted"
        />
        <Command.List className="max-h-[50vh] overflow-auto p-1.5 text-sm">
          <Command.Empty className="px-3 py-4 text-xs text-term-muted">
            {t("cmd.empty")}
          </Command.Empty>

          {(hits?.results.length ?? 0) > 0 && (
            <Command.Group heading={t("cmd.tickers")} className={groupClass}>
              {hits!.results.map((h) => (
                <Command.Item
                  key={`${h.symbol}-${h.universe}`}
                  value={`ticker ${h.symbol} ${h.name ?? ""} ${h.sector ?? ""}`}
                  onSelect={() => pickTicker(h.symbol, h.asset)}
                  className={`flex items-center justify-between ${itemClass}`}
                >
                  <span className="font-mono font-semibold">{h.symbol}</span>
                  <span className="truncate pl-2 text-xs text-term-muted">
                    {h.asset} · {h.sector ?? h.name ?? h.universe}
                  </span>
                </Command.Item>
              ))}
            </Command.Group>
          )}

          <Command.Group heading={t("cmd.widgets")} className={groupClass}>
            {WIDGET_TYPES.filter((type) => type !== "topicnews").map((type: WidgetType) => (
              <Command.Item
                key={type}
                value={`open ${type} ${widgetTitle(type, lang)} widget`}
                onSelect={() => {
                  openWidget(type);
                  onClose();
                }}
                className={itemClass}
              >
                {t("cmd.open", { x: widgetTitle(type, lang) })}
              </Command.Item>
            ))}
          </Command.Group>

          {(topics?.topics.length ?? 0) > 0 && (
            <Command.Group heading={t("cmd.newsTopics")} className={groupClass}>
              {topics!.topics.map((tp) => (
                // a multi-label topic yields one entry per label (same key) — key on both
                <Command.Item
                  key={`${tp.key}:${tp.label}`}
                  value={`open news topic ${tp.label}`}
                  onSelect={() => {
                    openWidget("topicnews", { category: tp.key, label: tp.label });
                    onClose();
                  }}
                  className={itemClass}
                >
                  {t("cmd.open", { x: tp.label })}
                </Command.Item>
              ))}
            </Command.Group>
          )}

          <Command.Group heading={t("cmd.commands")} className={groupClass}>
            <Command.Item
              value="settings preferences language color 设置"
              onSelect={() => {
                onOpenSettings();
                onClose();
              }}
              className={itemClass}
            >
              {t("cmd.openSettings")}
            </Command.Item>
            <Command.Item
              value="toggle theme light dark 主题"
              onSelect={() => {
                toggleTheme();
                onClose();
              }}
              className={itemClass}
            >
              {t("cmd.toggleTheme")}
            </Command.Item>
            <Command.Item
              value="save workspace layout 保存工作区"
              onSelect={() => {
                useWorkspace.getState().saveCurrent();
                onClose();
              }}
              className={itemClass}
            >
              {t("cmd.saveWorkspace")}
            </Command.Item>
          </Command.Group>

          {nlCandidate && (
            <Command.Group heading={t("cmd.assistant")} className={groupClass}>
              <Command.Item
                value={`ask ${query}`}
                onSelect={() => askNl(query)}
                className={`${itemClass} !text-term-accent`}
              >
                {t("cmd.ask", { x: query })}
              </Command.Item>
            </Command.Group>
          )}
        </Command.List>
      </div>
    </Command.Dialog>
  );
}
