import type { RequestItem } from "../types";

type Props = { items: RequestItem[] };

export function RequestSpecView({ items }: Props) {
  return (
    <section className="developer-card">
      <div className="developer-card-title">
        <span>01</span>
        <div><h3>用户需求拆解</h3><p>Request Understanding · RequestSpec</p></div>
      </div>
      {items.length ? (
        <div className="request-list">
          {items.map((item) => (
            <div className="request-item" key={item.id}>
              <strong>{item.id}</strong>
              <p>{item.text}</p>
              <small>{item.required ? "Required" : "Optional"}{item.semanticType ? ` · ${item.semanticType}` : ""}</small>
            </div>
          ))}
        </div>
      ) : <p className="empty-state">Run an analysis to inspect its RequestSpec.</p>}
    </section>
  );
}
