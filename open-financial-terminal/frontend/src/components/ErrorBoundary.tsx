import { Component, type ReactNode } from "react";
import { translate } from "../lib/i18n";
import { useSettings } from "../state/settings";

interface Props {
  label: string;
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/** Per-widget boundary: a crashing widget shows its error in place instead of
 * blanking the whole workspace.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error): void {
    console.error(`[widget:${this.props.label}]`, error.message, error.stack);
  }

  render() {
    if (this.state.error) {
      // Class component, no hooks — read the language directly; an error tile not
      // re-rendering on a live language flip is acceptable.
      const lang = useSettings.getState().language;
      return (
        <div className="flex h-full flex-col gap-2 bg-term-panel p-4">
          <div className="text-xs font-semibold text-term-down">
            {translate(lang, "error.crashed", { x: this.props.label })}
          </div>
          <pre className="overflow-auto whitespace-pre-wrap font-mono text-[10px] text-term-muted">
            {this.state.error.message}
          </pre>
          <button
            onClick={() => this.setState({ error: null })}
            className="self-start rounded border border-term-border px-2 py-0.5 text-[10px] uppercase tracking-wide text-term-muted hover:text-term-text"
          >
            {translate(lang, "common.retry")}
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
