import type { TraceEvent } from "../types";

type Props = { events: TraceEvent[] };

const steps = [
  { label: "Question", matches: ["run_start"] },
  { label: "RequestSpec", matches: ["request_spec_built"] },
  { label: "Planner", matches: ["plan_created"] },
  { label: "Workers", matches: ["worker_draft", "worker_provider_attempt"] },
  { label: "Tools", matches: ["tool_call", "retrieval_query"] },
  { label: "Checker", matches: ["checker_result"] },
  { label: "Final Answer", matches: ["final_answer"] },
];

export function AgentTrace({ events }: Props) {
  const eventTypes = new Set(events.map((event) => event.event_type));
  return (
    <section className="developer-card trace-card">
      <div className="developer-card-title">
        <span>04</span>
        <div><h3>Execution Trace</h3><p>Observable stages only</p></div>
      </div>
      <ol className="vertical-trace">
        {steps.map((step, index) => {
          const observed = step.matches.some((name) => eventTypes.has(name));
          const notRequired = step.label === "Tools" && events.length > 0 && !observed;
          return (
            <li className={observed ? "observed" : ""} key={step.label}>
              <span className="trace-node">{observed ? "✓" : index + 1}</span>
              <div><strong>{step.label}</strong>{notRequired && <small>Not required</small>}</div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
