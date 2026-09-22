type Props = { answer: string };

function directAnswerParts(answer: string): { conclusion: string; attention: string | null } {
  const normalized = answer.trim().replace(/\r\n/g, "\n");
  const attentionLabel = /(?:^|\n)\s*(?:需要关注|关注|警示)\s*[:：]\s*/m;
  const labeledMatch = attentionLabel.exec(normalized);
  if (labeledMatch) {
    const conclusion = normalized
      .slice(0, labeledMatch.index)
      .replace(/^\s*(?:结论|最可能诊断)\s*[:：]\s*/u, "")
      .trim();
    const attention = normalized.slice(labeledMatch.index + labeledMatch[0].length).trim();
    return { conclusion: conclusion || normalized, attention: attention || null };
  }

  const concernMatch = /[，,；;]\s*(?=(?:但|然而|需要关注|需警惕|值得警惕))/u.exec(normalized);
  if (concernMatch?.index !== undefined) {
    return {
      conclusion: normalized.slice(0, concernMatch.index).trim(),
      attention: normalized.slice(concernMatch.index + concernMatch[0].length).trim(),
    };
  }

  return {
    conclusion: normalized.replace(/^\s*(?:结论|最可能诊断)\s*[:：]\s*/u, "") || "暂未生成核心结论。",
    attention: null,
  };
}

export function AnswerSummary({ answer }: Props) {
  const { conclusion, attention } = directAnswerParts(answer);
  return (
    <article className="answer-card direct-card">
      <div className="card-label"><span>01</span> Direct Answer</div>
      <h3>核心结论</h3>
      <dl className="direct-facts">
        <div>
          <dt>结论</dt>
          <dd>{conclusion}</dd>
        </div>
        {attention && (
          <div className="attention-fact">
            <dt>关注</dt>
            <dd>{attention}</dd>
          </div>
        )}
      </dl>
    </article>
  );
}
