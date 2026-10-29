import type { TraceEvent } from "../types";

type Props = { events: TraceEvent[] };

const labels: Record<string, string> = {
  run_start: "Question received", request_spec_built: "RequestSpec extracted",
  plan_created: "Planner selected route", dependency_wait: "Dependent task waits for upstream",
  worker_draft: "Worker public answer recorded", worker_handoff: "Structured Handoff delivered",
  dependency_ready: "Upstream valid — dependent task ready", dependency_blocked: "Dependency blocked",
  tool_call: "Tool requested", retrieval_query: "Retrieval query recorded",
  tool_result: "Tool result recorded", memory_read: "Same-session history injected",
  synthesis_output: "Structured synthesis recorded", final_request_coverage: "Final coverage checked",
  checker_result: "Guardrail check recorded", final_answer: "Final answer available",
};

export function AgentTrace({ events }: Props) {
  return (
    <section className="developer-card trace-card">
      <div className="developer-card-title">
        <span>04</span>
        <div><h3>示例事件顺序</h3><p>Illustrative Trace · precomputed, no live execution</p></div>
      </div>
      <ul className="status-list">
        {events.map((event, index) => (
            <li className="status-complete" key={event.event_id || `${event.event_type}-${index}`}>
              <span aria-hidden="true">{index + 1}</span>
              <div>
                <strong>{labels[event.event_type || ""] || event.event_type || "Recorded event"}</strong>
                {typeof event.payload?.subtask_id === "string" && <small>{event.payload.subtask_id}</small>}
                {event.event_id && <code>{event.event_id}</code>}
              </div>
            </li>
        ))}
      </ul>
    </section>
  );
}
