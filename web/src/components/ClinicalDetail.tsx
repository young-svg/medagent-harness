type Props = { content: string };

export function ClinicalDetail({ content }: Props) {
  return (
    <details className="answer-card clinical-card">
      <summary>
        <span>
          <span className="card-label"><span>03</span> Clinical Detail</span>
          <strong>医学详细分析</strong>
        </span>
        <span className="expand-label"><i aria-hidden="true" /> 展开</span>
      </summary>
      <div className="clinical-content">
        {content || "完成分析后，这里会展示诊断依据、鉴别诊断、治疗原则、检查建议与安全提示。"}
      </div>
    </details>
  );
}
