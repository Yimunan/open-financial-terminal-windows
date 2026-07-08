import { useEffect, useRef, useState } from "react";
import { cx } from "../lib/format";

/** Wraps a numeric cell; flashes green/red when `value` moves up/down. */
export default function FlashCell({
  value,
  children,
  className,
}: {
  value: number | null | undefined;
  children: React.ReactNode;
  className?: string;
}) {
  const prev = useRef<number | null | undefined>(value);
  const [flash, setFlash] = useState<"up" | "down" | null>(null);
  const [seq, setSeq] = useState(0);

  useEffect(() => {
    if (value != null && prev.current != null && value !== prev.current) {
      setFlash(value > prev.current ? "up" : "down");
      setSeq((s) => s + 1); // key bump re-triggers the CSS animation
    }
    prev.current = value;
  }, [value]);

  return (
    <span key={seq} className={cx(className, flash === "up" && "flash-up", flash === "down" && "flash-down")}>
      {children}
    </span>
  );
}
