import { useRef, useState } from "react";

import { AnswerSummary } from "./components/AnswerSummary";
import { ClinicalDetail } from "./components/ClinicalDetail";
import { DeveloperPanel } from "./components/DeveloperPanel";
import { PlainLanguageCard } from "./components/PlainLanguageCard";
import { demoCases } from "./demoData";
import { adaptAnalyzeResponse } from "./presentationAdapter";
import type { AnalyzeResponse, PresentationView } from "./types";

type AppMode = "demo" | "live";

const emptyPresentation: PresentationView = {
  directAnswer: "提交病例后，这里会显示核心结论与下一步建议。",
  plainLanguage: "该结果基于病例信息和医学分析生成，详细解释见下方。",
  clinicalDetail: "",
  disclaimer: "本工具仅用于医学信息与病例分析演示，不能替代专业医生的诊断和治疗。",
  evidenceCards: [],
  evidenceState: { status: "NOT_REQUIRED" },
  tools: [],
  requestItems: [],
  route: null,
  workers: [],
  retrieval: null,
  traceEvents: [],
  memory: null,
};

export default function App() {
  const [description, setDescription] = useState("");
  const [question, setQuestion] = useState("");
  const [presentation, setPresentation] = useState<PresentationView>(emptyPresentation);
  const [developerMode, setDeveloperMode] = useState(false);
  const [clinicalExpanded, setClinicalExpanded] = useState(false);
  const [status, setStatus] = useState<"idle" | "running" | "complete" | "error">("idle");
  const [statusMessage, setStatusMessage] = useState("Ready");
  const [mode, setMode] = useState<AppMode>("live");
  const [runId, setRunId] = useState<string | null>(null);
  const [selectedDemo, setSelectedDemo] = useState("");
  const sessionId = useRef(crypto.randomUUID());

  const canAnalyze = mode === "live" && description.trim().length > 0 && question.trim().length > 0 && status !== "running";

  function resetExpandedState() {
    setClinicalExpanded(false);
    setDeveloperMode(false);
  }

  function updateDescription(value: string) {
    if (mode === "demo") {
      setMode("live");
      setSelectedDemo("");
    }
    setDescription(value);
    if (!value.trim() && !question.trim()) resetExpandedState();
  }

  function updateQuestion(value: string) {
    if (mode === "demo") {
      setMode("live");
      setSelectedDemo("");
    }
    setQuestion(value);
    if (!value.trim() && !description.trim()) resetExpandedState();
  }

  function loadDemo(demoId: string) {
    if (!demoId) {
      resetCase();
      return;
    }
    const demo = demoCases.find((item) => item.id === demoId);
    resetExpandedState();
    setMode("demo");
    setSelectedDemo(demoId);
    const demoPresentation = demo?.response.presentation;
    const fixtureReady = Boolean(
      demo
      && demo.description.trim()
      && demo.question.trim()
      && demoPresentation?.direct_answer?.trim()
      && demoPresentation.plain_language?.trim()
      && demoPresentation.clinical_detail?.trim(),
    );
    if (!demo || !fixtureReady) {
      setDescription("");
      setQuestion("");
      setPresentation(emptyPresentation);
      setRunId(null);
      setStatus("error");
      setStatusMessage("Demo data unavailable");
      return;
    }

    try {
      setDescription(demo.description);
      setQuestion(demo.question);
      setPresentation(adaptAnalyzeResponse(demo.response));
      setRunId(demo.response.run_id);
      setStatus("complete");
      setStatusMessage("Demo loaded · local data");
    } catch {
      setDescription("");
      setQuestion("");
      setPresentation(emptyPresentation);
      setRunId(null);
      setStatus("error");
      setStatusMessage("Demo data unavailable");
    }
  }

  function resetCase() {
    sessionId.current = crypto.randomUUID();
    setDescription("");
    setQuestion("");
    setPresentation(emptyPresentation);
    resetExpandedState();
    setRunId(null);
    setStatus("idle");
    setStatusMessage("Ready");
    setMode("live");
    setSelectedDemo("");
  }

  async function analyze() {
    if (!canAnalyze) return;
    resetExpandedState();
    setMode("live");
    setSelectedDemo("");
    setStatus("running");
    setStatusMessage("Analyzing case…");

    try {
      const response = await fetch("/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ description, question, session_id: sessionId.current }),
      });
      const payload = (await response.json()) as AnalyzeResponse & { detail?: string };
      if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);

      setPresentation(adaptAnalyzeResponse(payload));
      setRunId(payload.run_id);
      setStatus(payload.status === "completed" ? "complete" : "error");
      setStatusMessage(payload.status === "completed" ? "Analysis complete" : `Run ${payload.status}`);
    } catch (error) {
      setStatus("error");
      setStatusMessage(error instanceof Error ? error.message : "Analysis failed");
    }
  }

  return (
    <main className="app-shell">
      <header className="site-header">
        <a className="brand" href="#top" aria-label="MedAgent home">
          <span className="brand-mark" aria-hidden="true">M</span>
          <span><strong>MedAgent</strong><small>AI Clinical Analysis</small></span>
        </a>
        <div className={`run-status status-${status}`} role="status">
          <span className="status-dot" />{statusMessage}
        </div>
      </header>

      <section className="hero" id="top">
        <p className="overline">CLINICAL REASONING, MADE CLEAR</p>
        <h1>从病例到<wbr />清晰、可读的医学分析</h1>
        <p className="hero-subtitle">多 Agent 协作，让答案先给结论，再解释依据。</p>
        <p className="hero-description">输入病例与问题，MedAgent 会按阅读深度呈现同一份专业答案。</p>
      </section>

      <section className="input-card" aria-labelledby="case-input-title">
        <div className="section-heading compact-heading">
          <div>
            <span className="step-number">01</span>
            <div><h2 id="case-input-title">输入病例或医学问题</h2><p>请勿输入可识别个人身份的信息。</p></div>
          </div>
          <label className="demo-picker">
            <span>Demo Cases</span>
            <select
              aria-label="Demo Cases"
              value={selectedDemo}
              onChange={(event) => loadDemo(event.target.value)}
            >
              <option value="">Select a showcase…</option>
              {demoCases.map((demo) => <option value={demo.id} key={demo.id}>{demo.label}</option>)}
            </select>
          </label>
        </div>

        <div className="input-grid">
          <label>
            <span>病例信息</span>
            <small className="field-help">患者信息、病史、检查结果等</small>
            <textarea value={description} onChange={(event) => updateDescription(event.target.value)} placeholder="粘贴病史、体格检查和辅助检查结果…" />
          </label>
          <label>
            <span>重点分析的问题</span>
            <small className="field-help">例如：诊断依据、鉴别诊断、治疗方案等</small>
            <textarea className="question-input" value={question} onChange={(event) => updateQuestion(event.target.value)} placeholder="写下这次希望重点了解的临床问题…" />
          </label>
        </div>

        <div className="input-actions">
          <button className="secondary-button" type="button" onClick={resetCase}>新病例</button>
          <button className="primary-button" type="button" onClick={analyze} disabled={!canAnalyze}>
            {mode === "demo" ? "Demo loaded" : status === "running" ? "分析中…" : "Analyze"}<span aria-hidden="true">→</span>
          </button>
        </div>
      </section>

      <section className="results" aria-labelledby="results-title">
        <div className="section-heading">
          <div>
            <span className="step-number">02</span>
            <div>
              <h2 id="results-title">Analysis</h2>
              <p className="result-context">
                {runId && <span className={`mode-badge mode-${mode}`}>{mode === "demo" ? "Demo fixture" : "Live run"}</span>}
                {runId ? `Run ${runId}` : "结果会按阅读深度分为三层。"}
              </p>
            </div>
          </div>
        </div>
        <AnswerSummary answer={presentation.directAnswer} />
        <PlainLanguageCard explanation={presentation.plainLanguage} />
        <ClinicalDetail
          content={presentation.clinicalDetail}
          expanded={clinicalExpanded}
          onExpandedChange={setClinicalExpanded}
        />
        <p className="disclaimer">{presentation.disclaimer}</p>
      </section>

      <section className="developer-area" aria-labelledby="developer-title">
        <div className="developer-toggle-row">
          <div>
            <p className="overline">EXECUTION DETAILS</p>
            <h2 id="developer-title">查看 Agent 工作过程</h2>
            <p>按需展开 RequestSpec、Planner、Workers、Tools / Evidence 与 Trace。</p>
          </div>
          <button className={`toggle ${developerMode ? "toggle-on" : ""}`} type="button" role="switch" aria-checked={developerMode} onClick={() => setDeveloperMode((value) => !value)}>
            <span />{developerMode ? "收起" : "展开"}
          </button>
        </div>
        {developerMode && <DeveloperPanel presentation={presentation} />}
      </section>

      <footer><span>MedAgent Harness</span><span>Professional answer remains the benchmark output.</span></footer>
    </main>
  );
}
