import { useRef, useState } from "react";

import { AnswerSummary } from "./components/AnswerSummary";
import { ClinicalDetail } from "./components/ClinicalDetail";
import { DeveloperPanel } from "./components/DeveloperPanel";
import { PlainLanguageCard } from "./components/PlainLanguageCard";
import { demoInput, demoResponse } from "./demoData";
import { adaptAnalyzeResponse } from "./presentationAdapter";
import type { AnalyzeResponse, PresentationView } from "./types";

const emptyPresentation: PresentationView = {
  directAnswer: "提交病例后，这里会显示核心结论与下一步建议。",
  plainLanguage: "该结果基于病例信息和医学分析生成，详细解释见下方。",
  clinicalDetail: "",
  disclaimer: "本工具仅用于医学信息与病例分析演示，不能替代专业医生的诊断和治疗。",
  evidenceCards: [],
  evidenceState: { status: "NOT_REQUIRED" },
  requestItems: [],
  route: null,
  workers: [],
  retrieval: null,
  traceEvents: [],
};

export default function App() {
  const [description, setDescription] = useState("");
  const [question, setQuestion] = useState("");
  const [presentation, setPresentation] = useState<PresentationView>(emptyPresentation);
  const [developerMode, setDeveloperMode] = useState(false);
  const [clinicalExpanded, setClinicalExpanded] = useState(false);
  const [status, setStatus] = useState<"idle" | "running" | "complete" | "error">("idle");
  const [statusMessage, setStatusMessage] = useState("Ready");
  const [runId, setRunId] = useState<string | null>(null);
  const sessionId = useRef(crypto.randomUUID());

  const canAnalyze = description.trim().length > 0 && question.trim().length > 0 && status !== "running";

  function resetExpandedState() {
    setClinicalExpanded(false);
    setDeveloperMode(false);
  }

  function updateDescription(value: string) {
    setDescription(value);
    if (!value.trim() && !question.trim()) resetExpandedState();
  }

  function updateQuestion(value: string) {
    setQuestion(value);
    if (!value.trim() && !description.trim()) resetExpandedState();
  }

  function loadDemo() {
    resetExpandedState();
    setDescription(demoInput.description);
    setQuestion(demoInput.question);
    setPresentation(adaptAnalyzeResponse(demoResponse));
    setRunId(demoResponse.run_id);
    setStatus("complete");
    setStatusMessage("Demo loaded");
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
  }

  async function analyze() {
    if (!canAnalyze) return;
    resetExpandedState();
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
        <h1>从病例信息到清晰、可读的医学分析</h1>
        <p>输入病例与问题。MedAgent 会组织临床 Agent 协作，并以不同深度呈现同一份专业答案。</p>
      </section>

      <section className="input-card" aria-labelledby="case-input-title">
        <div className="section-heading compact-heading">
          <div>
            <span className="step-number">01</span>
            <div><h2 id="case-input-title">输入病例或医学问题</h2><p>请勿输入可识别个人身份的信息。</p></div>
          </div>
          <button className="text-button" type="button" onClick={loadDemo}>加载示例</button>
        </div>

        <div className="input-grid">
          <label>
            <span>病例信息</span>
            <textarea value={description} onChange={(event) => updateDescription(event.target.value)} placeholder="粘贴病史、体格检查和辅助检查结果…" />
          </label>
          <label>
            <span>希望 MedAgent 回答什么？</span>
            <textarea className="question-input" value={question} onChange={(event) => updateQuestion(event.target.value)} placeholder="例如：请分析诊断依据、鉴别诊断和治疗原则。" />
          </label>
        </div>

        <div className="input-actions">
          <button className="secondary-button" type="button" onClick={resetCase}>新病例</button>
          <button className="primary-button" type="button" onClick={analyze} disabled={!canAnalyze}>
            {status === "running" ? "分析中…" : "Analyze"}<span aria-hidden="true">→</span>
          </button>
        </div>
      </section>

      <section className="results" aria-labelledby="results-title">
        <div className="section-heading">
          <div>
            <span className="step-number">02</span>
            <div><h2 id="results-title">Analysis</h2><p>{runId ? `Run ${runId}` : "结果会按阅读深度分为三层。"}</p></div>
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
            <p className="overline">FOR BUILDERS</p>
            <h2 id="developer-title">Developer Mode</h2>
            <p>查看 RequestSpec、Agent 分工、证据使用与可观察执行过程。</p>
          </div>
          <button className={`toggle ${developerMode ? "toggle-on" : ""}`} type="button" role="switch" aria-checked={developerMode} onClick={() => setDeveloperMode((value) => !value)}>
            <span />{developerMode ? "On" : "Off"}
          </button>
        </div>
        {developerMode && <DeveloperPanel presentation={presentation} />}
      </section>

      <footer><span>MedAgent Harness</span><span>Professional answer remains the benchmark output.</span></footer>
    </main>
  );
}
