type Props = { explanation: string };

export function PlainLanguageCard({ explanation }: Props) {
  return (
    <article className="answer-card plain-card">
      <div className="card-label"><span>02</span> Plain Language</div>
      <h3>简单解释</h3>
      <p className="card-subtitle">给患者看的解释</p>
      <div className="answer-copy">{explanation}</div>
    </article>
  );
}
