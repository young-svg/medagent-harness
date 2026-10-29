import type { HandoffInfo } from "../types";

export function HandoffFlow({ handoffs }: { handoffs: HandoffInfo[] }) {
  if (!handoffs.length) return null;
  return (
    <section className="handoff-section" aria-label="Precomputed dependency handoff">
      <p className="overline">PRECOMPUTED DEPENDENCY EXAMPLE</p>
      <h2>Wait for upstream results, then hand off</h2>
      <p className="handoff-disclosure">静态合成示例 · 无实时执行。上游失败或无有效答案时，运行时会阻断下游。</p>
      {handoffs.map((handoff) => (
        <div className="handoff-flow" key={`${handoff.sourceTaskId}-${handoff.targetTaskId}`}>
          <article className="handoff-step">
            <span className="chip">{handoff.sourceTaskId} · public result</span>
            <h3>1. Upstream Worker</h3>
            <p>交接公开结构化答案；推断保持待验证，不写成已确认患者事实。</p>
            <ul>{handoff.answers.map((item) => (
              <li key={item.requestItemId}><strong>{item.requestItemId}</strong> {item.answer}</li>
            ))}</ul>
          </article>
          <article className="handoff-step handoff-transfer">
            <span className="handoff-arrow" aria-hidden="true">→</span>
            <h3>2. Validated Handoff</h3>
            <p>{handoff.sourceTaskId} → {handoff.targetTaskId}</p>
            <span className="success-chip">Fixture upstream status: {handoff.status}</span>
            <p>先清洗公开答案并检查有效性，再交给下游；不传隐藏推理。</p>
            <small>Illustrative source event</small>
            <code>{handoff.sourceEventId}</code>
          </article>
          <article className="handoff-step">
            <span className="chip">{handoff.targetTaskId} · depends_on [{handoff.sourceTaskId}]</span>
            <h3>3. Dependent Worker</h3>
            <p>等待 {handoff.sourceTaskId} 成功后，使用交接结果形成条件性治疗与随访建议。</p>
            <p>Handoff 与患者事实、Session Memory 分开；本页不调用下游模型。</p>
          </article>
        </div>
      ))}
    </section>
  );
}
