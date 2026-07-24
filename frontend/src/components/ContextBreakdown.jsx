export default function ContextBreakdown({ summary }) {
  if (!summary) return null;
  const categories = [
    { label: "Input", value: summary.input_tokens },
    { label: "Output", value: summary.output_tokens },
    { label: "Cache Read", value: summary.cache_read_tokens },
    { label: "Cache Creation", value: summary.cache_creation_tokens },
    { label: "Reasoning", value: summary.reasoning_tokens },
  ];
  const total = categories.reduce((sum, c) => sum + c.value, 0) || 1;

  return (
    <div className="card">
      <h4>Context breakdown</h4>
      {categories.map((c) => (
        <div key={c.label} className="bar-row">
          <span className="bar-label">{c.label}</span>
          <div className="bar-track">
            <div className="bar-fill" style={{ width: `${(c.value / total) * 100}%` }} />
          </div>
          <span className="bar-value">{c.value.toLocaleString()}</span>
        </div>
      ))}
    </div>
  );
}
