import type { PresentationView } from "../types";
import { AgentTrace } from "./AgentTrace";
import { RequestSpecView } from "./RequestSpecView";

type Props = { presentation: PresentationView };

const agentNames: Record<string, string> = {
  diagnostic_agent: "Diagnostic Agent",
  consultation_agent: "Consultation Agent",
  research_agent: "Research Agent",
};

const agentResponsibilities: Record<string, string> = {
  diagnostic_agent: "诊断推理",
  consultation_agent: "治疗管理",
  research_agent: "证据检索与归纳",
};

function memoryFactText(key: string, value: string): string {
  if (key === "attack_frequency") return `${value}偏头痛发作`;
  if (key === "associated_symptoms") return `伴${value}`;
  if (key === "current_focus") return `当前关注：${value}`;
  return value;
}

export function DeveloperPanel({ presentation }: Props) {
  const evidenceStatus = presentation.evidenceState.status;
  return (
    <div className="developer-panel">
      <RequestSpecView items={presentation.requestItems} />

      <section className="developer-card">
        <div className="developer-card-title">
          <span>02</span>
          <div><h3>任务分配与执行角色</h3><p>Agent Plan</p></div>
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
                <p className="worker-responsibility"><span>负责：</span>{agentResponsibilities[worker.name] || "临床分析"}</p>
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
          <div><h3>能力调用状态</h3><p>Tool / Evidence</p></div>
        </div>
        {presentation.tools.length > 0 && (
          <div className="tool-list">
            {presentation.tools.map((tool) => (
              <div className="tool-row" key={tool.name}>
                <code>{tool.name}</code>
                {tool.status && <span className="success-chip">{tool.status}</span>}
              </div>
            ))}
          </div>
        )}
        {evidenceStatus === "NOT_REQUIRED" && (
          <div className="evidence-empty"><span>—</span><div><strong>External Evidence</strong><p>Not required</p></div></div>
        )}
        {evidenceStatus === "REQUIRED_UNAVAILABLE" && (
          <div className="evidence-empty evidence-unavailable">
            <span>!</span>
            <div>
              <strong>External Evidence</strong>
              <p>Required but unavailable</p>
              <small>Reason: {presentation.evidenceState.reason || "retrieval backend unavailable"}</small>
            </div>
          </div>
        )}
        {evidenceStatus === "AVAILABLE" && (
          <div className="evidence-list">
            <div className="evidence-status evidence-available">
              <span>✓</span><div><strong>External Evidence</strong><p>Retrieved</p></div>
            </div>
            {presentation.evidenceCards.map((card) => (
              <article key={card.evidence_id}>
                <strong>{card.title || card.evidence_id}</strong>
                <small>{card.source}{card.section ? ` · ${card.section}` : ""}</small>
                <p>{card.text_preview}</p>
              </article>
            ))}
          </div>
        )}
      </section>

      <AgentTrace events={presentation.traceEvents} />

      {presentation.memory && (
        <section className="developer-card memory-card">
          <div className="developer-card-title">
            <span>05</span>
            <div><h3>Memory Continuity</h3><p>Session Memory · 会话上下文</p></div>
          </div>
          <p className="memory-scope">Session-scoped memory · 仅用于当前会话</p>
          <dl className="memory-facts">
            <div><dt>Session</dt><dd>{presentation.memory.sessionId}</dd></div>
            <div><dt>History injected</dt><dd>{presentation.memory.historyInjectedCount ?? presentation.memory.historyInjected.length} messages</dd></div>
            <div><dt>New session</dt><dd>{presentation.memory.newSessionHistoryCount ?? 0} messages</dd></div>
          </dl>
          <div className="stored-context">
            <h4>Stored context</h4>
            <ul>
              {(presentation.memory.storedFacts || []).map((fact) => (
                <li key={fact.key}>{memoryFactText(fact.key, fact.value)}</li>
              ))}
            </ul>
          </div>
        </section>
      )}
    </div>
  );
}
