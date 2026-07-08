/** Shared widget chrome: toolbar with channel-link dots + data badge, plus the
 * `useWidgetSymbol` hook that resolves a widget's active symbol from its link
 * channel (shared) or its own panel params (channel = none).
 */

import { type MouseEvent, type ReactNode } from "react";
import { useT } from "../lib/i18n";
import {
  CHANNEL_DOT,
  LINK_CHANNELS,
  useLinking,
  type Channel,
  type ChannelContext,
  type SymbolRef,
} from "../state/linking";
import type { WidgetProps } from "../workspace/widgetRegistry";
import type { Asset } from "../api/types";
import { cx } from "../lib/format";
import { Badge, type BadgeTone } from "../components/Badge";

export function useWidgetSymbol(props: WidgetProps): {
  symbol: string;
  asset: Asset;
  channel: Channel;
  setChannel: (c: Channel) => void;
  setSymbol: (ref: SymbolRef) => void;
} {
  const channel = (props.params.channel ?? "red") as Channel;
  const linked = useLinking((s) => (channel === "none" ? null : s.symbols[channel]));
  const setLinked = useLinking((s) => s.setSymbol);

  const symbol = linked?.symbol ?? props.params.symbol ?? "AAPL";
  const asset = (linked?.asset ?? props.params.asset ?? "equity") as Asset;

  return {
    symbol,
    asset,
    channel,
    setChannel: (c) => props.api.updateParameters({ channel: c }),
    setSymbol: (ref) => {
      if (channel === "none") {
        props.api.updateParameters({ symbol: ref.symbol, asset: ref.asset });
      } else {
        setLinked(channel, ref);
      }
    },
  };
}

/** Richer sibling of `useWidgetSymbol`: resolves the full ChannelContext (symbol/asset plus any
 * timeframe/range/universe/factor that travels with the channel) and lets a widget set or patch it.
 * Opt-in — most widgets only need `useWidgetSymbol`. For `channel === "none"` the context is read
 * from / written to the panel's own params, mirroring `useWidgetSymbol`. */
export function useChannelContext(props: WidgetProps): {
  context: ChannelContext;
  channel: Channel;
  setContext: (ctx: Partial<ChannelContext>) => void;
} {
  const channel = (props.params.channel ?? "red") as Channel;
  const linked = useLinking((s) => (channel === "none" ? null : s.symbols[channel]));
  const setLinkedContext = useLinking((s) => s.setContext);

  const context: ChannelContext = {
    symbol: linked?.symbol ?? (props.params.symbol as string) ?? "AAPL",
    asset: (linked?.asset ?? props.params.asset ?? "equity") as Asset,
    timeframe: linked?.timeframe ?? (props.params.timeframe as ChannelContext["timeframe"]),
    range: linked?.range,
    universe: linked?.universe,
    factor: linked?.factor,
    extra: linked?.extra,
  };

  return {
    context,
    channel,
    setContext: (patch) => {
      const next = { ...context, ...patch };
      if (channel === "none") {
        props.api.updateParameters({ symbol: next.symbol, asset: next.asset, timeframe: next.timeframe });
      } else {
        setLinkedContext(channel as Exclude<Channel, "none">, next);
      }
    },
  };
}

/** Icon/glyph button with a forced aria-label and a visible keyboard focus ring.
 * Use for every control whose meaning is conveyed by an icon rather than text. */
export function IconButton({
  label,
  onClick,
  children,
  danger,
  className,
  title,
}: {
  label: string;
  onClick: (e: MouseEvent) => void;
  children: ReactNode;
  danger?: boolean;
  className?: string;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={title ?? label}
      className={cx(
        "focus-ring rounded text-term-muted transition-colors",
        danger ? "hover:text-term-down" : "hover:text-term-text",
        className,
      )}
    >
      {children}
    </button>
  );
}

/** Text/pill button with the canonical active/idle styling used in toolbars and
 * settings. Carries the focus ring and the standard px-2.5 py-1 rhythm. */
export function TextButton({
  active,
  danger,
  onClick,
  children,
  className,
  title,
}: {
  active?: boolean;
  danger?: boolean;
  onClick: () => void;
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className={cx(
        "focus-ring rounded border px-2.5 py-1 text-xs transition-colors",
        active
          ? "border-term-accent bg-term-accent/15 text-term-accent"
          : danger
            ? "border-term-border text-term-muted hover:border-term-down hover:text-term-down"
            : "border-term-border text-term-muted hover:text-term-text",
        className,
      )}
    >
      {children}
    </button>
  );
}

export function ChannelDots({
  channel,
  onChange,
}: {
  channel: Channel;
  onChange: (c: Channel) => void;
}) {
  return (
    <div className="flex items-center gap-1" title="Link channel">
      {LINK_CHANNELS.map((c) => (
        <button
          key={c}
          type="button"
          onClick={() => onChange(channel === c ? "none" : c)}
          aria-label={`link channel ${c}`}
          aria-pressed={channel === c}
          className={cx(
            "focus-ring h-2.5 w-2.5 rounded-full border transition-transform",
            channel === c ? "scale-125 border-term-text" : "border-transparent opacity-40 hover:opacity-80",
          )}
          style={{ backgroundColor: CHANNEL_DOT[c] }}
        />
      ))}
    </div>
  );
}

export type BadgeKind = "live" | "delayed" | "eod";

export function DataBadge({ kind }: { kind: BadgeKind }) {
  const t = useT();
  const tone: BadgeTone = kind === "live" ? "up" : kind === "delayed" ? "accent" : "muted";
  return (
    <Badge
      tone={tone}
      icon={kind === "live" ? <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-term-up align-middle" /> : undefined}
    >
      {t(`badge.${kind}`)}
    </Badge>
  );
}

export function WidgetShell({
  toolbar,
  channel,
  onChannelChange,
  badge,
  sendMenu,
  children,
}: {
  toolbar?: ReactNode;
  channel?: Channel;
  onChannelChange?: (c: Channel) => void;
  badge?: BadgeKind;
  /** Optional "Send to…" control (see components/SendToMenu) for cross-module hand-offs. */
  sendMenu?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex h-full flex-col bg-term-panel text-term-text">
      <div className="flex min-h-[30px] items-center justify-between gap-2 border-b border-term-border px-2 py-1">
        <div className="flex min-w-0 flex-1 items-center gap-2">{toolbar}</div>
        <div className="flex shrink-0 items-center gap-2">
          {sendMenu}
          {badge && <DataBadge kind={badge} />}
          {channel !== undefined && onChannelChange && (
            <ChannelDots channel={channel} onChange={onChannelChange} />
          )}
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">{children}</div>
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("skeleton", className)} />;
}

export function SkeletonRows({ rows = 6 }: { rows?: number }) {
  return (
    <div className="space-y-2 p-3">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-5 w-full" />
      ))}
    </div>
  );
}
