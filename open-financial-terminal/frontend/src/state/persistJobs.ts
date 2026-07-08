/** Shared `persist` helper for the chat-agent stores (backtest / factor monitor / chart studio).
 *
 * Their `jobs` map holds each conversation's `sessionId`, which is what the chat-history store
 * dedups on. That map used to be in-memory only while history was persisted — so a page reload
 * dropped the `sessionId`, and continuing the same chat minted a fresh one and archived a SECOND
 * history row. We now persist `jobs`, but only the light identity (sessionId + messages): the heavy
 * per-run payloads (`runs`) and live socket status are dropped and rebuilt on the next run.
 */

interface PersistableJob {
  status: "idle" | "running" | "done" | "error";
  messages: unknown[];
  runs: unknown[];
  activeRunId: string | null;
  sessionId: string;
}

/** Strip ephemeral/heavy fields and drop empty jobs, leaving just enough to resume a conversation. */
export function lightenJobs<J extends PersistableJob>(jobs: Record<string, J>): Record<string, J> {
  return Object.fromEntries(
    Object.entries(jobs)
      .filter(([, j]) => j.messages.length > 0)
      .map(([id, j]) => [id, { ...j, status: "idle", runs: [], activeRunId: null }]),
  ) as Record<string, J>;
}
