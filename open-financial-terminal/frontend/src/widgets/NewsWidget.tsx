import { api } from "../api/client";
import { useT } from "../lib/i18n";
import type { WidgetProps } from "../workspace/widgetRegistry";
import { useWidgetSymbol } from "./shell";
import NewsFeed from "./NewsFeed";

/** Per-symbol news: headlines for the channel's ticker, LLM-scored and composite-ranked. */
export default function NewsWidget(props: WidgetProps) {
  const { symbol, channel, setChannel } = useWidgetSymbol(props);
  const t = useT();
  return (
    <NewsFeed
      title={symbol}
      subtitle={t("news.subtitle")}
      emptyMessage={t("news.empty", { x: symbol })}
      queryKey={["news", symbol]}
      queryFn={(rank) => api.news(symbol, true, rank)}
      resetKey={symbol}
      channel={channel}
      onChannelChange={setChannel}
    />
  );
}
