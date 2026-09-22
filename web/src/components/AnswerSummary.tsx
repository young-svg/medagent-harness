type Props = { answer: string };

export function AnswerSummary({ answer }: Props) {
  return (
    <article className="answer-card direct-card">
      <div className="card-label"><span>01</span> Direct Answer</div>
      <h3>结论</h3>
      <div className="answer-copy answer-emphasis">{answer}</div>
    </article>
  );
}
