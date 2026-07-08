import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type {
  FilingCategory,
  InsiderTxn,
  InstitutionalHolder,
} from "../api/types";
import { cx, fmtAgo, fmtCompact, fmtPct, upDownClass } from "../lib/format";
import { useT } from "../lib/i18n";
import type { WidgetProps } from "../workspace/widgetRegistry";
import { SkeletonRows, TextButton, WidgetShell, useWidgetSymbol } from "./shell";
import { Badge, type BadgeTone } from "../components/Badge";
import { EmptyState, ErrorState } from "../components/States";

type Tab = "feed" | "insider" | "holders";

const CATEGORIES: FilingCategory[] = [
  "all",
  "financials",
  "events",
  "insider",
  "ownership",
  "governance",
  "offerings",
];

const CAT_TONE: Record<string, BadgeTone> = {
  financials: "accent",
  events: "accent",
  insider: "up",
  ownership: "muted",
  governance: "muted",
  offerings: "muted",
  other: "muted",
};

const FILINGS_STALE = 6 * 60 * 60_000; // 6h — SEC filings don't change intraday

export default function PublicFilingsWidget(props: WidgetProps) {
  const { symbol, asset, channel, setChannel } = useWidgetSymbol(props);
  const t = useT();
  const [tab, setTab] = useState<Tab>("feed");
  const isEquity = asset === "equity";

  return (
    <WidgetShell
      channel={channel}
      onChannelChange={setChannel}
      badge="eod"
      toolbar={
        <>
          <span className="font-mono text-sm font-bold">{symbol}</span>
          <span className="hidden text-[10px] uppercase tracking-wider text-term-muted sm:inline">
            {t("filings.subtitle")}
          </span>
          <div className="ml-auto flex items-center gap-1">
            {(["feed", "insider", "holders"] as Tab[]).map((k) => (
              <TextButton key={k} active={tab === k} onClick={() => setTab(k)}>
                {t(`filings.tab.${k}` as Parameters<typeof t>[0])}
              </TextButton>
            ))}
          </div>
        </>
      }
    >
      {!isEquity ? (
        <EmptyState title={t("filings.cryptoOnly")} />
      ) : tab === "feed" ? (
        <FeedTab symbol={symbol} />
      ) : tab === "insider" ? (
        <InsiderTab symbol={symbol} />
      ) : (
        <HoldersTab symbol={symbol} />
      )}
    </WidgetShell>
  );
}

/* ── Filings feed ───────────────────────────────────────────────────────────── */

function FeedTab({ symbol }: { symbol: string }) {
  const t = useT();
  const [cat, setCat] = useState<FilingCategory>("all");
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["filings", symbol, cat],
    queryFn: () => api.filings(symbol, cat),
    staleTime: FILINGS_STALE,
    retry: 1,
  });

  return (
    <div>
      <div className="flex flex-wrap items-center gap-1 border-b border-term-border/40 px-2 py-1.5">
        {CATEGORIES.map((c) => (
          <TextButton key={c} active={cat === c} onClick={() => setCat(c)}>
            {t(`filings.cat.${c}` as Parameters<typeof t>[0])}
          </TextButton>
        ))}
        {data?.coverage === "cached" && (
          <Badge tone="muted" className="ml-auto">
            {t("filings.cached")}
          </Badge>
        )}
      </div>

      {isLoading && <SkeletonRows rows={10} />}
      {error && <ErrorState message={(error as Error).message} onRetry={() => refetch()} />}
      {data && data.items.length === 0 && <EmptyState title={t("filings.empty", { x: symbol })} />}
      {data && data.items.length > 0 && (
        <ul className="divide-y divide-term-border/30">
          {data.items.map((f) => (
            <li key={f.accession + f.form}>
              <a
                href={f.url}
                target="_blank"
                rel="noreferrer"
                className="focus-ring block px-3 py-2 hover:bg-term-border/15"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex min-w-0 items-center gap-2">
                    <Badge tone={CAT_TONE[f.category] ?? "muted"}>{f.form}</Badge>
                    <span className="truncate text-xs text-term-text">{f.label || "—"}</span>
                  </div>
                  <span className="shrink-0 text-[10px] text-term-muted" title={f.filing_date}>
                    {fmtAgo(f.filed)}
                  </span>
                </div>
                {f.report_date && (
                  <div className="mt-0.5 text-[10px] text-term-muted/80">
                    {t("filings.period")}: {f.report_date}
                  </div>
                )}
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ── Insider transactions (Form 4) ──────────────────────────────────────────── */

function InsiderTab({ symbol }: { symbol: string }) {
  const t = useT();
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["filings-insider", symbol],
    queryFn: () => api.filingsInsider(symbol),
    staleTime: FILINGS_STALE,
    retry: 1,
  });

  if (isLoading) return <SkeletonRows rows={10} />;
  if (error) return <ErrorState message={(error as Error).message} onRetry={() => refetch()} />;
  if (!data || data.items.length === 0)
    return <EmptyState title={t("filings.insiderEmpty", { x: symbol })} />;

  const w = data.summary.d90;
  return (
    <div>
      <div className="border-b border-term-border/40 px-3 py-2">
        <div className="text-[10px] uppercase tracking-wider text-term-muted">{t("filings.net90")}</div>
        <div className="mt-1 flex items-center gap-4 text-xs">
          <span className={upDownClass(w.net_value)}>
            {w.net_value >= 0 ? "▲" : "▼"} {fmtCompact(Math.abs(w.net_value))}
          </span>
          <span className="text-term-up">
            {t("filings.buys")}: {w.n_buys} · {fmtCompact(w.buy_value)}
          </span>
          <span className="text-term-down">
            {t("filings.sells")}: {w.n_sells} · {fmtCompact(w.sell_value)}
          </span>
        </div>
      </div>
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr className="border-b border-term-border text-[10px] uppercase tracking-wider text-term-muted">
            <th className="px-2 py-1 text-left font-medium">{t("common.time")}</th>
            <th className="px-2 py-1 text-left font-medium">{t("filings.insiderCol")}</th>
            <th className="px-2 py-1 text-center font-medium">{t("filings.txn")}</th>
            <th className="px-2 py-1 text-right font-medium">{t("filings.shares")}</th>
            <th className="px-2 py-1 text-right font-medium">{t("filings.price")}</th>
            <th className="px-2 py-1 text-right font-medium">{t("filings.value")}</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((x, i) => (
            <InsiderRow key={i} x={x} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function InsiderRow({ x }: { x: InsiderTxn }) {
  const buy = x.code === "P" || x.acq_disp === "A";
  const sell = x.code === "S" || x.acq_disp === "D";
  const tone: BadgeTone = x.code === "P" ? "up" : x.code === "S" ? "down" : "muted";
  return (
    <tr className="border-b border-term-border/30">
      <td className="px-2 py-1 text-term-muted" title={x.date}>{x.date}</td>
      <td className="px-2 py-1">
        <div className="max-w-[12rem] truncate text-term-text" title={`${x.insider} — ${x.role}`}>
          {x.insider || "—"}
        </div>
        {x.role && <div className="truncate text-[9px] text-term-muted">{x.role}</div>}
      </td>
      <td className="px-2 py-1 text-center">
        <Badge tone={tone}>{x.code || "?"}</Badge>
      </td>
      <td className={cx("px-2 py-1 text-right font-mono", buy ? "text-term-up" : sell ? "text-term-down" : "")}>
        {(buy ? "+" : sell ? "−" : "") + fmtCompact(x.shares)}
      </td>
      <td className="px-2 py-1 text-right font-mono text-term-muted">
        {x.price ? fmtCompact(x.price) : "—"}
      </td>
      <td className="px-2 py-1 text-right font-mono">{x.value ? fmtCompact(x.value) : "—"}</td>
    </tr>
  );
}

/* ── Institutional holders (13F) ────────────────────────────────────────────── */

function HoldersTab({ symbol }: { symbol: string }) {
  const t = useT();
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["filings-holders", symbol],
    queryFn: () => api.filingsHolders(symbol),
    staleTime: FILINGS_STALE,
    retry: 1,
  });

  if (isLoading) return <SkeletonRows rows={10} />;
  if (error) return <ErrorState message={(error as Error).message} onRetry={() => refetch()} />;
  if (!data || data.items.length === 0)
    return <EmptyState title={t("filings.holdersEmpty", { x: symbol })} />;

  return (
    <div>
      {data.period && (
        <div className="border-b border-term-border/40 px-3 py-1.5 text-[10px] uppercase tracking-wider text-term-muted">
          {t("filings.tab.holders")} · {t("filings.asof", { x: data.period })}
        </div>
      )}
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr className="border-b border-term-border text-[10px] uppercase tracking-wider text-term-muted">
            <th className="px-2 py-1 text-left font-medium">{t("filings.manager")}</th>
            <th className="px-2 py-1 text-right font-medium">{t("filings.value")}</th>
            <th className="px-2 py-1 text-right font-medium">{t("filings.book")}</th>
            <th className="px-2 py-1 text-right font-medium">{t("filings.qoq")}</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((h, i) => (
            <HolderRow key={i} h={h} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function HolderRow({ h }: { h: InstitutionalHolder }) {
  return (
    <tr className="border-b border-term-border/30">
      <td className="px-2 py-1">
        <div className="max-w-[14rem] truncate text-term-text" title={h.manager}>{h.manager}</div>
        <div className="text-[9px] text-term-muted">{fmtCompact(h.shares)} sh</div>
      </td>
      <td className="px-2 py-1 text-right font-mono">{fmtCompact(h.value_usd)}</td>
      <td className="px-2 py-1 text-right font-mono text-term-muted">
        {h.pct_of_book ? fmtPct(h.pct_of_book, false) : "—"}
      </td>
      <td className={cx("px-2 py-1 text-right font-mono", upDownClass(h.change_shares))}>
        {h.change_shares === 0
          ? "—"
          : `${h.change_shares > 0 ? "▲" : "▼"} ${fmtCompact(Math.abs(h.change_shares))}`}
      </td>
    </tr>
  );
}
