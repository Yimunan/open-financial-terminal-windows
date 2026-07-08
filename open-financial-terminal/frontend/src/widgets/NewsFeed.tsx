import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { cx, fmtAgo, fmtStamp } from "../lib/format";
import { useT, type I18nKey } from "../lib/i18n";
import type { NewsItem } from "../api/types";
import type { Channel } from "../state/linking";
import { SkeletonRows, WidgetShell } from "./shell";
import { Badge, sentimentBadge } from "../components/Badge";
import { EmptyState, ErrorState } from "../components/States";

const POLL_MS = 30_000;

/** Hover text spelling out the signals behind a headline's composite rank score. */
function rankBreakdown(n: NewsItem): string {
  const parts = [`rank ${(n.rank_score ?? 0).toFixed(2)}`];
  if (n.relevance != null) parts.push(`relevance ${n.relevance.toFixed(2)}`);
  if (n.source) parts.push(`source ${n.source}${n.source_weight != null ? ` (${n.source_weight})` : ""}`);
  if (n.score != null) parts.push(`sentiment ${n.score.toFixed(2)}`);
  return parts.join(" · ");
}

export interface NewsFeedProps {
  /** Bold label at the toolbar's left (a ticker, or a category name). */
  title: string;
  /** Pre-translated subtitle shown next to the live dot. */
  subtitle: string;
  /** Pre-translated message for the empty state. */
  emptyMessage: string;
  /** Base react-query key; the active sort is appended automatically. */
  queryKey: readonly unknown[];
  /** Fetch the feed; `rank` is true when the "Ranked" sort is active. */
  queryFn: (rank: boolean) => Promise<{ items: NewsItem[] }>;
  /** Reset the new-headline flash memory when this changes (e.g. the symbol). */
  resetKey: string;
  /** Channel-link wiring; omit for symbol-agnostic feeds (hides the channel dots). */
  channel?: Channel;
  onChannelChange?: (c: Channel) => void;
  /** Show a search box that filters the loaded headlines into a dropdown. */
  searchable?: boolean;
}

/** Shared News feed: polling, new-headline flash, sort toggle, source filter, sentiment + rank
 * rendering. Both the per-symbol News widget and the topic Market/Macro widgets wrap this. */
export default function NewsFeed({
  title,
  subtitle,
  emptyMessage,
  queryKey,
  queryFn,
  resetKey,
  channel,
  onChannelChange,
  searchable,
}: NewsFeedProps) {
  const t = useT();
  const [publisher, setPublisher] = useState<string>("all");
  const [sort, setSort] = useState<"ranked" | "newest">("ranked");
  const [srcOpen, setSrcOpen] = useState(false);
  const srcRef = useRef<HTMLDivElement>(null);
  const [search, setSearch] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const searchRef = useRef<HTMLDivElement>(null);

  // close the source picker on outside click
  useEffect(() => {
    if (!srcOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (srcRef.current && !srcRef.current.contains(e.target as Node)) setSrcOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [srcOpen]);

  // close the search results dropdown on outside click
  useEffect(() => {
    if (!searchOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (searchRef.current && !searchRef.current.contains(e.target as Node)) setSearchOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [searchOpen]);

  const { data, isLoading, error, dataUpdatedAt } = useQuery({
    queryKey: [...queryKey, sort],
    queryFn: () => queryFn(sort === "ranked"),
    refetchInterval: POLL_MS, // roll the feed in near real time
    refetchIntervalInBackground: true,
    staleTime: POLL_MS,
    retry: 1,
  });

  // Track which headlines we've already shown so freshly-arrived ones can flash in.
  // Reset the memory when the feed's subject changes.
  const seen = useRef<Set<string>>(new Set());
  const firstLoad = useRef(true);
  useEffect(() => {
    seen.current = new Set();
    firstLoad.current = true;
  }, [resetKey]);

  const newKeys = useRef<Set<string>>(new Set());
  if (data) {
    const fresh = new Set<string>();
    for (const it of data.items) {
      if (!seen.current.has(it.title)) {
        if (!firstLoad.current) fresh.add(it.title); // don't flash the entire first page
        seen.current.add(it.title);
      }
    }
    newKeys.current = fresh;
    firstLoad.current = false;
  }

  // tick so relative timestamps ("2m") advance between polls
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), 30_000);
    return () => clearInterval(id);
  }, []);

  const publishers = useMemo(() => {
    const set = new Set<string>();
    data?.items.forEach((n) => n.publisher && set.add(n.publisher));
    return ["all", ...Array.from(set).sort()];
  }, [data]);

  const items = data?.items.filter((n) => publisher === "all" || n.publisher === publisher) ?? [];
  const updatedAgo = dataUpdatedAt ? fmtAgo(Math.floor(dataUpdatedAt / 1000)) : "";

  // Search the full loaded feed (ignoring the publisher filter) into a dropdown of matches.
  const matches = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return [];
    return (data?.items ?? []).filter((n) => n.title.toLowerCase().includes(q)).slice(0, 12);
  }, [search, data]);

  return (
    <WidgetShell
      channel={channel}
      onChannelChange={onChannelChange}
      toolbar={
        <>
          <span className="font-mono text-sm font-bold">{title}</span>
          <span className="flex shrink-0 items-center gap-1 text-[10px] uppercase tracking-wider text-term-muted">
            <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-term-up" />
            {subtitle}
          </span>
          <div className="ml-auto flex shrink-0 items-center gap-1.5">
            {searchable && (
              <div ref={searchRef} className="relative">
                <input
                  value={search}
                  onChange={(e) => { setSearch(e.target.value); setSearchOpen(true); }}
                  onFocus={() => setSearchOpen(true)}
                  placeholder={t("news.search")}
                  aria-label={t("news.search")}
                  spellCheck={false}
                  className="focus-ring w-28 rounded border border-term-border bg-term-sunken px-1.5 py-0.5 text-[10px] text-term-text placeholder:text-term-muted focus:w-40 focus:border-term-accent"
                />
                {searchOpen && search.trim() && (
                  // results dropdown: matching headlines, click to open the article
                  <div className="absolute right-0 top-6 z-50 max-h-64 w-72 overflow-auto rounded border border-term-border bg-term-elev py-0.5 shadow-elev-2">
                    {matches.length === 0 ? (
                      <div className="px-2 py-1.5 text-[11px] text-term-muted">{t("news.searchEmpty")}</div>
                    ) : (
                      matches.map((n) => (
                        <a
                          key={n.title}
                          href={n.link ?? undefined}
                          target="_blank"
                          rel="noreferrer"
                          onClick={() => setSearchOpen(false)}
                          className="block px-2 py-1.5 text-left text-[11px] leading-snug text-term-muted hover:bg-term-border/40 hover:text-term-text"
                        >
                          {n.title}
                        </a>
                      ))
                    )}
                  </div>
                )}
              </div>
            )}
            {/* Ranked / Newest — server ranks by the composite router score when Ranked. */}
            <div
              role="group"
              aria-label={t("news.sort")}
              className="flex items-center overflow-hidden rounded border border-term-border bg-term-sunken text-[10px]"
            >
              {(["ranked", "newest"] as const).map((s) => (
                <button
                  key={s}
                  onClick={() => setSort(s)}
                  aria-pressed={sort === s}
                  className={cx(
                    "px-1.5 py-0.5 transition-colors",
                    sort === s ? "bg-term-accent/15 text-term-accent" : "text-term-muted hover:text-term-text",
                  )}
                >
                  {t(s === "ranked" ? "news.ranked" : "news.newest")}
                </button>
              ))}
            </div>
            {publishers.length > 2 && (
            <div ref={srcRef} className="relative">
              <button
                onClick={() => setSrcOpen((v) => !v)}
                aria-label="Filter by source"
                aria-expanded={srcOpen}
                className="focus-ring flex items-center gap-1 rounded border border-term-border bg-term-sunken px-1.5 py-0.5 text-[10px] text-term-muted transition-colors hover:text-term-text"
              >
                <span className="max-w-[120px] truncate">
                  {publisher === "all" ? `${t("news.allSources")} (${data?.items.length ?? 0})` : publisher}
                </span>
                <span className="text-[8px]" aria-hidden>▾</span>
              </button>
              {srcOpen && (
                // rolling window: a fixed-height scrollable list of sources
                <div className="absolute right-0 top-6 z-50 max-h-48 w-48 overflow-auto rounded border border-term-border bg-term-elev py-0.5 shadow-elev-2">
                  {publishers.map((p) => (
                    <button
                      key={p}
                      onClick={() => { setPublisher(p); setSrcOpen(false); }}
                      className={cx(
                        "block w-full truncate px-2 py-1 text-left text-[11px] hover:bg-term-border/40",
                        publisher === p ? "text-term-accent" : "text-term-muted",
                      )}
                    >
                      {p === "all" ? `${t("news.allSources")} (${data?.items.length ?? 0})` : p}
                    </button>
                  ))}
                </div>
              )}
            </div>
            )}
          </div>
        </>
      }
    >
      {isLoading && <SkeletonRows rows={8} />}
      {error && <ErrorState message={(error as Error).message} />}
      {data && items.length === 0 && <EmptyState title={emptyMessage} />}
      <div className="divide-y divide-term-border/40">
        {items.map((n) => {
          const senti = n.sentiment ?? null;
          const conf = n.score != null ? Math.round(n.score * 100) : null;
          const isNew = newKeys.current.has(n.title);
          const stamp = fmtStamp(n.published);
          const ago = fmtAgo(n.published);
          return (
            <a
              key={n.title}
              href={n.link ?? undefined}
              target="_blank"
              rel="noreferrer"
              className={cx("block px-3 py-2 hover:bg-term-border/20", isNew && "news-new")}
            >
              <div className="flex items-start justify-between gap-2">
                <span className="text-xs leading-snug text-term-text">{n.title}</span>
                {senti && (() => {
                  const { tone, icon } = sentimentBadge(senti);
                  return (
                    <Badge
                      tone={tone}
                      icon={icon}
                      title={conf != null ? `confidence ${conf}%` : undefined}
                      style={conf != null ? { opacity: 0.45 + (Math.abs(conf) / 100) * 0.55 } : undefined}
                    >
                      {t(`news.${senti}` as I18nKey)}
                      {conf != null && ` ${conf}`}
                    </Badge>
                  );
                })()}
              </div>
              <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-term-muted">
                {n.publisher && <span>{n.publisher}</span>}
                {stamp && n.publisher && <span>·</span>}
                {stamp && <span className="font-mono">{stamp}</span>}
                {ago && <span className="font-mono text-term-muted/70">({ago})</span>}
                {sort === "ranked" && n.rank_score != null && (
                  <span
                    className="ml-auto cursor-help font-mono text-term-accent/70"
                    title={rankBreakdown(n)}
                  >
                    ★ {n.rank_score.toFixed(2)}
                  </span>
                )}
              </div>
            </a>
          );
        })}
      </div>
      {data && items.length > 0 && (
        <div className="px-3 py-1.5 text-center text-[9px] uppercase tracking-wider text-term-muted">
          {updatedAgo ? `updated ${updatedAgo} ago` : "live"}
        </div>
      )}
    </WidgetShell>
  );
}
