import { useEffect } from "react";
import { api } from "../api/client";
import { useT } from "../lib/i18n";
import type { WidgetProps } from "../workspace/widgetRegistry";
import NewsFeed from "./NewsFeed";

/** Symbol-agnostic topic news: a built-in topic (Market/Macro) or a user "interest" topic, keyed by
 * `params.category`. Each topic is its own widget panel (Dockview shows them as draggable tabs).
 * Scored + ranked like the per-symbol News feed, with a search box over the loaded headlines. */
export default function TopicNewsWidget(props: WidgetProps) {
  const t = useT();
  const category = (props.params.category as string) ?? "market";
  const label = (props.params.label as string) ?? t("widget.topicnews");

  // Keep the Dockview tab title in sync with the topic label.
  useEffect(() => {
    props.api.setTitle(label);
  }, [props.api, label]);

  return (
    <NewsFeed
      searchable
      title={label}
      subtitle={t("news.topic.subtitle")}
      emptyMessage={t("news.topicEmpty")}
      queryKey={["news-topic", category]}
      queryFn={(rank) => api.newsTopic(category, true, rank)}
      resetKey={category}
    />
  );
}
