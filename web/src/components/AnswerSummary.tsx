type Props = { title: string; items: string[] };

export function AnswerSummary({ title, items }: Props) {
  return (
    <article className="answer-card direct-card">
      <div className="card-label"><span>01</span> Direct Action / Conclusion</div>
      <h3>{title}</h3>
      <ul className="direct-items">
        {items.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}
      </ul>
    </article>
  );
}
