type Props = { paragraphs: string[] };

export function PlainLanguageCard({ paragraphs }: Props) {
  return (
    <article className="answer-card plain-card">
      <div className="card-label">Simple Why</div>
      <h3>为什么这样做</h3>
      <p className="card-subtitle">用简单语言解释</p>
      <div className="plain-explanation">
        {paragraphs.map((paragraph, index) => <p key={`${index}-${paragraph}`}>{paragraph}</p>)}
      </div>
    </article>
  );
}
