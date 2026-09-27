import { useState } from "react";

import { AnswerSummary } from "./components/AnswerSummary";
import { ClinicalDetail } from "./components/ClinicalDetail";
import { DeveloperPanel } from "./components/DeveloperPanel";
import { PlainLanguageCard } from "./components/PlainLanguageCard";
import { demoCases } from "./demoData";
import { adaptAnalyzeResponse } from "./presentationAdapter";

const DEFAULT_DEMO_ID = "demo-02-multi-agent";
const defaultDemo = demoCases.find((item) => item.id === DEFAULT_DEMO_ID) ?? demoCases[0];

if (!defaultDemo) throw new Error("Static demo fixtures are unavailable");

export default function App() {
  const [description, setDescription] = useState(defaultDemo.description);
  const [question, setQuestion] = useState(defaultDemo.question);
  const [presentation, setPresentation] = useState(() => adaptAnalyzeResponse(defaultDemo.response));
  const [developerMode, setDeveloperMode] = useState(true);
  const [clinicalExpanded, setClinicalExpanded] = useState(false);
  const [status, setStatus] = useState<"complete" | "error">("complete");
  const [statusMessage, setStatusMessage] = useState("Static fixture loaded");
  const [runId, setRunId] = useState(defaultDemo.response.run_id);
  const [selectedDemo, setSelectedDemo] = useState(defaultDemo.id);

  function loadDemo(demoId: string) {
    const demo = demoCases.find((item) => item.id === demoId);
    const demoPresentation = demo?.response.presentation;
    const fixtureReady = Boolean(
      demo
      && demo.description.trim()
      && demo.question.trim()
      && demoPresentation?.direct_answer_title?.trim()
      && demoPresentation.direct_answer_items?.length
      && demoPresentation.plain_explanation?.length
      && demoPresentation.clinical_detail?.trim(),
    );

    if (!demo || !fixtureReady) {
      setStatus("error");
      setStatusMessage("Demo data unavailable");
      return;
    }

    try {
      setDescription(demo.description);
      setQuestion(demo.question);
      setPresentation(adaptAnalyzeResponse(demo.response));
      setRunId(demo.response.run_id);
      setSelectedDemo(demoId);
      setClinicalExpanded(false);
      setDeveloperMode(true);
      setStatus("complete");
      setStatusMessage("Static fixture loaded");
    } catch {
      setStatus("error");
      setStatusMessage("Demo data unavailable");
    }
  }

  return (
    <main className="app-shell">
      <header className="site-header">
        <a className="brand" href="#top" aria-label="MedAgent showcase home">
          <span className="brand-mark" aria-hidden="true">M</span>
          <span><strong>MedAgent Harness</strong><small>Agent Engineering Showcase</small></span>
        </a>
        <div className={`run-status status-${status}`} role="status">
          <span className="status-dot" />{statusMessage}
        </div>
      </header>

      <section className="showcase-banner" aria-label="Static showcase status">
        <div className="showcase-badges">
          <strong>STATIC SHOWCASE</strong>
          <strong>LOCAL FIXTURES</strong>
          <strong>NO API CALL</strong>
        </div>
        <p>Static showcase only. Runs on local demo fixtures without backend inference.</p>
      </section>

      <section className="hero" id="top">
        <p className="overline">AGENT ENGINEERING PORTFOLIO SHOWCASE</p>
        <h1>Inspect a multi-agent workflow from request to verified response</h1>
        <p className="hero-subtitle">Planner, specialized agents, tool and evidence state, session memory, and execution trace in one inspectable interface.</p>
        <p className="hero-description">This page demonstrates workflow and UI presentation only. It is not an online medical service.</p>
      </section>

      <section className="input-card" aria-labelledby="case-input-title">
        <div className="section-heading compact-heading">
          <div>
            <span className="step-number">01</span>
            <div>
              <h2 id="case-input-title">Explore a demo fixture</h2>
              <p>Switch between four fixed scenarios. All content is bundled with this static site.</p>
            </div>
          </div>
          <label className="demo-picker">
            <span>Showcase Cases</span>
            <select
              aria-label="Showcase Cases"
              value={selectedDemo}
              onChange={(event) => loadDemo(event.target.value)}
            >
              {demoCases.map((demo) => <option value={demo.id} key={demo.id}>{demo.label}</option>)}
            </select>
          </label>
        </div>

        <div className="input-grid">
          <label>
            <span>Synthetic case context</span>
            <small className="field-help">Read-only local fixture; no patient record is submitted.</small>
            <textarea value={description} readOnly />
          </label>
          <label>
            <span>User request</span>
            <small className="field-help">The request drives the displayed plan, worker ownership, and answer contract.</small>
            <textarea className="question-input" value={question} readOnly />
          </label>
        </div>

        <div className="input-actions">
          <button className="secondary-button" type="button" onClick={() => loadDemo(DEFAULT_DEMO_ID)}>
            Reset to Multi-Agent
          </button>
        </div>
      </section>

      <section className="results" aria-labelledby="results-title">
        <div className="section-heading">
          <div>
            <span className="step-number">02</span>
            <div>
              <h2 id="results-title">Fixture Response</h2>
              <p className="result-context">
                <span className="mode-badge mode-demo">Local fixture</span>
                {`Run ${runId}`}
              </p>
            </div>
          </div>
        </div>
        <AnswerSummary title={presentation.directAnswerTitle} items={presentation.directAnswerItems} sections={presentation.directAnswerSections} />
        <PlainLanguageCard paragraphs={presentation.plainExplanation} />
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
            <h2 id="developer-title">Inspect the Agent workflow</h2>
            <p>RequestSpec, Planner, worker ownership, tools and evidence, trace events, and session memory.</p>
          </div>
          <button className={`toggle ${developerMode ? "toggle-on" : ""}`} type="button" role="switch" aria-checked={developerMode} onClick={() => setDeveloperMode((value) => !value)}>
            <span />{developerMode ? "Collapse" : "Expand"}
          </button>
        </div>
        {developerMode && <DeveloperPanel presentation={presentation} isDemo />}
      </section>

      <footer>
        <span>MedAgent Harness · Static portfolio showcase</span>
        <span>No backend · No API keys · No external inference</span>
      </footer>
    </main>
  );
}
