export const projectEvents = [
  "agent_budget_changed",
  "agent_context_budget_changed",
  "agent_context_policy_changed",
  "artifact_available",
  "artifact_changed",
  "changed",
  "compile_finished",
  "context_changed",
  "controller_changed",
  "cursor_reset",
  "evaluation_protocol_revised",
  "goal_contract_revised",
  "metric_available",
  "node_changed",
  "paper_changed",
  "plan_changed",
  "project_changed",
  "project_imported",
  "route_review_available",
  "run_changed",
  "run_completed",
  "run_failed",
  "run_progress",
  "run_queued",
  "run_recovery_queued",
  "run_resumed",
  "run_started",
  "run_time_budget_recalculated",
  "run_waiting",
  "tool_finished"
] as const;
type Listener = () => void;
const streams = new Map<string, { source: EventSource; listeners: Set<Listener>; timer?: ReturnType<typeof setTimeout> }>();
export function subscribeProject(projectId: string, listener: Listener) {
  let entry = streams.get(projectId);
  if (!entry) {
    const source = new EventSource(`/api/projects/${encodeURIComponent(projectId)}/events`);
    entry = { source, listeners: new Set() };
    streams.set(projectId, entry);
    const shared = entry;
    const refresh = () => {
      if (shared.timer) return;
      shared.timer = setTimeout(() => {
        shared.timer = undefined;
        shared.listeners.forEach((fn) => fn());
      }, 150);
    };
    projectEvents.forEach((name) => source.addEventListener(name, refresh));
    source.onopen = refresh;
    source.onmessage = refresh;
  }
  entry.listeners.add(listener);
  return () => {
    entry!.listeners.delete(listener);
    if (!entry!.listeners.size) {
      entry!.source.close();
      clearTimeout(entry!.timer);
      streams.delete(projectId);
    }
  };
}
