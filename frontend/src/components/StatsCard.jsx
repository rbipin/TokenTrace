export default function StatsCard({ summary }) {
  if (!summary) return <div className="card">Loading…</div>;
  return (
    <div className="card">
      <div className="stats-pills">
        <div className="pill"><span>{summary.total_tokens.toLocaleString()}</span><label>Total tokens</label></div>
        <div className="pill"><span>{summary.session_count}</span><label>Sessions</label></div>
        <div className="pill"><span>{summary.active_days}</span><label>Active days</label></div>
      </div>
      <div className="top-models">
        <h4>Top models</h4>
        <ol>
          {summary.models.slice(0, 5).map((m) => (
            <li key={m.model}>
              {m.model} — {(m.pct * 100).toFixed(1)}%
            </li>
          ))}
        </ol>
      </div>
      {summary.first_date && <footer>Started {summary.first_date}</footer>}
    </div>
  );
}
