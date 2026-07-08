import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { NewListing } from "../api/types";
import { cx } from "../lib/format";
import type { WidgetProps } from "../workspace/widgetRegistry";
import { SkeletonRows, WidgetShell, useWidgetSymbol } from "./shell";
import { EmptyState, ErrorState } from "../components/States";

const DAY_OPTIONS = [7, 14, 30] as const;

/** Form-type filter chips. "listings" = 8-A12B family (exchange listings); "ipos" = 424B4. */
const FORM_FILTERS = [
  { key: "all", label: "All" },
  { key: "ipos", label: "IPOs" },
  { key: "listings", label: "Listings" },
] as const;
type FormFilter = (typeof FORM_FILTERS)[number]["key"];

function matchesFilter(form: string, f: FormFilter): boolean {
  if (f === "ipos") return form.startsWith("424B4");
  if (f === "listings") return form.startsWith("8-A12B");
  return true;
}

/** Newly listed securities, detected from SEC EDGAR filings (8-A12B exchange listings + 424B4 IPO
 * prospectuses). Clicking a row with a ticker retargets linked Chart/News/Research widgets. */
export default function NewListingsWidget(props: WidgetProps) {
  const { symbol: activeSymbol, channel, setChannel, setSymbol } = useWidgetSymbol(props);
  const [days, setDays] = useState<number>(14);
  const [filter, setFilter] = useState<FormFilter>("all");

  const { data, isLoading, error } = useQuery({
    queryKey: ["listings", days],
    queryFn: () => api.newListings(days),
    refetchInterval: 5 * 60_000,
    staleTime: 60_000,
    retry: 1,
  });

  const items = (data?.items ?? []).filter((it) => matchesFilter(it.form, filter));

  const chip = (on: boolean) =>
    cx(
      "rounded px-2 py-0.5 text-[10px] uppercase tracking-wide",
      on ? "bg-term-accent/20 text-term-accent" : "text-term-muted hover:text-term-text",
    );

  return (
    <WidgetShell
      channel={channel}
      onChannelChange={setChannel}
      toolbar={
        <div className="flex flex-wrap items-center gap-1.5">
          {DAY_OPTIONS.map((d) => (
            <button key={d} onClick={() => setDays(d)} className={chip(days === d)}>
              {d}d
            </button>
          ))}
          <span className="mx-0.5 text-term-border">|</span>
          {FORM_FILTERS.map((f) => (
            <button key={f.key} onClick={() => setFilter(f.key)} className={chip(filter === f.key)}>
              {f.label}
            </button>
          ))}
          <span className="ml-auto text-[10px] tabular-nums text-term-muted">
            {items.length}
            {data?.coverage === "cached" ? " · cached" : ""}
          </span>
        </div>
      }
    >
      {isLoading && <SkeletonRows />}
      {error && <ErrorState message={(error as Error).message} />}
      {!isLoading && !error && items.length === 0 && (
        <EmptyState title={`No new listings in the last ${days} days`} />
      )}
      {items.length > 0 && (
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b border-term-border text-[10px] uppercase tracking-wider text-term-muted">
              <th className="px-2 py-1 text-left font-medium">Date</th>
              <th className="px-2 py-1 text-left font-medium">Company</th>
              <th className="px-2 py-1 text-left font-medium">Type</th>
              <th className="w-8 px-2 py-1 text-right font-medium">SEC</th>
            </tr>
          </thead>
          <tbody>
            {items.map((it: NewListing) => {
              const ticker = it.tickers[0];
              const isActive = ticker != null && ticker === activeSymbol;
              return (
                <tr
                  key={it.accession}
                  onClick={() => ticker && setSymbol({ symbol: ticker, asset: "equity" })}
                  className={cx(
                    "border-b border-term-border/40",
                    ticker ? "cursor-pointer hover:bg-term-border/30" : "",
                    isActive && "bg-term-border/40",
                  )}
                >
                  <td className="whitespace-nowrap px-2 py-1 font-mono text-[10px] text-term-muted">
                    {it.filing_date}
                  </td>
                  <td className="px-2 py-1">
                    <div className="flex items-center gap-1.5">
                      {it.tickers.length > 0 && (
                        <span className="font-mono text-xs font-semibold text-term-accent">
                          {it.tickers.join(" ")}
                        </span>
                      )}
                      <span className="truncate text-[11px] text-term-text" title={it.company}>
                        {it.company}
                      </span>
                    </div>
                  </td>
                  <td className="px-2 py-1">
                    <span
                      className={cx(
                        "rounded px-1.5 py-0.5 text-[9px] uppercase tracking-wide",
                        it.form.startsWith("424B4")
                          ? "bg-term-up/15 text-term-up"
                          : "bg-term-border/40 text-term-muted",
                      )}
                      title={`${it.kind} · ${it.form}`}
                    >
                      {it.form.startsWith("424B4") ? "IPO" : "List"}
                    </span>
                  </td>
                  <td className="px-2 py-1 text-right">
                    <a
                      href={it.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      onClick={(e) => e.stopPropagation()}
                      className="text-[10px] text-term-muted hover:text-term-accent"
                      title="Open filing on SEC.gov"
                    >
                      ↗
                    </a>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </WidgetShell>
  );
}
