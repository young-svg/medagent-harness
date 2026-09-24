type Props = {
  title: string;
  items: string[];
  sections: Array<{ requestItemId: string; title: string; items: string[] }>;
};

export function AnswerSummary({ title, items, sections }: Props) {
  return (
    <article className="answer-card direct-card">
      <div className="card-label">Direct Action / Conclusion</div>
      <h3>{title}</h3>
      {sections.length ? sections.map((section) => (
        <section className="direct-section" key={section.requestItemId}>
          <h4>{section.title}</h4>
          <ul className="direct-items">
            {section.items.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}
          </ul>
        </section>
      )) : (
        <ul className="direct-items">
          {items.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}
        </ul>
      )}
    </article>
  );
}
