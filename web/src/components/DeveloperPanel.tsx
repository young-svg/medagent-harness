import type { PresentationView } from "../types";
import { AgentTrace } from "./AgentTrace";
import { RequestSpecView } from "./RequestSpecView";

type Props = { presentation: PresentationView };

const agentNames: Record<string, string> = {
  diagnostic_agent: "Diagnostic Agent",
  consultation_agent: "Consultation Agent",
  research_agent: "Research Agent",
};

export function DeveloperPanel({ presentation }: Props) {
  const hasExternalEvidence = presentation.evidenceCards.length > 0 || Boolean(presentation.retrieval?.retrievedCount);
  return (
    <div className="developer-panel">
      <RequestSpecView items={presentation.requestItems} />

      <section className="developer-card">
        <div className="developer-card-title">
          <span>02</span>
          <div><h3>Agent Plan</h3><p>Route and ownership</p></div>
        </div>
        <div className="route-line">
          <span>Route</span>
          <strong>{presentation.route?.mode || "Not available"}</strong>
        </div>
        <div className="worker-list">
          {presentation.workers.length ? presentation.workers.map((worker) => (
            <div className="worker-card" key={worker.id}>
              <span className="agent-avatar">{(agentNames[worker.name] || worker.name).slice(0, 1)}</span>
              <div>
                <strong>{agentNames[worker.name] || worker.name}</strong>
                <p>{worker.description}</p>
                <div className="chip-row">
                  {worker.requestItemIds.map((id) => <span className="chip" key={id}>{id}</span>)}
                  {worker.status && <span className="success-chip">{worker.status}</span>}
                </div>
              </div>
            </div>
          )) : <p className="empty-state">No plan is available yet.</p>}
        </div>
      </section>

      <section className="developer-card">
        <div className="developer-card-title">
          <span>03</span>
          <div><h3>Tool / Evidence</h3><p>External evidence</p></div>
        </div>
        {!hasExternalEvidence ? (
          <div className="evidence-empty"><span>—</span><div><strong>External Evidence</strong><p>Not required</p></div></div>
        ) : (
          <div className="evidence-list">
            {presentation.evidenceCards.map((card) => (
              <article key={card.evidence_id}>
                <strong>{card.title || card.evidence_id}</strong>
                <small>{card.source}{card.section ? ` · ${card.section}` : ""}</small>
                <p>{card.text_preview}</p>
              </article>
            ))}
            {!presentation.evidenceCards.length && presentation.retrieval && (
              <article>
                <strong>Retrieval completed</strong>
                <small>{presentation.retrieval.collection || "Collection not specified"}</small>
                <p>{presentation.retrieval.retrievedCount} item(s) retrieved; no evidence was admitted to the final answer.</p>
              </article>
            )}
          </div>
        )}
      </section>

      <AgentTrace events={presentation.traceEvents} />
    </div>
  );
}
