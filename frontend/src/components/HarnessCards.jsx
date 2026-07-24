export default function HarnessCards({ summary }) {
  if (!summary) return null;
  return (
    <div>
      <div className="segmented-bar">
        {summary.harnesses.map((h) => (
          <div key={h.source} className="segment" style={{ width: `${h.pct * 100}%` }} />
        ))}
      </div>
      <div className="harness-grid">
        {summary.harnesses.map((h) => (
          <div key={h.source} className="harness-card">
            <strong>{h.source}</strong>
            <span>{(h.pct * 100).toFixed(1)}%</span>
            <small>{h.model_count} model{h.model_count === 1 ? "" : "s"}</small>
          </div>
        ))}
        {summary.harnesses.length === 0 && <p>No usage data for this range.</p>}
      </div>
    </div>
  );
}
