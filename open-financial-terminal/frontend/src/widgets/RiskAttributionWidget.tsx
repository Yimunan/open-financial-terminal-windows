/** Risk Attribution widget — portfolio-level Barra factor + position risk decomposition.
 * Not symbol-linked (it's about the whole book), so it carries no channel dots; a Holdings/Paper
 * source toggle lives in the shared view. */

import { useState } from "react";
import type { WidgetProps } from "../workspace/widgetRegistry";
import { WidgetShell } from "./shell";
import AttributionView from "./risk/AttributionView";

export default function RiskAttributionWidget(_props: WidgetProps) {
  const [source, setSource] = useState<"holdings" | "paper">("holdings");
  return (
    <WidgetShell badge="eod">
      <AttributionView source={source} onSourceChange={setSource} />
    </WidgetShell>
  );
}
