import { useRef, useState } from "react";

type View = "patient" | "clinical" | "developer";

type EvidenceCard = {
  evidence_id: string;
  title?: string | null;
  source: string;
  section?: string | null;
  score: number | null;
  text_preview: string;
};

type Result = {
  headline: string;
  plain_language_summary: string;
  professional_answer: string;
  evidence_cards: EvidenceCard[];
  execution_summary: Record<string, unknown>;
  disclaimer: string;
};

const initial: Result = {
  headline: "\u7b49\u5f85\u8fd0\u884c",
  plain_language_summary: "\u63d0\u4ea4\u5408\u6210\u75c5\u4f8b\u540e\uff0c\u8fd9\u91cc\u663e\u793a\u539f\u56de\u7b54\u6458\u8981\u3002",
  professional_answer: "\u5c1a\u672a\u6267\u884c\u3002",
  evidence_cards: [],
  execution_summary: { runtime_mode: "not_started", tokens: null },
  disclaimer: "\u4ec5\u4f9b\u533b\u5b66\u4fe1\u606f\u4e0e\u75c5\u4f8b\u5206\u6790\u6f14\u793a\uff0c\u4e0d\u80fd\u66ff\u4ee3\u4e13\u4e1a\u533b\u751f\u8bca\u7597\u3002",
};

export default function App() {
  const [view, setView] = useState<View>("patient");
  const [description, setDescription] = useState(
    "SYNTHETIC EXAMPLE - adult with intermittent fatigue; no emergency symptoms supplied.",
  );
  const [question, setQuestion] = useState(
    "What information is needed for a safe initial assessment?",
  );
  const [result, setResult] = useState<Result>(initial);
  const [status, setStatus] = useState("Ready");
  const sessionId = useRef(crypto.randomUUID());

  function startNewCase() {
    sessionId.current = crypto.randomUUID();
    setResult(initial);
    setStatus("Ready - new case session");
  }

  async function analyze() {
    setStatus("Running harness...");
    try {
      const response = await fetch("/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ description, question, session_id: sessionId.current }),
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail || `HTTP ${response.status}`);
      }
      setResult(payload.presentation);
      const mode = payload.presentation.execution_summary?.runtime_mode || "unknown";
      setStatus(`Run ${payload.run_id} - ${mode}`);
    } catch (error) {
      setStatus(`Execution failed: ${error instanceof Error ? error.message : "unknown error"}`);
    }
  }

  return (
    <main>
      <header>
        <div>
          <p className="eyebrow">CLINICAL-DOMAIN AGENT ENGINEERING</p>
          <h1>MedAgent <span>Harness</span></h1>
        </div>
        <div className="status"><i />{status}</div>
      </header>
      <section className="workspace">
        <aside>
          <label>
            Case description
            <textarea value={description} onChange={(event) => setDescription(event.target.value)} />
          </label>
          <label>
            Question
            <textarea className="short" value={question} onChange={(event) => setQuestion(event.target.value)} />
          </label>
          <button onClick={analyze}>Run analysis <b>{"\u2192"}</b></button>
          <button className="new-case" onClick={startNewCase}>{"\u65b0\u75c5\u4f8b"}</button>
          <p className="privacy">Synthetic examples only. Do not enter identifiable patient data.</p>
        </aside>
        <article>
          <nav>
            {(["patient", "clinical", "developer"] as View[]).map((item) => (
              <button className={view === item ? "active" : ""} onClick={() => setView(item)} key={item}>
                {item} view
              </button>
            ))}
          </nav>
          {view === "patient" && (
            <div className="panel patient">
              <p className="tag">{"\u539f\u56de\u7b54\u6458\u8981"}</p>
              <h2>{result.headline}</h2>
              <p>{result.plain_language_summary}</p>
              {result.evidence_cards.length > 0 && (
                <>
                  <h3>{"\u68c0\u7d22\u5230\u7684\u53c2\u8003\u8d44\u6599"}</h3>
                  <ul>
                    {result.evidence_cards.slice(0, 3).map((card) => (
                      <li key={card.evidence_id}>{card.text_preview}</li>
                    ))}
                  </ul>
                </>
              )}
              <div className="notice">{result.disclaimer}</div>
            </div>
          )}
          {view === "clinical" && (
            <div className="panel">
              <p className="tag">PROFESSIONAL ANSWER</p>
              <pre>{result.professional_answer}</pre>
              <h3>{"\u68c0\u7d22\u5230\u7684\u53c2\u8003\u8d44\u6599"}</h3>
              {result.evidence_cards.length ? (
                result.evidence_cards.map((card) => (
                  <div className="card" key={card.evidence_id}>
                    <b>{card.evidence_id} - {card.title || "Untitled evidence"}</b>
                    <small>
                      {card.source || "Source metadata unavailable"} - {card.section || "Section unavailable"} - {card.score === null ? "Score unavailable" : card.score.toFixed(3)}
                    </small>
                    <p>{card.text_preview}</p>
                  </div>
                ))
              ) : (
                <p className="muted">No admitted evidence. Source metadata unavailable.</p>
              )}
            </div>
          )}
          {view === "developer" && (
            <div className="panel">
              <p className="tag">TRACE V2 / REPLAY</p>
              <h2>Observable execution, not private reasoning</h2>
              <p className="muted">
                Actual event IDs, parent event IDs, model/tool calls, retrieval, checker edits, usage and latency.
              </p>
              <pre className="json">{JSON.stringify(result.execution_summary, null, 2)}</pre>
            </div>
          )}
        </article>
      </section>
    </main>
  );
}
