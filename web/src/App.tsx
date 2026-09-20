import { useState } from "react";

type View = "patient" | "clinical" | "developer";
type EvidenceCard = { evidence_id: string; title?: string; source: string; section?: string; score: number; text_preview: string };
type Result = {
  headline: string;
  plain_language_summary: string;
  professional_answer: string;
  evidence_cards: EvidenceCard[];
  execution_summary: Record<string, unknown>;
  disclaimer: string;
};

const demo: Result = {
  headline: "Offline demonstration",
  plain_language_summary: "This preview shows the presentation layer without inventing clinical conclusions.",
  professional_answer: "Load a synthetic example or connect the FastAPI backend to inspect a complete run.",
  evidence_cards: [],
  execution_summary: { mode: "sample trace", route: { mode: "single", workers: ["diagnostic_agent"] }, tokens: 0 },
  disclaimer: "仅供医学信息与病例分析演示，不能替代专业医生诊疗。",
};

export default function App() {
  const [view, setView] = useState<View>("patient");
  const [description, setDescription] = useState("SYNTHETIC EXAMPLE — adult with intermittent fatigue; no emergency symptoms supplied.");
  const [question, setQuestion] = useState("What information is needed for a safe initial assessment?");
  const [result, setResult] = useState<Result>(demo);
  const [status, setStatus] = useState("Sample trace ready");

  async function analyze() {
    setStatus("Running harness…");
    try {
      const response = await fetch("/api/analyze", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ description, question, session_id: "web-demo" }) });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      setResult(payload.answer);
      setStatus(`Run ${payload.run_id}`);
    } catch {
      setResult(demo);
      setStatus("Backend unavailable — showing bundled sample");
    }
  }

  return <main>
    <header>
      <div><p className="eyebrow">CLINICAL-DOMAIN AGENT ENGINEERING</p><h1>MedAgent <span>Harness</span></h1></div>
      <div className="status"><i />{status}</div>
    </header>
    <section className="workspace">
      <aside>
        <label>Case description<textarea value={description} onChange={event => setDescription(event.target.value)} /></label>
        <label>Question<textarea className="short" value={question} onChange={event => setQuestion(event.target.value)} /></label>
        <button onClick={analyze}>Run analysis <b>→</b></button>
        <p className="privacy">Synthetic examples only. Do not enter identifiable patient data.</p>
      </aside>
      <article>
        <nav>{(["patient", "clinical", "developer"] as View[]).map(item => <button className={view === item ? "active" : ""} onClick={() => setView(item)} key={item}>{item} view</button>)}</nav>
        {view === "patient" && <div className="panel patient">
          <p className="tag">PRELIMINARY SUMMARY</p><h2>{result.headline}</h2><p>{result.plain_language_summary}</p>
          {result.evidence_cards.length > 0 && <><h3>Why this assessment?</h3><ul>{result.evidence_cards.slice(0, 3).map(card => <li key={card.evidence_id}>{card.text_preview}</li>)}</ul></>}
          <div className="notice">{result.disclaimer}</div>
        </div>}
        {view === "clinical" && <div className="panel"><p className="tag">PROFESSIONAL ANSWER</p><pre>{result.professional_answer}</pre><h3>Evidence sources</h3>{result.evidence_cards.length ? result.evidence_cards.map(card => <div className="card" key={card.evidence_id}><b>{card.evidence_id} · {card.title || "Untitled evidence"}</b><small>{card.source || "Source metadata unavailable"} · {card.section || "Section unavailable"} · {card.score.toFixed(3)}</small><p>{card.text_preview}</p></div>) : <p className="muted">No admitted evidence. Source metadata unavailable.</p>}</div>}
        {view === "developer" && <div className="panel"><p className="tag">TRACE / REPLAY</p><h2>Observable execution, not private reasoning</h2><p className="muted">Context, plan, route, tools, retrieval, worker status, checker edits, tokens and latency.</p><pre className="json">{JSON.stringify(result.execution_summary, null, 2)}</pre></div>}
      </article>
    </section>
  </main>;
}

