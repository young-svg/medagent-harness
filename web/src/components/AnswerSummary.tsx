type Props = { answer: string };

function directAnswerParts(answer: string): { conclusion: string; attention: string | null } {
  const normalized = answer.trim().replace(/\r\n/g, "\n");
  const withoutConclusionLabel = normalized.replace(
    /^\s*(?:【\s*)?(?:结论|最可能的?诊断)(?:\s*】)?\s*[:：]?\s*/u,
    "",
  );
  const attentionLabel = /(?:^|\n)\s*(?:需要关注|关注|警示)\s*[:：]\s*/m;
  const labeledMatch = attentionLabel.exec(withoutConclusionLabel);
  if (labeledMatch) {
    const conclusion = withoutConclusionLabel
      .slice(0, labeledMatch.index)
      .trim();
    const attention = withoutConclusionLabel.slice(labeledMatch.index + labeledMatch[0].length).trim();
    return { conclusion: conclusion || withoutConclusionLabel, attention: attention || null };
  }

  const concernMatch = /[，,；;]\s*(?=(?:但|然而|需要关注|需警惕|值得警惕))/u.exec(withoutConclusionLabel);
  if (concernMatch?.index !== undefined) {
    return {
      conclusion: withoutConclusionLabel.slice(0, concernMatch.index).trim(),
      attention: withoutConclusionLabel.slice(concernMatch.index + concernMatch[0].length).trim(),
    };
  }

  return {
    conclusion: withoutConclusionLabel || "暂未生成核心结论。",
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
