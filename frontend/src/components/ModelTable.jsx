export default function ModelTable({ summary }) {
  if (!summary) return null;
  return (
    <div className="card">
      <h4>Model breakdown</h4>
      <table className="model-table">
        <thead>
          <tr><th>Model</th><th>Tokens</th><th>Share</th></tr>
        </thead>
        <tbody>
          {summary.models.map((m) => (
            <tr key={m.model}>
              <td>{m.model}</td>
              <td>{m.tokens.toLocaleString()}</td>
              <td>
                <div className="bar-track small">
                  <div className="bar-fill" style={{ width: `${m.pct * 100}%` }} />
                </div>
                {(m.pct * 100).toFixed(1)}%
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
