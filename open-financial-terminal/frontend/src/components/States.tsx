/** Shared empty / error / loading states so every widget renders the no-data,
 * failure, and pending cases the same way. SkeletonRows stays the skeleton source
 * of truth in widgets/shell.tsx; LoadingState is a thin re-export of it. */

import { type ReactNode } from "react";
import { cx } from "../lib/format";
import { useSettings } from "../state/settings";
import { SkeletonRows } from "../widgets/shell";

export function EmptyState({
  icon,
  title,
  hint,
  className,
}: {
  icon?: ReactNode;
  title: string;
  hint?: string;
  className?: string;
}) {
  const showIcons = useSettings((s) => s.showIcons);
  return (
    <div className={cx("flex h-full flex-col items-center justify-center gap-1 p-6 text-center", className)}>
      {icon && showIcons && <div className="text-2xl text-term-muted/60" aria-hidden>{icon}</div>}
      <div className="text-xs text-term-muted">{title}</div>
      {hint && <div className="text-[11px] text-term-muted/70">{hint}</div>}
    </div>
  );
}

export function ErrorState({
  message,
  onRetry,
  retryLabel = "Retry",
}: {
  message: string;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center" role="alert">
      <div className="text-lg text-term-down" aria-hidden>⚠</div>
      <div className="max-w-xs text-xs text-term-down">{message}</div>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="focus-ring rounded border border-term-border px-2.5 py-1 text-[11px] text-term-muted hover:text-term-text"
        >
          {retryLabel}
        </button>
      )}
    </div>
  );
}

export function LoadingState({ rows = 6 }: { rows?: number }) {
  return <SkeletonRows rows={rows} />;
}
