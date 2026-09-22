import type { TraceEvent } from "../types";

type Props = { events: TraceEvent[] };

const steps = [
  { label: "Question received", matches: ["run_start"] },
  { label: "RequestSpec extracted", matches: ["request_spec_built"] },
  { label: "Planner selected route", matches: ["plan_created"] },
  { label: "Workers completed", matches: ["worker_draft", "worker_provider_attempt"] },
  { label: "Evidence checked", matches: ["tool_call", "retrieval_query"] },
  { label: "Final answer generated", matches: ["final_answer"] },
];

export function AgentTrace({ events }: Props) {
  const eventTypes = new Set(events.map((event) => event.event_type));
  return (
    <section className="developer-card trace-card">
      <div className="developer-card-title">
        <span>04</span>
        <div><h3>执行状态</h3><p>Execution Trace</p></div>
      </div>
      <ul className="status-list">
        {steps.map((step) => {
          const observed = step.matches.some((name) => eventTypes.has(name));
          const notRequired = step.label === "Evidence checked" && events.length > 0 && !observed;
          return (
            <li className={observed ? "status-complete" : "status-idle"} key={step.label}>
              <span aria-hidden="true">{observed ? "✓" : "○"}</span>
              <strong>{notRequired ? "Tools not required" : step.label}</strong>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
